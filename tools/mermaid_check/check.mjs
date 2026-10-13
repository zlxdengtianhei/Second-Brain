#!/usr/bin/env node
// Parse (and optionally render) a batch of Mermaid blocks with the exact
// Mermaid build that Obsidian ships, in a headless Chromium browser.
//
// Obsidian 1.13.x bundles mermaid@11.13.0 with one local patch (label width
// rounding) and calls mermaid.initialize with the options in OBSIDIAN_CONFIG.
// This tool loads the npm build of the same version, applies the same patch
// and the same options, so "ok" here means "renders in Obsidian".
//
// Usage:
//   node check.mjs [options] IN.jsonl [OUT.jsonl]
//   node check.mjs [options] < IN.jsonl > OUT.jsonl
//
// Input: one JSON object per line, {"id": <any>, "text": "<mermaid source>"}.
// Output: one JSON object per line, in input order:
//   {"id", "ok", "error"?, "type"?, "direction"?, "nodes"?, "edges"?,
//    "subgraphs"?, "actors"?, "messages"?, "width"?, "height"?,
//    "label_overlaps"?, "render_error"?, "label_text"?}
// label_overlaps counts pairs of edge labels that cover each other.
// A summary line goes to stderr. Exit code 1 if any block failed, 2 on usage
// or setup errors, or when the browser cannot be started or stops responding
// (the message says how many results were written before that).
//
// Options:
//   --browser PATH        Chromium/Chrome executable (or MERMAID_CHECK_BROWSER)
//   --modules DIR         directory whose node_modules holds the dependencies
//                         (default: this tool's directory)
//   --no-render           parse and extract structure only; skip SVG render
//   --label-text          include the rendered label text (for fidelity tests)
//   --svg-dir DIR         write each rendered SVG to DIR/<n>.svg
//   --no-obsidian-patch   use the npm build unmodified
//   --batch N             blocks per browser round trip (default 50)
//   --recycle N           open a fresh page after N blocks (default 1000)

import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

// Verbatim from Obsidian 1.13.7 app.js; the theme font variable is replaced by
// a concrete stack because there is no Obsidian stylesheet in this page.
const OBSIDIAN_CONFIG = {
  startOnLoad: false,
  securityLevel: 'strict',
  themeVariables: { fontFamily: '-apple-system, "PingFang SC", "Noto Sans CJK SC", sans-serif' },
  flowchart: { useMaxWidth: false },
  sequence: { useMaxWidth: false },
  gantt: { useMaxWidth: true },
  journey: { useMaxWidth: true },
  class: { useMaxWidth: true },
  git: { useMaxWidth: false },
  state: { useMaxWidth: true },
  er: { useMaxWidth: false },
  pie: { useMaxWidth: true },
};

// Obsidian's patch to mermaid.min.js: compare label widths after rounding.
const PATCH_FROM = /return (\w)\.width===(\w)&&\(/g;
const PATCH_TO = 'return Math.round($1.width)===Math.round($2)&&(';

const BROWSER_CANDIDATES = [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/usr/bin/google-chrome',
  '/usr/bin/google-chrome-stable',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
];

// Slow machines and busy CI runners: give the browser time to start and to
// render a batch, and start it once more before giving up. Root (a CI
// container) cannot use Chrome's sandbox; a small /dev/shm crashes pages.
const LAUNCH_TIMEOUT_MS = 120000;
const PROTOCOL_TIMEOUT_MS = 600000;
const PAGE_TIMEOUT_MS = 120000;

async function launchBrowser(puppeteer, executablePath) {
  const args = ['--no-first-run', '--no-default-browser-check', '--disable-dev-shm-usage'];
  if (process.getuid && process.getuid() === 0) args.push('--no-sandbox');
  let last = null;
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      return await puppeteer.launch({
        executablePath, headless: true, args, timeout: LAUNCH_TIMEOUT_MS, protocolTimeout: PROTOCOL_TIMEOUT_MS,
      });
    } catch (e) {
      last = e;
    }
  }
  throw new Error(`could not start the browser ${executablePath} (2 attempts): ${(last && last.message) || last}`);
}

function parseArgs(argv) {
  const o = { render: true, labelText: false, patch: true, batch: 50, recycle: 1000, files: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--browser') o.browser = argv[++i];
    else if (a === '--modules') o.modules = argv[++i];
    else if (a === '--no-render') o.render = false;
    else if (a === '--label-text') o.labelText = true;
    else if (a === '--svg-dir') o.svgDir = argv[++i];
    else if (a === '--no-obsidian-patch') o.patch = false;
    else if (a === '--batch') o.batch = Number(argv[++i]);
    else if (a === '--recycle') o.recycle = Number(argv[++i]);
    else if (a === '-h' || a === '--help') o.help = true;
    else if (a.startsWith('--')) throw new Error(`unknown option ${a}`);
    else o.files.push(a);
  }
  return o;
}

function findBrowser(explicit) {
  const c = explicit || process.env.MERMAID_CHECK_BROWSER;
  if (c) {
    if (!fs.existsSync(c)) throw new Error(`browser not found: ${c}`);
    return c;
  }
  const hit = BROWSER_CANDIDATES.find((p) => fs.existsSync(p));
  if (!hit) throw new Error('no Chromium found; pass --browser PATH or set MERMAID_CHECK_BROWSER');
  return hit;
}

async function readBlocks(file) {
  const input = file ? fs.createReadStream(file, 'utf8') : process.stdin;
  const rl = readline.createInterface({ input, crlfDelay: Infinity });
  const blocks = [];
  for await (const line of rl) {
    if (!line.trim()) continue;
    const rec = JSON.parse(line);
    if (typeof rec.text !== 'string') throw new Error(`line ${blocks.length + 1}: "text" must be a string`);
    blocks.push(rec);
  }
  return blocks;
}

// Runs inside the page. Returns one result object per block.
async function checkBatch(items, opts) {
  const out = [];
  for (const { n, text } of items) {
    const r = { ok: true };
    try {
      await mermaid.parse(text);
    } catch (e) {
      r.ok = false;
      r.error = String((e && e.message) || e).slice(0, 500);
      out.push(r);
      continue;
    }
    try {
      const d = await mermaid.mermaidAPI.getDiagramFromText(text);
      r.type = d.type;
      const db = d.db;
      if (db.getVertices) {
        const vs = db.getVertices();
        const arr = vs instanceof Map ? [...vs.values()] : Object.values(vs);
        r.nodes = arr.map((v) => ({ id: v.id, label: v.text ?? v.id, shape: v.type || null }));
        r.edges = db.getEdges().map((e) => ({ from: e.start, to: e.end, label: e.text || '' }));
        r.subgraphs = (db.getSubGraphs ? db.getSubGraphs() : []).length;
        r.direction = db.getDirection ? db.getDirection() : null;
      } else if (db.getActors && db.getMessages) {
        const acts = db.getActors();
        r.actors = acts instanceof Map ? acts.size : Object.keys(acts).length;
        r.messages = db.getMessages().length;
      }
    } catch (e) {
      r.structure_error = String((e && e.message) || e).slice(0, 300);
    }
    if (opts.render) {
      const host = document.getElementById('host');
      try {
        const { svg } = await mermaid.render('m' + n, text);
        host.innerHTML = svg;
        const el = host.querySelector('svg');
        const bb = el.getBoundingClientRect();
        r.width = Math.round(bb.width);
        r.height = Math.round(bb.height);
        // Edge labels that cover each other (more than 10% of the smaller box).
        const labels = [...el.querySelectorAll('g.edgeLabel')]
          .filter((g) => g.textContent.trim())
          .map((g) => g.getBoundingClientRect())
          .filter((b) => b.width > 1 && b.height > 1);
        let overlaps = 0;
        for (let a = 0; a < labels.length; a++) {
          for (let c = a + 1; c < labels.length; c++) {
            const p = labels[a], q = labels[c];
            const w = Math.min(p.right, q.right) - Math.max(p.left, q.left);
            const h = Math.min(p.bottom, q.bottom) - Math.max(p.top, q.top);
            if (w > 0 && h > 0 && w * h > 0.1 * Math.min(p.width * p.height, q.width * q.height)) overlaps++;
          }
        }
        r.label_overlaps = overlaps;
        if (opts.labelText) {
          const clone = el.cloneNode(true);
          clone.querySelectorAll('style').forEach((s) => s.remove());
          r.label_text = clone.textContent;
        }
        if (opts.keepSvg) r.svg = svg;
      } catch (e) {
        r.render_error = String((e && e.message) || e).slice(0, 300);
      }
      host.innerHTML = '';
      document.querySelectorAll('body > [id^="dm"]').forEach((x) => x.remove());
    }
    out.push(r);
  }
  return out;
}

async function main() {
  const o = parseArgs(process.argv.slice(2));
  if (o.help) {
    process.stdout.write(fs.readFileSync(fileURLToPath(import.meta.url), 'utf8').split('\n\n')[0] + '\n');
    return 0;
  }
  const req = createRequire(path.join(path.resolve(o.modules || HERE), 'noop.js'));
  const puppeteer = req('puppeteer-core');
  let lib = fs.readFileSync(req.resolve('mermaid/dist/mermaid.min.js'), 'utf8');
  if (o.patch) {
    const hits = lib.match(PATCH_FROM) || [];
    if (hits.length !== 1) throw new Error(`Obsidian patch expected 1 match, found ${hits.length}`);
    lib = lib.replace(PATCH_FROM, PATCH_TO);
  }
  const blocks = await readBlocks(o.files[0]);
  const sink = o.files[1] ? fs.createWriteStream(o.files[1]) : process.stdout;
  if (o.svgDir) fs.mkdirSync(o.svgDir, { recursive: true });

  const executablePath = findBrowser(o.browser);
  let browser = await launchBrowser(puppeteer, executablePath);
  const t0 = Date.now();
  const tally = { blocks: blocks.length, ok: 0, parse_failed: 0, render_failed: 0, page_crashes: 0 };
  const opts = { render: o.render, labelText: o.labelText, keepSvg: Boolean(o.svgDir) };
  let page = null;
  let used = 0;
  const newPage = async () => {
    if (page) await page.close().catch(() => {});
    page = null;
    page = await browser.newPage();
    page.setDefaultTimeout(PAGE_TIMEOUT_MS);
    await page.setViewport({ width: 1400, height: 900 });
    await page.setContent(`<!doctype html><html><head><meta charset="utf-8">
<style>body{font-family:-apple-system,"PingFang SC","Noto Sans CJK SC",sans-serif;font-size:16px;margin:0;background:#fff}</style>
</head><body><div id="host"></div></body></html>`);
    await page.addScriptTag({ content: lib });
    await page.evaluate((cfg) => mermaid.initialize(cfg), OBSIDIAN_CONFIG);
    await page.evaluate(`window.__checkBatch = ${checkBatch.toString()}`);
    used = 0;
  };
  // A page that cannot be opened means the browser is gone or stuck: start a
  // new one once, then give up with a message that says so.
  const openPage = async () => {
    try {
      await newPage();
    } catch (e) {
      await browser.close().catch(() => {});
      try {
        browser = await launchBrowser(puppeteer, executablePath);
        await newPage();
      } catch (e2) {
        throw new Error(`the browser stopped responding (${(e2 && e2.message) || e2}; first: ${(e && e.message) || e})`);
      }
    }
  };
  // A long-lived page slowly fills up and can crash; recycle it, and on a
  // crash retry on a fresh page, then block by block so one bad block cannot
  // take the rest of the batch with it.
  const run = async (items) => {
    if (!page || used >= o.recycle) await openPage();
    used += items.length;
    return page.evaluate((it, op) => window.__checkBatch(it, op), items, opts);
  };
  const runSafely = async (items) => {
    try {
      return await run(items);
    } catch (e) {
      tally.page_crashes++;
      await openPage();
    }
    try {
      return await run(items);
    } catch (e) {
      tally.page_crashes++;
    }
    const out = [];
    for (const item of items) {
      await openPage();
      try {
        out.push(...(await run([item])));
      } catch (e) {
        tally.page_crashes++;
        out.push({ ok: false, error: `checker page crashed: ${String((e && e.message) || e).slice(0, 200)}` });
      }
    }
    return out;
  };
  let written = 0;
  try {
    for (let i = 0; i < blocks.length; i += o.batch) {
      const items = blocks.slice(i, i + o.batch).map((b, k) => ({ n: i + k, text: b.text }));
      const res = await runSafely(items);
      res.forEach((r, k) => {
        const n = i + k;
        if (r.svg) {
          fs.writeFileSync(path.join(o.svgDir, `${n}.svg`), r.svg);
          delete r.svg;
        }
        if (!r.ok) tally.parse_failed++;
        else if (r.render_error) tally.render_failed++;
        else tally.ok++;
        sink.write(JSON.stringify({ id: blocks[n].id ?? n, ...r }) + '\n');
        written++;
      });
    }
  } catch (e) {
    throw new Error(`${e.message} (results written for ${written} of ${blocks.length} blocks)`);
  } finally {
    await browser.close().catch(() => {});
    if (sink !== process.stdout) await new Promise((res) => sink.end(res));
  }
  tally.seconds = Math.round((Date.now() - t0) / 100) / 10;
  process.stderr.write(JSON.stringify(tally) + '\n');
  return tally.parse_failed || tally.render_failed ? 1 : 0;
}

// Exit explicitly: the browser driver can leave handles open after close().
main().then(
  (code) => process.exit(code),
  (e) => {
    process.stderr.write(`mermaid-check: ${e.message}\n`);
    process.exit(2);
  },
);

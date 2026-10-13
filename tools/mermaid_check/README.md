# mermaid_check

Parse and render Mermaid blocks with the Mermaid build that Obsidian ships, in a headless Chromium. "ok" here means the block renders in Obsidian instead of showing a syntax error.

Obsidian 1.13.x bundles `mermaid@11.13.0` with one local patch (label widths are compared after rounding) and calls `mermaid.initialize` with `securityLevel: "strict"`, `flowchart.useMaxWidth: false` and a few other options. This tool loads the npm build of the same version, applies the same patch (it refuses to run if the patch does not match exactly once) and the same options.

Used by:

- `tests/unit/test_diagrams_mermaid.py`: the V9 regression corpus (every emitted diagram parses, renders and keeps its label text);
- `python -m secondbrain.diagrams audit`: parse results and diagram structure for existing vault diagrams;
- after `prune`: confirming that changed diagrams still parse.

## Install

Needs Node.js 18 or later and a Chrome or Chromium binary.

```sh
cd tools/mermaid_check
npm ci            # or: npm install
```

To keep `node_modules` elsewhere, install there and pass `--modules <dir>` (the directory that contains `node_modules`).

The browser is found from `--browser PATH`, the `MERMAID_CHECK_BROWSER` environment variable, or standard install locations of Google Chrome and Chromium.

## Use

```sh
node check.mjs [options] IN.jsonl [OUT.jsonl]
```

Each input line is `{"id": ..., "text": "<mermaid source>"}`. Each output line has `id`, `ok`, and when relevant `error`, `type`, `direction`, `nodes`, `edges`, `subgraphs`, `actors`, `messages`, `width`, `height`, `render_error`, `label_text`. A one-line summary goes to stderr. Exit code 1 means at least one block failed; 2 means a usage or setup error.

| Option | Effect |
|---|---|
| `--no-render` | parse and extract structure only (faster; no width or height) |
| `--label-text` | include the visible text of the rendered SVG |
| `--svg-dir DIR` | write each rendered SVG to `DIR/<n>.svg` |
| `--no-obsidian-patch` | use the npm build unmodified |
| `--batch N` | blocks per browser round trip (default 50) |
| `--recycle N` | open a fresh page after N blocks (default 1000); a crashed page is reopened and the batch retried |

Speed on a laptop: about 40 blocks per second with rendering.

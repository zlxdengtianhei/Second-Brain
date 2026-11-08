#!/usr/bin/env node
// Read-only TeX parse check for `sb export` (MathJax 3, package `mathjax-full`).
//
// stdin: one JSON object per line, {"tex": "...", "display": true|false}
// stdout: one JSON object per line, {"ok": true} or {"ok": false, "error": "..."}
//
// Undefined macros and parse errors count as failures: the `noundefined` and
// `noerrors` packages (which turn them into red text) are left out. Install
// the package anywhere and point NODE_PATH at its node_modules, e.g.
//   npm install --prefix ~/.cache/sb-mathjax mathjax-full@3
//   NODE_PATH=~/.cache/sb-mathjax/node_modules node tools/mathjax_check.js
'use strict';
const readline = require('readline');
const {mathjax} = require('mathjax-full/js/mathjax.js');
const {TeX} = require('mathjax-full/js/input/tex.js');
const {SVG} = require('mathjax-full/js/output/svg.js');
const {liteAdaptor} = require('mathjax-full/js/adaptors/liteAdaptor.js');
const {RegisterHTMLHandler} = require('mathjax-full/js/handlers/html.js');
const {AllPackages} = require('mathjax-full/js/input/tex/AllPackages.js');

const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);
const packages = AllPackages.filter((p) => !['noundefined', 'noerrors', 'autoload', 'require'].includes(p));
let lastError = null;
const tex = new TeX({packages, formatError: (jax, err) => { lastError = err; throw err; }});
const doc = mathjax.document('', {InputJax: tex, OutputJax: new SVG({fontCache: 'none'})});

const rl = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
rl.on('line', (line) => {
  if (!line.trim()) return;
  let item;
  try {
    item = JSON.parse(line);
  } catch (e) {
    process.stdout.write(JSON.stringify({ok: false, error: 'bad input line'}) + '\n');
    return;
  }
  lastError = null;
  try {
    doc.convert(String(item.tex), {display: !!item.display});
    process.stdout.write(JSON.stringify({ok: true}) + '\n');
  } catch (e) {
    const err = lastError || e;
    process.stdout.write(JSON.stringify({ok: false, error: String(err && err.message || err)}) + '\n');
  }
});

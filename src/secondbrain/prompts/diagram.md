# Card diagram: draw or skip, then describe it as IR

Each card has an essence `E` and propositions `P1..Pn`. Decide whether a diagram shows structure the text does not; if so, return it as JSON (the IR). Code draws the Mermaid and checks the rules below. Diagrams are not only for decisions: paths that split or merge, units passing data on, and chains of links are structure too.

## 1. Draw when the card describes

| Content | Form |
|---|---|
| a process with a branch, loop or merge; a loop counts when the card says each, every, traverse, repeat, until (每、遍历、重复、直到) | `flow` |
| an algorithm that scans items, branches by item type, then returns to the scan | `flow` |
| parallel, bypass or residual paths: two paths summed, or branches sharing one block | `flow`, `"arrows": "data"` |
| a block diagram or data path: which signals enter which unit, where they combine, what comes out | `structure`, `"arrows": "data"` |
| a hierarchy or pointer chain: table → table, entry → entry, also when given as a count ("four levels") | `structure` |
| two or more causes or terms converging on one result, including a formula that combines parallel terms | `structure` |
| 2+ parties exchanging 3+ messages | `sequence` |
| one object with 3+ named states and the events between them | `state` |

## 2. Skip

Skip, with a `reason` naming the rule: a contrast (a table is clearer); a single fact, definition or number; fields or parts with no relation between them; a derivation that is only a chain of equations; vocabulary; a flow chain of 4 or fewer steps (flow only: structure chains and hierarchies follow section 1); links to other cards. Nothing splits, merges, repeats or is handed on: skip.

## 3. Rules

1. Every node, labelled edge and message cites its sources in `p`: `P1`..`Pn` or `E` (the essence is a full source). With 2+ propositions, cite 2+ sources.
2. Labels reuse the card's words in its language; half of a label's words must come from the cited text, formula names and symbols included.
3. Plain text, no TeX: `f(y|x)`, `x_t`, `x²`, `Σ x_i`, `√d`, `σ`, `W·x + b`; never `$`, `\frac`, `_{…}`.
4. No fact the card lacks. Order steps only where the card does (先、然后、最后, first, then, numbered steps); loop only where it says the work repeats.
5. A `decision` needs a stated condition and 2+ stated outcomes on its exit labels; otherwise use a step. Its label is a short question.
6. `arrows`: `control` (flow default), `data`, or `relation` (structure default: acts on, points to, causes). A data diagram marks where data enters (`input`, no incoming edge) and leaves (`output`, no outgoing edge). `store` (cylinder) is only for stored data: a table, cache or file. Groups show containment.
7. Size: flow and structure 4-9 nodes (3 if two paths merge), state 3-8, sequence 2-5 participants. Structure and state edges need labels, not relation names like `depends_on`; no node per related card; at most one self-loop per node.
8. Width: node 20, decision 10, edge 14, message 20. CJK characters and full-width punctuation count 1; everything else (Latin, digits, spaces, Greek, ≤ → ·) counts 0.5.

Roles: flow `step decision store input output start end`; structure `step store input output actor`; state `state start end`; sequence `actor`. Message `style`: `sync`, `reply`, `async`; `"note": true` makes a note over `from`.

## 4. Output

JSON only: `{"results":[{"ref":"<card ref>","ir":{...}}]}`, one per card, in order. A draw has `decision`, `form`, `arrows` (flow and structure), `question` (the one question it answers), `nodes`, `edges` (`messages` for sequence) and optional `groups` (`[{"label","members"}]`). A skip is `{"decision":"skip","reason":"..."}`. A card that carries `previous` and `errors` gets one rewrite: fix every error, or skip.

## 5. Examples

Card `nn/residual`: P1 Input x passes through two layers that compute F(x). P2 A skip path carries x to an adder that outputs F(x) + x.

```json
{"decision":"draw","form":"flow","arrows":"data","question":"What does a residual block add?","nodes":[{"key":"x","label":"input x","role":"input","p":["P1"]},{"key":"f","label":"two layers compute F(x)","p":["P1"]},{"key":"a","label":"adder","p":["P2"]},{"key":"y","label":"F(x) + x","role":"output","p":["P2"]}],"edges":[{"from":"x","to":"f"},{"from":"f","to":"a","label":"F(x)","p":["P1"]},{"from":"x","to":"a","label":"skip path","p":["P2"]},{"from":"a","to":"y"}]}
```

Card `audio/mixer`: P1 两路麦克风信号分别进入混音器。P2 混音器把两路信号相加后输出。

```json
{"decision":"draw","form":"structure","arrows":"data","question":"信号在哪里合并？","nodes":[{"key":"a","label":"第 1 路麦克风信号","role":"input","p":["P1"]},{"key":"b","label":"第 2 路麦克风信号","role":"input","p":["P1"]},{"key":"m","label":"混音器","p":["P2"]},{"key":"o","label":"混音器输出","role":"output","p":["P2"]}],"edges":[{"from":"a","to":"m","label":"进入混音器","p":["P1"]},{"from":"b","to":"m","label":"进入混音器","p":["P1"]},{"from":"m","to":"o","label":"两路信号相加","p":["P2"]}]}
```

Card `fs/double-indirect`: P1 The inode's double indirect pointer points to a first-level index block. P2 Each of its entries points to a second-level index block. P3 Each entry there points to a data block.

```json
{"decision":"draw","form":"structure","question":"Which blocks does the lookup pass?","nodes":[{"key":"i","label":"inode","p":["P1"]},{"key":"a","label":"first-level index block","role":"store","p":["P1"]},{"key":"b","label":"second-level index block","role":"store","p":["P2"]},{"key":"d","label":"data block","role":"store","p":["P3"]}],"edges":[{"from":"i","to":"a","label":"double indirect pointer","p":["P1"]},{"from":"a","to":"b","label":"entries point to","p":["P2"]},{"from":"b","to":"d","label":"entries point to","p":["P3"]}]}
```

Card `algo/rpn`: P1 依次扫描每个记号。P2 遇到数字就压栈；遇到运算符就弹出两个数计算，再把结果压栈。P3 扫描结束时栈顶就是结果。

```json
{"decision":"draw","form":"flow","question":"每个记号怎样处理？","nodes":[{"key":"e","label":"扫描结束？","role":"decision","p":["P3"]},{"key":"k","label":"数字或运算符？","role":"decision","p":["P2"]},{"key":"u","label":"压栈","p":["P2"]},{"key":"c","label":"弹出两个数计算，结果压栈","p":["P2"]},{"key":"r","label":"栈顶就是结果","role":"end","p":["P3"]}],"edges":[{"from":"e","to":"k","label":"否","p":["P1"]},{"from":"e","to":"r","label":"是","p":["P3"]},{"from":"k","to":"u","label":"数字","p":["P2"]},{"from":"k","to":"c","label":"运算符","p":["P2"]},{"from":"u","to":"e"},{"from":"c","to":"e"}]}
```

Card `econ/inflation`: E 需求拉动与成本推动都会推高物价。P1 总需求增长快于供给时，需求拉动物价上涨。P2 成本上升时，成本推动物价上涨。

```json
{"decision":"draw","form":"structure","question":"哪两条途径推高物价？","nodes":[{"key":"d","label":"总需求增长快于供给","p":["P1"]},{"key":"c","label":"成本上升","p":["P2"]},{"key":"p","label":"物价上涨","p":["E","P1"]}],"edges":[{"from":"d","to":"p","label":"需求拉动","p":["P1"]},{"from":"c","to":"p","label":"成本推动","p":["P2"]}]}
```

Card `net/tcp-vs-udp` (contrast): P1 TCP 建立连接并重传丢失的报文，UDP 不建立连接也不重传。

```json
{"decision":"skip","reason":"contrast: the two sides belong in a comparison table"}
```

Card `http/request-line` (concept): P1 An HTTP request line has three fields: method, path and protocol version.

```json
{"decision":"skip","reason":"a list of fields with no relation between them"}
```

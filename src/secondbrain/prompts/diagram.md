# Card diagram: draw or skip, then describe it as IR

Each card has an essence `E` and propositions `P1..Pn`. Start from E: find the relation it states (what splits, merges, repeats, causes or is handed on). Draw only if a diagram shows that relation better than the text, and cite E where the diagram carries it. Return the diagram as JSON (the IR); code draws the Mermaid and checks the rules below. Diagrams are not only for decisions: paths that split or merge, units passing data on, and chains of links are structure too.

## 1. Draw when E's relation is

| Content | Form |
|---|---|
| a process with a branch, loop or merge, or an algorithm that scans items, branches by type and returns to the scan | `flow` |
| parallel, bypass or residual paths: two paths summed, branches sharing one block | `flow`, `"arrows":"data"` |
| a block diagram or data path: which signals enter which unit, where they combine | `structure`, `"arrows":"data"` |
| a hierarchy or pointer chain (table → table, entry → entry), also given as a count ("four levels") | `structure` |
| two or more causes or terms converging on one result | `structure` |
| 2-3 parties exchanging 3+ messages | `sequence` |
| one object with 3+ named states and the events between them | `state` |

## 2. Skip

Skip, with a `reason` naming the rule: a contrast; a single fact, definition, number or formula; a list of parts, fields, options or checks (unordered checks are a list, not a sequence); a derivation that is a chain of equations; vocabulary; a sequence of 4 or fewer steps the text already states; links to other cards.

`old_diagram`: if it `renders`, yours replaces it only when it carries E's relation; a diagram of one side proposition, one formula or one list does not, so skip. If it is `broken` and the card has a loop or several branches, draw.

## 3. Rules

1. Every node, labelled edge and message cites its sources in `p` (`P1`..`Pn`, `E`); cite E, and 2+ sources when the card has 2+ propositions.
2. Labels reuse the card's words in its language. At least half of a label's words must occur in the cited text: a Chinese character pair, an English word, a number and a formula symbol each count as a word; a one-character label counts that character; a label of symbols only must use symbols of the cited text. Plain text, no TeX: `f(y|x)`, `x_t`, `x²`, `Σ x_i`, `√d`, `σ`.
3. No fact the card lacks. Order steps only where the card does (先、然后、最后, first, then, numbered). When it says the work repeats (重复、循环、直到, repeat, loop, until), draw the back edge.
4. A `decision` needs a stated condition and 2+ stated outcomes on its exit labels; an "otherwise" exit counts only if the card states what happens. Parallel options are not a decision. One node fans out to at most 3 end nodes.
5. `arrows`: `control` (flow default), `data`, or `relation` (structure default). A data diagram starts at `input` nodes, each connected only to the nodes that use it, and ends at `output` nodes or a final `store`. `store` (cylinder) is only for stored data such as a table or file. Groups show containment.
6. Size: flow and structure 4-9 nodes (3 if two paths merge), state 3-8, sequence 2-5 participants. It must fit 700 px: at most 3 nodes side by side, sequence 2-3 participants, short labels (node 20, decision 10, edge 14, message 20; CJK and full-width punctuation count 1, everything else 0.5).
7. Structure and state edges need labels, not relation names like `depends_on`; no node per related card; at most one self-loop per node.

Roles: flow `step decision store input output start end`; structure `step store input output actor`; state `state start end`; sequence `actor`. A message is `{"from","to","text","p"}` with `style` `sync`, `reply` or `async`; `"note":true` puts `text` in a note over `from` (and `to`, if it names another participant).

## 4. Output

JSON only: `{"results":[{"ref":"<card ref>","ir":{...}}]}`, one per card, in order. A draw has `decision`, `form`, `arrows` (flow and structure), `question` (the one question it answers), `nodes`, `edges` (`messages` for sequence), optional `groups` (`[{"label","members"}]`). A skip is `{"decision":"skip","reason":"..."}`. A card with `previous` and `errors` gets one rewrite: fix every error, or skip.

## 5. Examples

Card `nn/residual`: E A residual block adds its input to its layers' output. P1 Input x passes through two layers that compute F(x). P2 A skip path carries x to an adder that outputs F(x) + x.

```json
{"decision":"draw","form":"flow","arrows":"data","question":"What does a residual block add?","nodes":[{"key":"x","label":"input x","role":"input","p":["P1"]},{"key":"f","label":"two layers compute F(x)","p":["P1"]},{"key":"a","label":"adder","p":["P2","E"]},{"key":"y","label":"F(x) + x","role":"output","p":["P2"]}],"edges":[{"from":"x","to":"f"},{"from":"f","to":"a","label":"F(x)","p":["P1"]},{"from":"x","to":"a","label":"skip path","p":["P2"]},{"from":"a","to":"y"}]}
```

Card `fs/double-indirect`: E A double indirect pointer reaches data through two levels of index blocks. P1 The inode's double indirect pointer points to a first-level index block. P2 Each of its entries points to a second-level index block. P3 Each entry there points to a data block.

```json
{"decision":"draw","form":"structure","question":"Which blocks does the lookup pass?","nodes":[{"key":"i","label":"inode","p":["P1"]},{"key":"a","label":"first-level index block","role":"store","p":["P1","E"]},{"key":"b","label":"second-level index block","role":"store","p":["P2","E"]},{"key":"d","label":"data block","role":"store","p":["P3"]}],"edges":[{"from":"i","to":"a","label":"double indirect pointer","p":["P1"]},{"from":"a","to":"b","label":"entries point to","p":["P2"]},{"from":"b","to":"d","label":"entries point to","p":["P3"]}]}
```

Card `algo/rpn`: E 逆波兰表达式用栈求值，扫描到结束为止。P1 依次扫描每个记号。P2 遇到数字就压栈；遇到运算符就弹出两个数计算，再把结果压栈。P3 扫描结束时栈顶就是结果。

```json
{"decision":"draw","form":"flow","question":"每个记号怎样处理？","nodes":[{"key":"e","label":"扫描结束？","role":"decision","p":["P3","E"]},{"key":"k","label":"数字或运算符？","role":"decision","p":["P2"]},{"key":"u","label":"压栈","p":["P2"]},{"key":"c","label":"弹出两个数计算，结果压栈","p":["P2"]},{"key":"r","label":"栈顶就是结果","role":"end","p":["P3"]}],"edges":[{"from":"e","to":"k","label":"否","p":["P1"]},{"from":"e","to":"r","label":"是","p":["P3"]},{"from":"k","to":"u","label":"数字","p":["P2"]},{"from":"k","to":"c","label":"运算符","p":["P2"]},{"from":"u","to":"e"},{"from":"c","to":"e"}]}
```

Card `econ/inflation`: E 需求拉动与成本推动都会推高物价。P1 总需求增长快于供给时，需求拉动物价上涨。P2 成本上升时，成本推动物价上涨。

```json
{"decision":"draw","form":"structure","question":"哪两条途径推高物价？","nodes":[{"key":"d","label":"总需求增长快于供给","p":["P1"]},{"key":"c","label":"成本上升","p":["P2"]},{"key":"p","label":"物价上涨","p":["E","P1"]}],"edges":[{"from":"d","to":"p","label":"需求拉动","p":["P1"]},{"from":"c","to":"p","label":"成本推动","p":["P2"]}]}
```

Card `net/tcp-vs-udp` (contrast): P1 TCP 建立连接并重传丢失的报文，UDP 不建立连接也不重传。

```json
{"decision":"skip","reason":"contrast: the two sides belong in a comparison table"}
```

Card `http/request-line` (concept), `old_diagram: renders`: E An HTTP request line names what to do with which resource. P1 It has three fields: method, path and protocol version.

```json
{"decision":"skip","reason":"a list of fields; a diagram would not carry E's relation better than the text"}
```

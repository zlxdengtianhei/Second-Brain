# Card diagram: draw or skip, then describe it as IR

Each card has an essence `E` and propositions `P1..Pn`. Start from E: find the relation it states (what splits, merges, repeats, causes or is handed on) and draw only if a diagram shows it better than the text. Code draws the Mermaid and checks these rules.

## 1. Draw when E's relation is

- `flow`: a process with a decision, a loop, or paths that split and merge again; with `"arrows":"data"`, parallel, bypass or residual paths that merge
- `structure`: a hierarchy; a chain of 3+ links citing 3+ different sources (a pointer chain, "four levels"); causes or terms converging on one result; with `"arrows":"data"`, a block diagram or data path (which signals enter which unit)
- `sequence`: 2-3 parties exchanging 3+ messages, even when the text lists them in order
- `state`: one object with 3+ named states and 3+ labelled transitions

## 2. Skip

Skip (`reason`: the rule) for: a contrast, unless E names one result both sides lead to (draw them merging); a single fact, definition, number or formula; a chain of equations; vocabulary; links to other cards; a list of parts, fields, options or checks (unordered checks are a list, and so is a decision whose 3+ exits all end at once); a chain of steps with no decision, loop or merge, at any length (paths that split and never meet are a list too); steps whose order the card does not state.
An `old_diagram` that `renders` is replaced only by a diagram of E's relation citing E and 2+ propositions (all, if fewer). If it is `broken` and the card has a loop or several branches, draw. On skip, code handles the old diagram.

## 3. Rules

1. Every node, labelled edge and message cites its own sources in `p` (`E`, `P1`..`Pn`). Over the whole diagram: cite E, and 2+ different sources if the card has 2+ propositions.
2. Labels use the card's words in its language: half of a label's words occur in the text it cites. A word is an overlapping pair of CJK characters (扫描结束: 扫描, 描结, 结束), an English word, a number or a formula symbol; a one-character label counts that character; a symbols-only label uses cited symbols. Exempt, still cited: 是 否 开始 结束 成功 失败 yes no start end success failure true false. Plain text, no TeX: `x_t`, `x²`, `Σ x_i`.
3. No fact or step order the card lacks. When text a flow cites says the work repeats (重复 循环 反复 迭代 直到 每轮 逐个 逐位 repeat loop until iterate), draw the back edge; not when negated (不会重复), meaning duplicates (重复项) or naming a thing (循环队列, human-in-the-loop).
4. A `decision` needs a stated condition and 2+ stated outcomes on its exit labels; an "otherwise" exit counts only if the card states its outcome. Parallel options are not a decision. A node fans out to at most 3 leaf nodes (no outgoing edge).
5. With `"arrows":"data"` the diagram starts at `input` nodes and ends at `output` nodes or a final `store`. Any `input` has outgoing edges only, to nodes whose cited text mentions it; any `output` has incoming edges only. `store` is data kept for later (table, file, buffer, accumulator); a value passed straight on is a step.
6. Size: flow and structure 4-9 nodes (3 if two paths merge), structure 3+ links unless paths merge; state 3-8 states; sequence 2-3 participants. Label width at most 20 (decision 10, edge 14), counting CJK and full-width punctuation 1, other characters 0.5.
7. Code measures the drawn width: at most 700 px (state diagrams scale to fit), with no edge labels overlapping. This fits: at most 3 nodes side by side, labels at most 7 wide when 3 are; at most 3 labelled edges side by side; sequence: names at most 8, notes and self-messages 9 wide, and with 3 participants messages 9 wide.
8. Structure and state edges need labels in the card's words, not relation names; no node per related card; one self-loop per node at most.

## 4. Output

JSON only: `{"results":[{"ref":"<card ref>","ir":{...}}]}`, one per card, in order. Skip: `{"decision":"skip","reason":"..."}`. Draw: `decision`, `form`, `arrows` (flow and structure: `control`, flow default; `data`; `relation`, structure default), `question` (what it answers), `nodes`, `edges` and, in flow and structure, optional containment `groups` (`[{"label","members"}]`). Roles: flow `step decision store input output start end`; structure `step store input output actor`; state `state start end`; sequence `actor`. A sequence has `messages`, not `edges`: `{"from","to","text","p"}` with `style` `sync`, `reply` or `async`; `"note":true` makes `text` a note over `from` (and `to`, if another participant). A card with `previous` and `errors` gets one rewrite: fix every error, or skip.

## 5. Examples

Card `nn/residual`: E A residual block adds its input to its layers' output. P1 Input x passes through two layers that compute F(x). P2 A skip path carries x to an adder that outputs F(x) + x.

```json
{"decision":"draw","form":"flow","arrows":"data","question":"What does a residual block add?","nodes":[{"key":"x","label":"input x","role":"input","p":["P1"]},{"key":"f","label":"two layers compute F(x)","p":["P1"]},{"key":"a","label":"adder","p":["P2","E"]},{"key":"y","label":"F(x) + x","role":"output","p":["P2"]}],"edges":[{"from":"x","to":"f"},{"from":"f","to":"a","label":"F(x)","p":["P1"]},{"from":"x","to":"a","label":"skip path","p":["P2"]},{"from":"a","to":"y"}]}
```

Card `algo/rpn`: E 逆波兰表达式用栈求值，扫描到结束为止。P1 逐个扫描记号。P2 遇到数字就压栈；遇到运算符就弹出两个数计算，再把结果压栈。P3 扫描结束时栈顶就是结果。

```json
{"decision":"draw","form":"flow","question":"每个记号怎样处理？","nodes":[{"key":"e","label":"扫描结束？","role":"decision","p":["P3","E"]},{"key":"k","label":"数字或运算符？","role":"decision","p":["P2"]},{"key":"u","label":"压栈","p":["P2"]},{"key":"c","label":"弹出两个数计算，结果压栈","p":["P2"]},{"key":"r","label":"栈顶就是结果","role":"end","p":["P3"]}],"edges":[{"from":"e","to":"k","label":"否","p":["P1"]},{"from":"e","to":"r","label":"是","p":["P3"]},{"from":"k","to":"u","label":"数字","p":["P2"]},{"from":"k","to":"c","label":"运算符","p":["P2"]},{"from":"u","to":"e"},{"from":"c","to":"e"}]}
```

Card `econ/inflation`: E 需求拉动与成本推动都会推高物价。P1 总需求增长快于供给时，需求拉动物价上涨。P2 成本上升时，成本推动物价上涨。

```json
{"decision":"draw","form":"structure","question":"哪两条途径推高物价？","nodes":[{"key":"d","label":"总需求增长快于供给","p":["P1"]},{"key":"c","label":"成本上升","p":["P2"]},{"key":"p","label":"物价上涨","p":["E","P1"]}],"edges":[{"from":"d","to":"p","label":"需求拉动","p":["P1"]},{"from":"c","to":"p","label":"成本推动","p":["P2"]}]}
```

Card `rag/router`: E 路由器按问题类型选检索方式。P1 事实问题用关键词检索，语义问题用向量检索，多跳问题用图检索。

```json
{"decision":"skip","reason":"3 exits that all end at once: a list"}
```

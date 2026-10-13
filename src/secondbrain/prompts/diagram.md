# Card diagram: draw or skip, then describe it as IR

Each card has an essence `E` and propositions `P1..Pn`. Start from E: find the relation it states (what splits, merges, repeats, causes or is handed on) and draw only if a diagram shows it better than the text. Diagrams are not only for decisions: split or merged paths, units passing data on and chains of links are structure too. Code draws the Mermaid and checks these rules.

## 1. Draw when E's relation is

- `flow`: a process with a branch, loop or merge, such as a scan that branches by item type and returns to the scan; with `"arrows":"data"`, parallel, bypass or residual paths
- `structure`: a hierarchy, or a pointer chain citing 3+ sources, also given as a count ("four levels"); causes converging on one result; with `"arrows":"data"`, a block diagram or data path (which signals enter which unit)
- `sequence`: 2-3 parties exchanging 3+ messages
- `state`: one object with 3+ named states and the events between them

## 2. Skip

Skip (`reason`: the rule) for a contrast; a single fact, definition, number or formula; a list of parts, fields, options or checks (unordered checks are a list); a chain of equations; vocabulary; 4 or fewer steps the text already states; links to other cards. An `old_diagram` that `renders` is replaced only by a diagram of E's relation, not of a side proposition, formula or list. If it is `broken` and the card has a loop or several branches, draw.

## 3. Rules

1. Every node, labelled edge and message cites its sources in `p` (`E`, `P1`..`Pn`): cite E, and 2+ sources if the card has 2+ propositions.
2. Labels use the card's words in its language: half of a label's words occur in the cited text (a word is a CJK character pair, an English word, a number or a formula symbol; a one-character label counts that character; a symbols-only label uses cited symbols). Plain text, no TeX: `x_t`, `x²`, `Σ x_i`, `√d`.
3. No fact or step order the card lacks. When it says the work repeats (重复、循环、直到, repeat, loop, until), draw the back edge.
4. A `decision` needs a stated condition and 2+ stated outcomes on its exit labels; an "otherwise" exit counts only if the card states its outcome. Parallel options are not a decision. A node fans out to at most 3 leaf nodes.
5. A data diagram starts at `input` nodes, each connected only to the nodes that use it, and ends at `output` nodes or a final `store`. `store` is only stored data, such as a table or file.
6. Size: flow and structure 4-9 nodes (3 if two paths merge), state 3-8, sequence 2-3 participants. It must fit 700 px: at most 3 nodes side by side; label width at most 20 (decision 10, edge 14), counting CJK and full-width punctuation 1, other characters 0.5.
7. Structure and state edges need labels in the card's words, not relation names; no node per related card; one self-loop per node at most.

## 4. Output

JSON only: `{"results":[{"ref":"<card ref>","ir":{...}}]}`, one per card, in order. Skip: `{"decision":"skip","reason":"..."}`. Draw: `decision`, `form`, `arrows` (flow and structure: `control`, flow default; `data`; `relation`, structure default), `question` (the one question it answers), `nodes`, `edges` and optional containment `groups` (`[{"label","members"}]`). Roles: flow `step decision store input output start end`; structure `step store input output actor`; state `state start end`; sequence `actor`. A sequence has `messages`, not `edges`: `{"from","to","text","p"}` with `style` `sync`, `reply` or `async`; `"note":true` makes `text` a note over `from` (and `to`, if another participant). A card with `previous` and `errors` gets one rewrite: fix every error, or skip.

## 5. Examples

Card `nn/residual`: E A residual block adds its input to its layers' output. P1 Input x passes through two layers that compute F(x). P2 A skip path carries x to an adder that outputs F(x) + x.

```json
{"decision":"draw","form":"flow","arrows":"data","question":"What does a residual block add?","nodes":[{"key":"x","label":"input x","role":"input","p":["P1"]},{"key":"f","label":"two layers compute F(x)","p":["P1"]},{"key":"a","label":"adder","p":["P2","E"]},{"key":"y","label":"F(x) + x","role":"output","p":["P2"]}],"edges":[{"from":"x","to":"f"},{"from":"f","to":"a","label":"F(x)","p":["P1"]},{"from":"x","to":"a","label":"skip path","p":["P2"]},{"from":"a","to":"y"}]}
```

Card `algo/rpn`: E 逆波兰表达式用栈求值，扫描到结束为止。P1 依次扫描每个记号。P2 遇到数字就压栈；遇到运算符就弹出两个数计算，再把结果压栈。P3 扫描结束时栈顶就是结果。

```json
{"decision":"draw","form":"flow","question":"每个记号怎样处理？","nodes":[{"key":"e","label":"扫描结束？","role":"decision","p":["P3","E"]},{"key":"k","label":"数字或运算符？","role":"decision","p":["P2"]},{"key":"u","label":"压栈","p":["P2"]},{"key":"c","label":"弹出两个数计算，结果压栈","p":["P2"]},{"key":"r","label":"栈顶就是结果","role":"end","p":["P3"]}],"edges":[{"from":"e","to":"k","label":"否","p":["P1"]},{"from":"e","to":"r","label":"是","p":["P3"]},{"from":"k","to":"u","label":"数字","p":["P2"]},{"from":"k","to":"c","label":"运算符","p":["P2"]},{"from":"u","to":"e"},{"from":"c","to":"e"}]}
```

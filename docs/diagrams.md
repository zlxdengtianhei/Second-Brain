# Card diagrams

[中文](diagrams.zh-CN.md)

A card gets a diagram only when the picture shows something the text does not: a branch, a loop, two paths that merge, parts that pass data to each other, a chain of pointers. The model decides whether to draw and describes the diagram as data (the IR). Code checks the IR and writes the Mermaid. The model never writes Mermaid, so a diagram cannot carry a claim the card does not make, and its syntax is always valid.

## The step in the ingest chain

The step runs after review, on the accepted cards of a source, before the vault is written.

1. **Pregate.** Cards of kind `contrast`, `fact` and `derivation` are skipped by rule, without a model call: a table, a sentence or an aligned formula shows them better. A card without propositions is skipped too.
2. **Request.** The accepted cards are grouped in tens before the kind rules apply, so a request can hold fewer than ten cards. The cards that remain go to the producer model with the instructions in [`src/secondbrain/prompts/diagram.md`](../src/secondbrain/prompts/diagram.md). Each card is sent as its title, domain, essence `E`, numbered propositions `P1`, `P2`, ..., kind and the titles of related cards.
3. **IR.** For each card the model returns `{"decision": "skip", "reason": ...}` or a draw: a form, the question the diagram answers, and nodes with edges (or participants with messages). Every node, labelled edge and message cites the parts of the card it comes from in `p`, for example `["E", "P2"]`.
4. **Check.** Code validates the IR (see [Checks](#checks)).
5. **Draw or rewrite.** A valid IR becomes a Mermaid block. An invalid one goes back to the model once, with the list of errors. If the rewrite is still invalid, the card is written without a diagram, and the reason is recorded in the job.

In host mode, the request is a hand-off with step `diagram` (slot `dg01` for the first batch of ten, `dg01.r1` for its rewrite). The result is a pure function of the stored cards and the stored answers, so the same Mermaid comes out when the step is repeated. `[cards] diagrams = false` turns the step off.

In the ingest chain the width check uses an estimate (V14). The checks that need a browser (V9 and V15, and the measured width) run only in the batch tools, with `sb diagrams apply --checker`.

### The IR

| Form | Use it for | Node roles |
|---|---|---|
| `flow` | a process with a branch, loop or merge | `step`, `decision`, `store`, `input`, `output`, `start`, `end` |
| `structure` | parts and how they connect: a block diagram, a hierarchy, a pointer chain, causes that converge | `step`, `store`, `input`, `output`, `actor` |
| `state` | one object with three or more named states | `state`, `start`, `end` |
| `sequence` | two or three parties that exchange messages | `actor` |

`arrows` (flow and structure only) says what an arrow means: `control` (the default for flow), `data`, or `relation` (the default for structure). A data diagram must start where data enters (role `input`) and end where it leaves (role `output`) or at a final store. `sb diagrams schema` prints the full JSON Schema.

## Checks

| Code | Check | Where it runs |
|---|---|---|
| V1 | shape: field types, a known form, roles allowed in that form, unique node keys, edges between known nodes, no node that loops on itself in a flow and at most one self-loop per node in the other forms, a node in at most one group, groups only in flow and structure, messages only in a sequence and edges only in the other forms | every IR |
| V2 | size: flow and structure 4 to 9 nodes (3 when two paths merge), state 3 to 8 states, sequence 2 to 3 participants (a fourth does not fit the note width) | every IR |
| V3 | every node, labelled edge and message cites parts the card has (`E`, `P1`..`Pn`) | every IR |
| V4 | at least half of each label's words are found in the cited text (formula names and symbols count; `yes`, `no`, `start`, `end` and a few others are exempt) | every IR |
| V5 | the diagram adds structure: a branch, merge or loop in a flow; a merge, loop or three or more links in a structure (a plain chain must cite three or more parts); three or more transitions in a state; three or more messages in a sequence; and a flow whose cited text says the work repeats draws the back edge, unless the model quotes those words in `no_repeat` (see [loop words](#loop-words) below) | every IR |
| V6 | label widths: node labels at most 20 units, decision labels 10, edge labels 14, messages 20 (a character from U+2E80 up, such as CJK or full-width punctuation, counts 1; other characters 0.5) | every IR |
| V7 | structure, state and sequence links are labelled; edges leaving a decision state their condition; a decision has two or more exits | every IR |
| V8 | a card with two or more propositions gets a diagram that cites two or more parts | every IR |
| V9 | the generated Mermaid renders in the Mermaid build Obsidian ships | regression corpus in the tests; `apply --checker` |
| V10 | labels are plain text, not TeX | every IR |
| V11 | a data diagram starts at `input` nodes and ends at `output` nodes or a final store; in any flow or structure, an `input` has outgoing edges only, into nodes whose cited text mentions it, and an `output` has incoming edges only | every IR |
| V12 | the diagram cites `E`: it carries the main relation the essence states. When the card's old diagram renders (batch tools), also when it renders too wide, the new one must also cite two propositions, or all of them if the card has fewer | every IR |
| V13 | no node fans out into more than three leaves, and no decision whose three or more exits are all leaves (that is a list) | every IR |
| V14 | rendered width at most 700 px for flow, structure and sequence (Obsidian scales state diagrams to fit): estimated from the layout, or measured instead with `apply --checker` | every IR of those forms (estimate); `apply --checker` (measured) |
| V15 | edge labels do not lie on top of each other after layout | `apply --checker` |

Warnings do not block a diagram. W0 reports an unknown key that was ignored or the retired role `data`, W1 a node that names a related card (card-to-card links belong in the relations, not in the diagram), W2 an edge labelled with a relation type such as `depends_on`, and W3 a `no_repeat` item that excuses nothing (see [loop words](#loop-words)).

The thresholds are defaults chosen from the distribution of the diagrams in the author's vault and a 50-card pilot. They are not calibrated against a larger study.

### Loop words

V5 looks for words that say the work repeats, such as repeat, loop, until, iterate, 重复, 循环, 直到, 不断 and 轮询. Some uses do not count: a negation ("does not repeat", 不必重复), a repeat that is avoided or a failure (避免重复, 死循环, 陷入循环), a duplicate or repetition as a noun (重复项, 资源重复, 重复ACK), a word inside a list, a trend (不断衰减), product iteration (持续迭代, 迭代空间), and names (循环队列, "circular queue", human-in-the-loop). The error asks for the back edge only if the text describes this work repeating. Otherwise the model adds an item to `no_repeat`: the source (`E` or `P2`, for example), the words around the loop word copied from that source (at most 40 characters), and why they do not mean a repeat. V5 then ignores those words in that source; other loop words in the same source still count. An item that names a source the card does not have, is not copied from it, is longer than 40 characters or has no loop word is ignored, with warning W3. A flow never draws a repeat as a node that loops on itself (V1): the back edge goes from the step where one round ends to the step that repeats.

## What a generated block looks like

This block is from the card "Bloom filter as a cheap first check and the rare-item false positive problem" in the [example vault](../examples/vault/):

```text
%% diagram v1 props=E,P1,P2,P3,P5 sha=98410946
flowchart TB
  n1(["check item with Bloom filter"])
  n2{"definite no?"}
  n3(["skip the slow lookup"])
  n4(["slow lookup settles the answer"])
  n1 --> n2
  n2 -->|"definite no"| n3
  n2 -->|"probably yes"| n4
```

The first line records which parts of the card the diagram cites and the first 8 hex digits of a SHA-256 over the essence and all propositions. If the card text changes later, the hash no longer matches, and the diagram counts as stale. The generator also numbers the nodes itself (`n1`, `n2`, ...), escapes every character that Mermaid reads as syntax, always lays flowcharts out top to bottom (also inside every group, since Mermaid lays out a group with no edge to the outside left to right unless told otherwise), and writes no colours or styles, because Obsidian inverts diagrams in dark themes.

## Diagrams that are already in a vault

`sb diagrams <command>` (the same as `python -m secondbrain.diagrams <command>`) runs batch tools on an existing vault. They are for a vault that already has many diagrams, made by hand or by an earlier tool. Nothing in the vault changes unless a command gets `--write`. Every write is guarded by the file hash recorded at audit time; a card edited since then is reported as a conflict and left alone.

| Command | Does |
|---|---|
| `schema` | prints the IR JSON Schema |
| `audit-extract VAULT OUT` | reads every card (read-only) into `cards.jsonl`, and every Mermaid block into `blocks.jsonl` (`--language` picks the card headings) |
| `audit-repair OUT --checker tools/mermaid_check/check.mjs` | rewrites the old flowcharts that fail to parse: it quotes every label (a `"` inside becomes `#quot;`), renames node ids that are Mermaid keywords or symbols (a renamed id without a label keeps the old id as its label), turns `A -- text --> B` into a label between pipes and puts one statement per line. It never changes what a label says. It renders the rewrites and writes `repairs.jsonl`, which is empty when nothing could be repaired |
| `audit-tier OUT` | reads `cards.jsonl`, the `tools/mermaid_check` results in `checks.jsonl` and `repairs.jsonl` (a repaired diagram that renders counts as one that renders), and sorts every diagram into a tier: `keep`, `delete`, `review` or `redraw` (`--delete-domain` puts a whole domain into `delete`). An upright flowchart (direction not `LR` or `RL`) that renders more than 730 px wide (700 px and a 30 px tolerance) goes to `redraw` with the rule `wide`, because turning it cannot make it narrower. One at most 30 px over stays in the tier its other rules give. New diagrams must still fit 700 px (V14). It writes `tiers.json`, which also holds each old flowchart's labels and edges for the redraw rule of `writeback`. `pool`, `prune` and `writeback` refuse a `tiers.json` made without `repairs.jsonl` while some old diagrams do not parse, and `writeback` refuses one without the flowchart graphs: run `audit-repair`, then `audit-tier` again |
| `pool OUT` | lists the cards to judge again: tiers `review` and `redraw`, plus the `delete` cards that `prune` leaves for review (by default mechanism cards with three or more propositions). `--refs FILE` limits it to the card refs in a file. With `--delete-tier` it lists the other `delete` cards instead, the ones `prune` handles, so that their old diagrams can be audited first; give it another file with `--out` |
| `old-audit-packets POOL OUT` | builds requests that ask, for each card whose old diagram would stay or could be removed, whether that old diagram restates the text, misleads, or is useful |
| `old-audit-apply ANSWERS... --out OUT` | reads those answers (`old-audit-NNN.answer.json` next to `old-audit-NNN.json`) into `old_audit_outcomes.jsonl`; only `remove` with `restates` or `misleading` removes an old diagram, every other answer keeps it |
| `prune VAULT OUT` | handles the `delete` and `keep` tiers. A `delete` diagram is removed only when its audit (`--old-audit FILE`) says `restates` or `misleading`. Without a verdict it stays and is listed as `pending` (`no audit` in `why`); a diagram the audit keeps is treated like a `keep` one. Only two kinds are removed without an audit: diagrams in a domain deleted with `--delete-domain`, and diagrams that still do not parse after `audit-repair`. Of the kept diagrams, it turns wide left-to-right ones top to bottom and writes the repaired source of those that only render after `audit-repair`. `--checker` renders the turned and repaired diagrams: a turn that would make a diagram wider than before is dropped (`not_turned` in the summary), and those still wider than 700 px are named |
| `packets CARDS OUT` | builds model requests in batches of ten (`--batch`), and writes the pregated cards to `pregated.jsonl`. A card whose old diagram renders is sent with that diagram's source (the repaired source, if it was repaired), and the instructions ask for a new diagram only when it shows the essence's relation clearly better. A card tiered `redraw` for width is sent as `too_wide` with its source: the model may draw the same steps narrower |
| `apply CARDS ANSWERS... --out OUT` | reads the model answers and decides each card: `draw` (with Mermaid), `rewrite` (writes a rewrite packet `rewrite-NNN.json`) or `skip`, into `outcomes.jsonl` and `mermaid.jsonl`. Name each answer `X.answer.json`, next to the packet `X.json` it answers. `--checker tools/mermaid_check/check.mjs` renders every new diagram and gates on V9, the measured width (V14) and V15 |
| `writeback VAULT OUT OUTCOMES...` | plans (or with `--write`, applies) the result for every pool card, by the rules below. Pass `outcomes.jsonl` and `pregated.jsonl` with the first round before the rewrites: a later outcome replaces an earlier one. Pass the audit with `--old-audit old_audit_outcomes.jsonl`. `--checker` renders the old diagrams that stay but change (turned or repaired) as they will be written. `--replace-only` writes new diagrams only: every other write becomes `keep_old` (rule name ending `/replace_only`), so the cards of a redraw set that get no new diagram stay as they are |

`prune` and `writeback` refuse `--write` without `--checker` when the plan turns a diagram top to bottom; without `--write` they only warn.

The write-back rules for a pool card:

- **draw**: the new diagram replaces the diagram section. If the old diagram renders, the new one replaces it only when it cites `E` and two propositions (all of them, if the card has fewer). Otherwise the card follows the skip rules below, under a rule name that starts with `draw:weak/`. A new flowchart that only redraws the old one is not written either: it has the same shape (the same number of nodes and of distinct edges, and the same in and out degrees) and at least 30% of the label words of the two diagrams are shared. That card follows the skip rules under a rule name that starts with `draw:same_as_old/`. This check does not apply to a card tiered `redraw` for width (rule `wide`): a narrower diagram of the same steps is the reason it was drawn again. Like every new diagram, it must pass the width check V14.
- **skip**: in a removed domain (`--remove-domain`, or tiered `domain` by the audit), the old diagram is removed. On a `delete` card that was in the pool for review, the old diagram is removed only when its audit says `restates` or `misleading`, and kept when the audit keeps it. Until there is a verdict nothing is written: the action is `pending`, with the rule `skip:delete_review/no_audit` (or that rule after `draw:weak/` or `draw:same_as_old/`). `--delete-review-on-skip keep` keeps all of these. Any other old diagram that renders (also after `audit-repair`) stays, and one that still does not parse is removed.
- **old-diagram audit**: an old diagram that stays is removed if its audit says `restates` or `misleading`.
- **repaired old diagram**: where an old diagram that only renders after `audit-repair` stays, its repaired source replaces the broken one (action `repair_old`, rule name ending `+repaired`).
- **wide old diagram**: an upright flowchart more than 730 px wide was already judged again (tier `redraw`, rule `wide`). An old diagram that stays and renders wider than 700 px is turned top to bottom when its direction is `LR` or `RL` (action `direction_tb`, rule name ending `+tb`). One that cannot be turned stays as it is, and its rule name ends `+wide`. With `--checker`, a diagram whose top-to-bottom render would be wider than its old width is not turned: it stays as it is (action `keep_old`, `+wide` instead of `+tb`, or `+wide+repaired` for a repaired one written back unturned), and one still wider than 700 px after it was turned or repaired also gets `+wide`. State diagrams are scaled to fit and never marked.
- **rewrite still pending, or no outcome**: nothing changes. The old-diagram audit acts only when its file is passed with `--old-audit`.

A typical run:

1. `audit-extract`, then `node tools/mermaid_check/check.mjs OUT/blocks.jsonl OUT/checks.jsonl`, `audit-repair --checker` and `audit-tier`.
2. Audit the old diagrams that `prune` could remove: `pool --delete-tier --out OUT/delete_pool.jsonl`, `old-audit-packets` on that file, the model answers, and `old-audit-apply --out OUT/delete_audit`.
3. `prune --old-audit OUT/delete_audit/old_audit_outcomes.jsonl --checker`, first without `--write` to read the plan, then with `--write`.
4. `pool`, `packets` on the pool, the model answers, `apply --checker`, and the rewrites.
5. `old-audit-packets` on the pool, the answers, and `old-audit-apply --out OUT/pool_audit`. This can run beside step 4 and covers every pool card that might keep or lose its old diagram; after step 4, `--outcomes outcomes.jsonl` limits it to the cards the model skipped.
6. `writeback --checker --old-audit OUT/pool_audit/old_audit_outcomes.jsonl` without `--write` to read the plan, then with `--write`.

Run `sb diagrams <command> --help` for every option.

The commands that read or write cards in a vault (`audit-extract`, `prune`, `writeback`) find the card sections by language: `--language en|zh` when given, else the vault's recorded language (`sb language`), else `[cards] language` of the config file, else the headings the cards use. They print the choice on stderr. If the cards use the other language's headings, the command stops and names the `--language` to pass. `pool` reads `cards.jsonl`, so it follows the language that `audit-extract` used. `audit-extract` reads the cards under `--knowledge-dir`, else `[layout] knowledge` of the config file, else `10_Knowledge`.

## Checking against Obsidian's Mermaid

[`tools/mermaid_check`](../tools/mermaid_check/README.md) loads the Mermaid version that Obsidian bundles, with the same patch and options, in headless Chrome. It needs Node.js 18 or later and a Chrome or Chromium binary (`npm ci` in that folder). The test suite runs every form of generated diagram through it when it is installed, and skips that test otherwise.

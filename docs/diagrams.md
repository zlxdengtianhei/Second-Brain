# Card diagrams

A card gets a diagram only when the picture shows something the text does not: a branch, a loop, two paths that merge, parts that pass data to each other, a chain of pointers. The model decides whether to draw and describes the diagram as data (the IR). Code checks the IR and writes the Mermaid. The model never writes Mermaid, so a diagram cannot carry a claim the card does not make, and its syntax is always valid.

## The step in the ingest chain

The step runs after review, on the accepted cards of a source, before the vault is written.

1. **Pregate.** Cards of kind `contrast`, `fact` and `derivation` are skipped by rule, without a model call: a table, a sentence or an aligned formula shows them better. A card without propositions is skipped too.
2. **Request.** The other cards go to the producer model in batches of ten, with the instructions in [`src/secondbrain/prompts/diagram.md`](../src/secondbrain/prompts/diagram.md). Each card is sent as its essence `E`, its numbered propositions `P1`, `P2`, ..., its kind and the titles of related cards.
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
| `sequence` | two to five parties that exchange messages | `actor` |

`arrows` (flow and structure only) says what an arrow means: `control` (the default for flow), `data`, or `relation` (the default for structure). A data diagram must show where data enters (role `input`) and where it leaves (role `output`). `sb diagrams schema` prints the full JSON Schema.

## Checks

| Code | Check | Where it runs |
|---|---|---|
| V1 | shape: field types, a known form, roles allowed in that form, unique node keys | every IR |
| V2 | size: flow and structure 4 to 9 nodes (3 when two paths merge), state 3 to 8 states, sequence 2 to 5 participants | every IR |
| V3 | every node, labelled edge and message cites parts the card has (`E`, `P1`..`Pn`) | every IR |
| V4 | at least half of each label's words are found in the cited text (formula names and symbols count; `yes`, `no`, `start`, `end` and a few others are exempt) | every IR |
| V5 | the diagram adds structure: a branch, merge or loop in a flow; a merge, loop or three or more links in a structure (a plain chain must cite three or more parts); three or more messages in a sequence; and a flow on a card that says the work repeats draws the back edge | every IR |
| V6 | label widths: node labels at most 20 units, decision labels 10, edge labels 14, messages 20 (a CJK character counts 1, a Latin character 0.5) | every IR |
| V7 | structure, state and sequence links are labelled; edges leaving a decision state their condition; a decision has two or more exits | every IR |
| V8 | a card with two or more propositions gets a diagram that cites two or more parts | every IR |
| V9 | the generated Mermaid renders in the Mermaid build Obsidian ships | regression corpus in the tests; `apply --checker` |
| V10 | labels are plain text, not TeX | every IR |
| V11 | a data diagram shows its inputs (outgoing edges only, into nodes whose cited text mentions them) and its outputs or a final store (incoming edges only) | every IR |
| V12 | the diagram cites `E`: it carries the main relation the essence states | every IR |
| V13 | no node fans out into more than three leaves, and no decision whose three or more exits are all leaves (that is a list) | every IR |
| V14 | rendered width at most 700 px: estimated from the layout, or measured with `apply --checker` | every IR (estimate); `apply --checker` (measured) |
| V15 | edge labels do not lie on top of each other after layout | `apply --checker` |

Warnings do not block a diagram. W0 reports an unknown key that was ignored or the retired role `data`, W1 a node that names a related card (card-to-card links belong in the relations, not in the diagram), and W2 an edge labelled with a relation type such as `depends_on`.

The thresholds are defaults chosen from the distribution of the diagrams in the author's vault and a 50-card pilot. They are not calibrated against a larger study.

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

The first line records which parts of the card the diagram cites and the first 8 hex digits of a SHA-256 over the essence and all propositions. If the card text changes later, the hash no longer matches, and the diagram counts as stale. The generator also numbers the nodes itself (`n1`, `n2`, ...), escapes every character that Mermaid reads as syntax, always lays flowcharts out top to bottom, and writes no colours or styles, because Obsidian inverts diagrams in dark themes.

## Diagrams that are already in a vault

`sb diagrams <command>` (the same as `python -m secondbrain.diagrams <command>`) runs batch tools on an existing vault. They are for a vault that already has many diagrams, made by hand or by an earlier tool. Nothing in the vault changes unless a command gets `--write`. Every write is guarded by the file hash recorded at audit time; a card edited since then is reported as a conflict and left alone.

| Command | Does |
|---|---|
| `schema` | prints the IR JSON Schema |
| `audit-extract VAULT OUT` | reads every card (read-only) into `cards.jsonl`, and every Mermaid block into `blocks.jsonl` (`--language` picks the card headings) |
| `audit-tier OUT` | reads `cards.jsonl` and the `tools/mermaid_check` results in `checks.jsonl`, and sorts every diagram into a tier: `keep`, `delete`, `review` or `redraw` (`--delete-domain` puts a whole domain into `delete`) |
| `prune VAULT OUT` | removes the diagram section of `delete` cards (mechanism cards with three or more propositions go to the review pool instead), and turns wide left-to-right keepers top to bottom |
| `pool OUT` | lists the cards to judge again: tiers `review` and `redraw`, plus the `delete` cards that prune left for review (`--refs FILE` limits it to the card refs in a file) |
| `packets CARDS OUT` | builds model requests in batches of ten (`--batch`), and writes the pregated cards to `pregated.jsonl` |
| `apply CARDS ANSWERS... --out OUT` | reads the model answers and decides each card: `draw` (with Mermaid), `rewrite` (writes a rewrite packet) or `skip`; `--checker tools/mermaid_check/check.mjs` renders every new diagram and gates on V9, the measured width (V14) and V15 |
| `old-audit-packets POOL OUT` | builds requests that ask, for each card whose old diagram would stay, whether that old diagram restates the text, misleads, or is useful |
| `old-audit-apply ANSWERS... --out OUT` | reads those answers; only `remove` with `restates` or `misleading` removes an old diagram, every other answer keeps it |
| `writeback VAULT OUT OUTCOMES...` | plans (or with `--write`, applies) the result for every pool card |

The write-back rules for a pool card:

- **draw**: the new diagram replaces the diagram section. If the old diagram renders, the new one replaces it only when it cites `E` and, on a card with two or more propositions, at least two of them; otherwise the old one stays (rule `draw:weak`).
- **skip**: in a removed domain (`--remove-domain`, or tiered `domain` by the audit), the old diagram is removed. On a `delete` card that was in the pool for review, it is removed too (`--delete-review-on-skip keep` keeps it). Otherwise an old diagram that renders stays, and one that does not is removed.
- **old-diagram audit**: an old diagram that stays is removed if its audit says `restates` or `misleading`.
- **rewrite still pending, or no outcome**: nothing changes.

A typical run: `audit-extract`, then `node tools/mermaid_check/check.mjs OUT/blocks.jsonl OUT/checks.jsonl`, then `audit-tier`, `prune`, `pool`, `packets` on the pool, the model answers, `apply --checker`, the rewrites, `old-audit-packets` and `old-audit-apply`, and finally `writeback` without `--write` to read the plan, then with `--write`. Run `sb diagrams <command> --help` for every option.

The commands that read or write cards in a vault (`audit-extract`, `prune`, `writeback`) find the card sections by language: `--language en|zh` when given, else `[cards] language` of the config file, else the headings the cards use. They print the choice on stderr. `pool` reads `cards.jsonl`, so it follows the language that `audit-extract` used.

## Checking against Obsidian's Mermaid

[`tools/mermaid_check`](../tools/mermaid_check/README.md) loads the Mermaid version that Obsidian bundles, with the same patch and options, in headless Chrome. It needs Node.js 18 or later and a Chrome or Chromium binary (`npm ci` in that folder). The test suite runs every form of generated diagram through it when it is installed, and skips that test otherwise.

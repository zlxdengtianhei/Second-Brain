# Export: one map's cards as an article

[中文](export.zh-CN.md)

`sb export` writes the cards of one map (MOC) up as one article. The program makes the outline from the taxonomy. A writer model writes the article unit by unit from the cards. A reviewer in another run checks each paragraph against the cards it cites, and the program checks citations, numbers and formulas. A paragraph that still fails when the repair rounds are used up is marked in the article and put on your list.

The article is a view of the cards: rebuild it when the cards change, and do not edit it. It is never written into the knowledge folder.

## Commands

```sh
sb export --domain <slug>                     # the map of this domain, as an article
sb export --moc "<map title>"                 # the map with this title
sb export --domain <slug> --ids <id> <id>     # only these cards of the map
sb export --domain <slug> --html              # also an HTML page with typeset formulas
sb export --domain <slug> --mode parallel     # write every unit at once
sb export --domain <slug> --out <folder>      # write the files there
sb export --domain <slug> --prepare-only      # freeze the outline, no model call
sb export --domain <slug> --executor host     # hand each step to a sub-agent
sb export --job <job_id> --executor host      # continue after the sub-agents submitted
sb export --domain <slug> --no-formula-check  # go on without the MathJax check
```

`--max-rounds N` overrides `[review] max_repair_rounds` for this run. `--parallel N` (1 to 8, default 4) sets how many model calls run at the same time in API mode. `--tag "<text>"` is kept in the job input; a new tag starts a new job. The same cards with the same options make the same job, so running the command again continues it.

## The steps

| Step | Done by | What happens |
|---|---|---|
| Outline | program | The taxonomy gives the sections, sub-sections and the order of the cards ([below](#the-outline)). The outline is frozen as the job's input. |
| Write | writer | Each writing unit (8 to 15 cards) is written as paragraphs. Each paragraph names the cards it uses with `[[Card title]]`. |
| Check | program | The program checks each paragraph ([below](#program-checks)). |
| Review | reviewer | A reviewer in another run judges each paragraph against the cards it cites: `accept` or `revise`, with a reason. |
| Repair | writer | A unit with a `revise` or a program finding goes back to the writer, with the findings, while `[review] max_repair_rounds` lasts. |
| Smooth | writer | One call writes the introduction and the transitions between sections, and chooses which duplicate paragraphs to drop. |
| Review the joins | reviewer | The same reviewer judges the introduction and each transition; a `revise` sends them back under the same rules. |
| Final checks | program | The program checks the whole article again and writes the files ([Output](#output)). |

### The outline

- The narrative of the map feeds the introduction.
- Pillars become sections in their `order`. A pillar's `why` is the point of the section's first paragraph.
- Topics become sub-sections in taxonomy order. A topic with fewer than 3 cards gets no heading of its own: its cards open the section of the pillar.
- Inside a topic, the cards follow the prerequisite order of the map page.
- Each pillar is cut into writing units of 8 to 15 cards, as even as possible, at a topic boundary when one is within 3 cards. Each part gets a target length: about 120 words per card in English, about 250 characters per card in Chinese.
- Cards of the map without a valid pillar and topic form a last section, "Unclassified cards", so no card of the scope is lost.

The writer sees each card's whole body without its links, review cards and diagram, and the relations between the cards of the unit as data.

### Sequential or parallel

`[export] mode` sets the default, and `--mode` overrides it for one run:

- `sequential` (the default): unit after unit. Each writer also sees the last paragraph of the unit before and the headings of the units already written.
- `parallel`: every unit at the same time, without the paragraph before.

Why sequential is the default: in a test of 6 sections, a Flash writer (GLM-5.3-Flash) that wrote unit after unit matched an Opus writer. In blind pairwise comparison it tied 5 sections and won 1. It made 0.54 statements per 10,000 characters that the cards do not support or that contradict them, against 0.29 for Opus. Writing every unit at once was about three times faster and read as well, but the same writer then made 1.63 such statements per 10,000 characters. A DeepSeek Flash writer lost the comparison in both modes. The judges were Opus models, which tend to prefer Opus text.

Which models write and review is your choice ([Configuration](#configuration)).

### Program checks

Each paragraph is checked against the cards it cites:

- it cites at least one card with `[[...]]`, and every link points to a card that exists;
- every number in it appears in the cards it cites;
- every formula appears in the cited cards (spaces ignored) and parses in MathJax 3 ([The formula check](#the-formula-check));
- formulas follow Pandoc's rules for `$`: no space after the opening `$` or before the closing `$`, no digit right after the closing `$`; a `$$` block starts and ends on its own line; no `|` inside a formula in a table row; no `$` that opens no formula;
- it holds no heading (the program makes the headings);
- it holds no literal `\n` outside formulas ([Reading the answers](#reading-the-answers)).

Every finding sends the unit back for a repair round, like a `revise`. The introduction and the transitions cite nothing, so they are checked against all cards of the article. After smoothing, the whole article is checked again. Every card should be cited at least once; the cards that no paragraph cites are listed at the end of the article.

### Review and repair

The reviewer gets the paragraphs, the cards they cite and the cards of the unit. For each paragraph it answers `accept` or `revise` with a concrete reason, and it can write notes. Notes have the same five kinds as in ingest ([Review rules](ingest.md#review-rules)): when the reviewer accepts a paragraph with a note of a blocking kind (`fact`, `unreadable`, `quote`, `not_a_unit`), the program turns the accept into a revise, with the note as its reason. The report keeps the turned paragraphs of each round in the unit's history.

### Smoothing

One call sees the outline, the narrative of the map, and the first and last paragraph of each unit. It writes the introduction, from the narrative of the map, and a transition into each later section. It does not rewrite the paragraphs. The program first finds pairs of paragraphs that repeat each other (by shared runs of characters), and the smoothing call chooses which of them to drop. A drop that would leave a card uncited is refused.

### A paragraph that never passed

When the repair rounds are used up, or a step's answers keep failing the program's checks past `[review] max_invalid_retries` (counted in the job, so running the export again goes on from the count), the unit is kept as `escalated`. A review that never gave a valid answer marks each paragraph it was to judge as not reviewed; a unit whose writer never gave one leaves a marker in the article where it would stand, under each of its headings: the part was not written, why (how many answers failed the checks, and the last message), and the cards it was to cover, as links; the unit is on your list too. It is not dropped, and it is not printed as if it had passed. Each paragraph that the review or the program still did not pass gets a line before it, with the first sentence of the reason:

```text
[This paragraph did not pass review: <reason>]
〔此段复核未通过：<reason>〕
[A part is missing here; it was not written: <reason>. It was to cover these cards: [[Card A]], [[Card B]]]
〔此处缺一段，没有写成：<reason>。这一段本来要写这些卡：[[卡 A]]、[[卡 B]]〕
```

The first and third forms are used in an English article, the second and fourth in a Chinese one. The same holds for the introduction and the transitions. Each such paragraph is also on your list: `sb status --owner-items` shows it with `owner_reason: pending` and `kind: export_paragraph`, with the article, the file, the job and the reason, from the latest finished export of each article. The report lists these paragraphs under `flagged`, and the front matter counts escalated units and flagged paragraphs.

Why: a paragraph that is not faithful, printed like the others, misleads the reader. A paragraph left out breaks the article and hides what is missing.

## Output

The files go to `[layout] articles` in the vault (`60_Articles` by default), or to `--out`:

- `<map title>_<YYYYMMDD>.md`: front matter (title, time generated, the writer and reviewer models, mode, cards, unused cards, escalated units, flagged paragraphs, job, and a note that it is a generated view), a line that tells Typora users how to switch on inline formulas, the article, and at the end "Cards not used in the text";
- `<map title>_<YYYYMMDD>.report.json`: the program's metrics (cards cited, unused cards, numbers not in the cited cards, formulas and how many parse, form violations, unknown links, every finding), each unit's status, repair rounds and history, the flagged paragraphs, the smoothing step, and the model runs with their tokens;
- with `--html`, `<map title>_<YYYYMMDD>.html`: one page made with Python-Markdown and `pymdownx.arithmatex`, where MathJax 3 typesets the formulas. So it does not depend on an editor setting. The page loads MathJax from a CDN when it opens. It needs the `export` extra: `pip install -e ".[export]"`.

Formulas are written as the cards have them: `$...$` inline and `$$` blocks on their own lines. The program never rewrites them. These are the only forms that Typora, Obsidian and GitHub all show; Typora shows inline formulas only when "Inline Math" is on, which is why the first line says how.

## The formula check

When the cards of an article have formulas, the program first checks that the MathJax checker works: it must parse a valid formula and refuse a broken one. This happens before any model is asked. If the checker cannot run, `sb export` stops with exit code 2, says why and how to install it, and spends no model call. The checker needs Node.js on `PATH` and the `mathjax-full` package:

```sh
npm install --prefix ~/.cache/sb-mathjax mathjax-full@3
export SB_MATHJAX_NODE_PATH=~/.cache/sb-mathjax/node_modules
```

The checker script, `mathjax_check.js`, is installed with the package (next to `secondbrain/export/checks.py`), so an install that is not editable (from git or from a wheel) has it too; `mathjax-full` you install yourself, as above. `SB_MATHJAX_CHECK` names another checker script, which is then used instead. If the checker fails later in the run, the export stops with the same error.

To go on without the check, run `sb export --no-formula-check`. The article then says under its title that its formulas were not parse-checked, and the report has `formula_check_skipped`. Cards without formulas need no checker.

## Reading the answers

The writer's paragraphs hold TeX, and a model that copies `\mid` or `\ge` into a JSON string without escaping it writes invalid JSON. So `sb export`, and only `sb export`, reads an answer that is not strict JSON once more, with the backslash of every TeX command doubled. A strict parse that succeeds is never read again. This reading has a cost: a real line break `\n` before letters is read as TeX. So the program reports a literal `\n` outside a formula as a finding, and the unit goes back for repair. Ingest answers are always parsed strictly ([ingest.md](ingest.md#answers-of-the-wrong-shape)).

## Configuration

```toml
[export]
mode = "sequential"          # sequential | parallel

[export.writer]              # unset: [executor.producer]
kind = "openai"
base_url = "https://api.example.com/v1"
model = "writer-model"
api_key_env = "SB_WRITER_API_KEY"

[export.reviewer]            # unset: [executor.reviewer]; a model family other than the writer's
kind = "anthropic"
base_url = "https://api.anthropic.com"
model = "reviewer-model"
api_key_env = "SB_REVIEWER_API_KEY"
```

`[export.writer]` and `[export.reviewer]` take the same keys as `[executor.producer]` ([configuration.md](configuration.md#executor-executorproducer-executorreviewer)). Each one that is not set falls back to `[executor.producer]` or `[executor.reviewer]`. In API mode the writer and the reviewer must come from different model families, as in ingest. `[layout] articles` sets the default folder. The review rules come from `[review]`: `max_repair_rounds`, `max_invalid_retries` and the note kinds apply as in ingest.

## Host mode

`sb export --domain <slug> --executor host` freezes the outline and returns `handoffs`, as `sb ingest` does. Give each hand-off to a new sub-agent: `role: producer` for writing, repair and smoothing, `role: reviewer` for the reviews ([agents.md](agents.md)). Every step that can run is handed out at once; in sequential mode, a unit is handed out once the unit before it has a draft. When the sub-agents have submitted, run `sb export --job <job_id> --executor host` for the next hand-offs. Repeat until the result has `status: complete` and `files`.

Continue with `sb export --job`, not with `sb job advance`: `sb job advance` can finish the steps, but it does not write the article files. `sb export --job <job_id>` on a finished job writes them.

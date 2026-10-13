# Examples

## `vault/`: an English example vault

A small, complete Second Brain vault for the [quick start](../README.md#quick-start-with-the-example-vault). Copy it before you try commands on it; `sb` writes into the vault it is pointed at.

| Part | What is there |
|---|---|
| `00_Inbox/` | two short notes that have not been ingested yet |
| `10_Knowledge/` | 42 cards in three domains: `learning` (13), `statistics` (12), `computing` (17); 8 cards have a diagram |
| `20_MOCs/` | one map per domain, its pillar pages, and the taxonomy files in `_pillars/` |
| `40_Sources/` | the 7 source notes the cards were made from |
| `_data/` | the catalog, the card and inbox registries, and a review log with 5 reviews |
| `HOME.md` | the generated entry page |
| `90_Meta/prompts/`, `.obsidian/` | English prompt templates and settings for the SecondBrain Review plugin |

How it was made:

1. The catalog (`sb init --catalog`), the three taxonomy files, the empty registry and the folders were written by hand, as [docs/new-vault.md](../docs/new-vault.md) describes. The source notes are original text written for this example.
2. Seven notes were ingested with the Agent Skill in Claude Code: one fresh sub-agent per hand-off, producers on one Claude model and reviewers on another (so every review is recorded as same-family). 34 sub-agent runs in total; every job was written in full, with no pending cards.
3. One card links across domains on purpose: "Bloom filter as a cheap first check and the rare-item false positive problem" (computing) is `similar_to` "Screening test with a rare disease" and `example_of` "Positive predictive value" (statistics).
4. Five reviews were logged with `sb review log --device example`, so the review status shows a few learned cards next to new ones.
5. After a tool update, the maps and `HOME.md` were regenerated with `sb refresh --apply` and `sb docs --apply`.

`tests/test_example_vault.py` checks that a fresh copy reports no problems, that the maps and `HOME.md` match the cards, that relations and links resolve, and that all text is English.

License: the vault content is under CC BY 4.0; the prompt templates and plugin settings are under MIT ([LICENSE](LICENSE)).

## `prompts/`: prompt templates for the review plugin

Grading (`grade.md`) and explanation (`explain.md`) prompts for SecondBrain Review, in Chinese, with English versions in `grade.en.md` and `explain.en.md`. Copy one to the path set in the plugin's settings (default `90_Meta/prompts/`) to change it.

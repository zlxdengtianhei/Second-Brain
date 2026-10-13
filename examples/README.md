# Examples

[中文](README.zh-CN.md)

Two small, complete Second Brain vaults on the same three subjects (how we learn, reasoning with data, computing foundations), one in English and one in Chinese, and the prompt templates of the review plugin. Copy a vault before you try commands on it; `sb` writes into the vault it is pointed at.

## `vault/`: the English example vault

Used by the [quick start](../README.md#quick-start-with-the-example-vault) of the English README.

| Part | What is there |
|---|---|
| `00_Inbox/` | two short notes that have not been ingested yet |
| `10_Knowledge/` | 42 cards in three domains: `learning` (13), `statistics` (12), `computing` (17); 8 cards have a diagram |
| `20_MOCs/` | one map per domain, its pillar pages, and the taxonomy files in `_pillars/` |
| `40_Sources/` | the 7 source notes the cards were made from |
| `_data/` | the catalog, the card and inbox registries, and a review log with 5 reviews |
| `HOME.md` | the generated entry page |

How it was made:

1. The catalog and the three taxonomy files were written by hand and the catalog put in with `sb init --catalog`. The `sb init` of that day did not yet add the empty registry and the folders, so they were made by hand too; [docs/new-vault.md](../docs/new-vault.md) describes the current steps. The source notes are original text written for this example.
2. Seven notes were ingested with the Agent Skill in Claude Code: one fresh sub-agent per hand-off, producers on one Claude model and reviewers on another (so every review is recorded as same-family). 34 sub-agent runs in total; every job was written in full, with no pending cards.
3. One card links across domains on purpose: "Bloom filter as a cheap first check and the rare-item false positive problem" (computing) is `similar_to` "Screening test with a rare disease" and `example_of` "Positive predictive value" (statistics).
4. Five reviews were logged with `sb review log --device example`, so the review status shows a few learned cards next to new ones.
5. After tool updates, the maps and `HOME.md` were regenerated with `sb refresh --apply` and `sb docs --apply`.

## `vault-zh/`: the Chinese example vault

Used by the quick start of the [Chinese README](../README.zh-CN.md). The vault records its language as `zh`, so cards have the headings `本质`, `命题` and `复习卡`, and the maps and `HOME.md` are in Chinese.

| Part | What is there |
|---|---|
| `00_Inbox/` | two short notes that have not been ingested yet |
| `10_Knowledge/` | 52 cards in three domains: `learning` (13), `statistics` (17), `computing` (22); 8 cards have a diagram |
| `20_MOCs/` | one map per domain, its pillar pages, and the taxonomy files in `_pillars/` |
| `40_Sources/` | the 7 source notes the cards were made from |
| `_data/` | the catalog, the card and inbox registries, and a review log with 5 reviews |
| `HOME.md` | the generated entry page |

How it was made:

1. The source notes are Chinese versions of the English vault's notes, written for this example. The catalog and the three taxonomy files were written by hand in Chinese; `sb init --catalog` put the catalog in and added the registry and the folders.
2. Seven notes were ingested with the Agent Skill in Claude Code, with `[cards] language = "zh"`: one fresh sub-agent per hand-off, producers on one Claude model and reviewers on another. 31 sub-agent runs in total; every job was written in full, with no pending cards.
3. One card links across domains: "布隆过滤器作为慢速查找的前置检查" (computing) is `similar_to` "基础率忽视" (statistics), and the inverse relation is on the other card.
4. Five reviews were logged with `sb review log --device example`.

Neither vault holds plugin settings: the SecondBrain Review plugin reads the vault's language and uses the matching headings, interface and built-in prompts.

`tests/test_example_vault.py` and `tests/test_example_vault_zh.py` check that a fresh copy of each vault reports no problems, that the maps and `HOME.md` match the cards, that relations and links resolve, that the text is in the vault's language, and that the counts given here, in both READMEs and in docs/agents.md are the vaults'.

## `prompts/`: prompt templates for the review plugin

Grading (`grade.md`) and explanation (`explain.md`) prompts for SecondBrain Review, in Chinese, with English versions in `grade.en.md` and `explain.en.md`. The plugin has the same texts built in. To change one, copy it to the path set in the plugin's settings (default `90_Meta/prompts/grade.md` and `90_Meta/prompts/explain.md`) and edit it there.

## License

Everything here is under the MIT License of the repository ([LICENSE](../LICENSE)).

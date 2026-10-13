# SecondBrain Review (Obsidian plugin)

The daily entry point for reviewing a Second Brain vault. It shows what is due today,
runs flip and question-and-answer reviews, and explains a card with a language model.
Every review is appended to an event log in the vault; scheduling (FSRS) is replayed
from that log, so card files are never modified. The same log is written by the
`sb review` CLI, so reviews done with a terminal agent count too.

The interface is in Chinese. Strings live in `src/i18n.ts` if you want to add a locale.

## What it does

| Feature | Where |
|---|---|
| Today dashboard: due cards, new cards started / daily limit, reviewed today, streak, progress per domain | Side view (ribbon icon or command), or a code block in any note |
| Flip review: question, show answer, rate Again / Hard / Good / Easy (keys 1–4, space to reveal) with the next interval on each button | Command or dashboard button |
| Q&A review: answer each question in your own words; the model grades it against the reference answer and the card's propositions; you confirm or change the rating; the card gets the lowest rating of its questions | Command or dashboard button |
| Explain: the model explains the open card with its related cards' essence, its pillar and topic in the knowledge map, and your review history; ask follow-up questions | Command, dashboard button, or the button in a review session |
| Reminders: status-bar count, one notice on the first launch of the day, and a daily calendar file (`.ics`) whose link opens the dashboard | Settings → Reminders |
| One-time import of Spaced Repetition plugin schedules (`<!--SR:...-->`, `sr-due`) | Command or settings |

Embed the dashboard in a note (for example `HOME.md`):

````markdown
```secondbrain-review
```
````

Open it from anywhere (calendar apps, other notes): `obsidian://secondbrain-review`
(add `&mode=flip` or `&mode=qa` to start a session).

## Install

1. Build: `npm ci && npm run build` in this folder.
2. Copy `main.js`, `manifest.json` and `styles.css` to
   `<vault>/.obsidian/plugins/secondbrain-review/`.
3. Enable **SecondBrain Review** in Settings → Community plugins.
4. For grading and explanations, install and enable the
   [AI Providers](https://github.com/pfrankov/obsidian-ai-providers) plugin, add one
   provider there (type, key, model, test), then pick it in this plugin's settings.
   Without it, flip review and the dashboard work; Q&A answers are rated by you.

## Settings

| Setting | Default |
|---|---|
| Knowledge folder, taxonomy folder (pillar YAML), data folder | `10_Knowledge`, `20_MOCs/_pillars`, `_data` |
| Section that holds `question::answer` lines | `复习卡` |
| New cards per day; study domains (new cards come only from these, in prerequisite order) | 10; all domains |
| Model (from AI Providers); grading and explanation templates | none; `90_Meta/prompts/grade.md`, `90_Meta/prompts/explain.md` (built-in defaults when missing; see `examples/prompts/`) |
| Explanation limits: related cards, essence length, same-topic cards, card length, answer length | 8, 120, 10, 6000, 600 |
| Daily notice; calendar time and file | on; `09:00`, `90_Meta/复习提醒.ics` |
| Log format | `jsonl` (choose `md` if your sync service only syncs notes) |

## Data

- Review log: `<data>/review/events-<device>.jsonl` (or `.md`), one file per device,
  append only. The format, the scheduler and the replay rules are specified in
  [`docs/review-registry.md`](../docs/review-registry.md).
- The device id (`obs-xxxxxx`) is kept in this device's local storage, not in the
  synced settings.
- Obsidian Sync does not sync `.jsonl` files unless "Sync all other types" is on.
- Q&A answers you type are stored in the log (up to 2,000 characters per question).
  Prompts go only to the provider you chose in AI Providers.

## Development

```sh
npm ci
npm test        # vitest: shared vectors with the Python registry, data layer, LLM parsing, UI models
npm run build   # type check + production bundle
```

`src/registry/` is the TypeScript twin of `src/secondbrain/review_registry.py`; both must
pass `tests/fixtures/review/vectors.json`. Modules under `src/registry/`, `src/llm/` and
`src/session.ts` do not import Obsidian and are unit-tested; `src/ui/`, `src/vault/` and
`src/main.ts` adapt them to Obsidian.

# SecondBrain Review (Obsidian plugin)

The daily entry point for reviewing a Second Brain vault. It shows what is due today,
runs flip and question-and-answer reviews, and explains a card with a language model.
Every review is appended to an event log in the vault; scheduling (FSRS) is replayed
from that log, so card files are never modified. The `sb review` commands of the
Python package (`status`, `queue`, `log`, `import-sr`) read and write the same log, so
reviews a terminal agent logs with `sb review log` count too.

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

Open it from anywhere (calendar apps, other notes): `obsidian://secondbrain-review`.
To start a session at once: `obsidian://secondbrain-review?mode=flip` or
`obsidian://secondbrain-review?mode=qa`. With several vaults, add the vault name:
`obsidian://secondbrain-review?vault=My%20Vault&mode=qa`.

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
| Section names: `question::answer` lines, essence, propositions | `复习卡`, `本质`, `命题` |
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
- A replay checkpoint is kept in this device's IndexedDB, so a start reads only the
  lines added since the last start. Clearing it is safe; the next start replays
  all logs.
- Obsidian Sync does not sync `.jsonl` files unless "Sync all other types" is on.
- Q&A answers you type are stored in the log (up to 2,000 characters per question).
  Prompts go only to the provider you chose in AI Providers.

## Manual test checklist

Unit tests cannot load the plugin inside Obsidian. Run these steps on a copy of your
vault, once on desktop and once on a phone. Keep the developer console open
(desktop: Ctrl/Cmd+Shift+I) and note any error.

1. **Install.** Copy the three files, enable the plugin. Expect: no console error; a
   status-bar item `复习 N` appears after the vault has loaded. Note how many seconds
   that takes.
2. **Dashboard.** Click the ribbon icon. Expect: the side view shows due, today not
   reviewed, new cards (started / limit), reviewed today, streak, and one row per
   domain. In a new vault every card is unlearned and due is 0.
3. **Note block.** Add the `secondbrain-review` code block to `HOME.md`. Expect: the
   same numbers in reading view and live preview, and a scrollable domain table.
4. **Flip review.** Click 开始复习. Expect: questions render (formulas, links);
   space shows the answers; each rating button shows an interval; keys 1–4 rate.
   After each rating `_data/review/events-obs-xxxxxx.jsonl` has one more line with
   `"mode":"flip"` and that rating. A card rated 重来 comes back at the end of the
   session.
5. **Study plan.** In settings, turn on one or two study domains and change new
   cards per day. Expect: new cards come only from those domains, in their order.
6. **Q&A review.** Install AI Providers, add a provider, pick it in this plugin's
   settings, click 问答复习. Answer one question well and one badly. Expect: the
   suggested rating is highlighted, you can pick another, and after the last
   question one `"mode":"qa"` line is written with one item per question and the
   lowest rating as the card rating. Then select a model that does not exist (or go
   offline): expect "未评分" with the reason, and you can still rate.
7. **Explain.** Open a card and run 讲解当前卡. Expect: the answer streams into the
   side view; the line above shows the context size and related-card count; a
   follow-up question gets an answer that uses the earlier one; 停止 stops it.
8. **Reminders.** Next day, open Obsidian. Expect: one notice with the counts, and no
   second one that day. In settings, write the calendar file and import it into the
   phone and desktop calendars. Expect: a reminder at the set time; tapping the link
   opens Obsidian on the dashboard.
9. **URI.** Open `obsidian://secondbrain-review?mode=qa` from a browser or another
   note. Expect: Obsidian opens the dashboard and starts a Q&A session.
10. **Two devices.** Review a few cards on each device, let sync finish, reopen.
    Expect: both show the same due counts and card states. With Obsidian Sync, turn on
    "Sync all other types" first, or switch the log format to `md`.
11. **Spaced Repetition import** (only if your cards have `<!--SR:...-->` comments or
    `sr-*` front matter). For an existing vault, prefer the CLI before installing the
    plugin: `sb review import-sr --dry-run`, then `sb review import-sr`. In the plugin,
    run the import command. Expect: a confirmation with the number of cards; cards whose
    SR data cannot be read are named in a notice and listed in the console, and are not
    imported; after it, the imported cards are in review with their old due dates; card
    files are unchanged; running it again imports nothing.

## Development

```sh
npm ci
npm test        # vitest: shared vectors with Python, data layer and cache, LLM parsing, review UI flows (happy-dom)
npm run build   # type check + production bundle
```

`src/registry/` is the TypeScript twin of `src/secondbrain/review_registry.py`; both must
pass `tests/fixtures/review/vectors.json`. Modules under `src/registry/`, `src/llm/` and
`src/session.ts` do not import Obsidian and are unit-tested; `src/ui/`, `src/vault/` and
`src/main.ts` adapt them to Obsidian.

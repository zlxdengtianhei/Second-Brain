# SecondBrain Review (Obsidian plugin)

[中文说明](README.zh-CN.md)

The daily entry point for reviewing a Second Brain vault. It shows what is due today,
runs flip and question-and-answer reviews, and explains a card with a language model.
Every review is appended to an event log in the vault; scheduling (FSRS) is replayed
from that log, so card files are never modified. The `sb review` commands of the
Python package (`status`, `queue`, `log`, `import-sr`) read and write the same log, so
reviews a terminal agent logs with `sb review log` count too.

The interface is in English or Chinese. By default it follows the vault's language, so an
English vault gets the English interface and a Chinese vault the Chinese one. The card section
headings the plugin reads follow the vault's language too (see [Language](#language)).

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

## Language

The vault's language is the one `sb init` or `sb language --set` recorded in
`<data>/secondbrain/catalog.json`. A vault made before that record existed is detected from
its generated map pages (the `.md` files in the folder that holds the taxonomy folder, for
example `20_MOCs`), the same way the `sb` command does it. The plugin reads the language when
Obsidian starts, when the catalog changes, and when you click Refresh on the dashboard.

| What | Follows | English vault | Chinese vault |
|---|---|---|---|
| Interface (views, buttons, notices, settings, status bar, calendar event text) | The **Interface language** setting. `Auto` (the default) uses the vault's language; when the vault has no language, Obsidian's language | English | Chinese |
| Built-in grading and explanation templates, and the context lines the plugin adds to them | The interface language | `examples/prompts/*.en.md` | `examples/prompts/*.md` |
| Section headings the plugin reads, when the setting is empty | The vault's language; when the vault has no language, the interface language | `Review cards`, `Essence`, `Propositions` | `复习卡`, `本质`, `命题` |
| Calendar file, when the setting is empty | Same as the section headings | `90_Meta/review-reminder.ics` | `90_Meta/复习提醒.ics` |

A heading or calendar path that you type in the settings always wins. The section headings
are the ones `sb` writes into cards (`src/secondbrain/cards.py`); a test checks that the two
tables agree. Command names take the interface language when Obsidian starts, so after you
change the setting, restart Obsidian to rename the commands.

Strings live in `src/i18n.ts`. To add a language, add a table with the same keys.

## Settings

| Setting | Default |
|---|---|
| Interface language | `Auto` (the vault's language, else Obsidian's) |
| Knowledge folder, taxonomy folder (pillar YAML), data folder | `10_Knowledge`, `20_MOCs/_pillars`, `_data` |
| Section names: `question::answer` lines, essence, propositions | Empty: the headings of the vault's language (see [Language](#language)) |
| New cards per day; study domains (new cards come only from these, in prerequisite order) | 10; all domains |
| Model (from AI Providers); grading and explanation templates | none; `90_Meta/prompts/grade.md`, `90_Meta/prompts/explain.md` (built-in defaults when missing; see `examples/prompts/`) |
| Explanation limits: related cards, essence length, same-topic cards, card length, answer length | 8, 120, 10, 6000, 600 |
| Daily notice; calendar time and file | on; `09:00`, empty (the file name of the vault's language) |
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

The steps name the English labels; with the Chinese interface the labels are the Chinese
ones in `src/i18n.ts` (for example `Start review` is `开始复习`, `Good` is `记得`).

1. **Install.** Copy the three files, enable the plugin. Expect: no console error; a
   status-bar item `Review N` appears after the vault has loaded. Note how many seconds
   that takes. In a Chinese vault, the interface and the section headings are Chinese; in
   an English vault (for example `examples/vault`), English.
2. **Dashboard.** Click the ribbon icon. Expect: the side view shows due, today not
   reviewed, new cards (started / limit), reviewed today, streak, and one row per
   domain. In a new vault every card is unlearned and due is 0.
3. **Note block.** Add the `secondbrain-review` code block to `HOME.md`. Expect: the
   same numbers in reading view and live preview, and a scrollable domain table.
4. **Flip review.** Click Start review. Expect: questions render (formulas, links);
   space shows the answers; each rating button shows an interval; keys 1–4 rate.
   After each rating `_data/review/events-obs-xxxxxx.jsonl` has one more line with
   `"mode":"flip"` and that rating. A card rated Again comes back at the end of the
   session.
5. **Study plan.** In settings, turn on one or two study domains and change new
   cards per day. Expect: new cards come only from those domains, in their order.
6. **Q&A review.** Install AI Providers, add a provider, pick it in this plugin's
   settings, click Q&A review. Answer one question well and one badly. Expect: the
   suggested rating is highlighted, you can pick another, and after the last
   question one `"mode":"qa"` line is written with one item per question and the
   lowest rating as the card rating. Then select a model that does not exist (or go
   offline): expect "Not graded" with the reason, and you can still rate.
7. **Explain.** Open a card and run Explain the current card. Expect: the answer streams
   into the side view; the line above shows the context size and related-card count; a
   follow-up question gets an answer that uses the earlier one; Stop stops it.
8. **Reminders.** Next day, open Obsidian. Expect: one notice with the counts, and no
   second one that day. In settings, write the calendar file and import it into the
   phone and desktop calendars. Expect: a reminder at the set time; tapping the link
   opens Obsidian on the dashboard.
9. **URI.** Open `obsidian://secondbrain-review?mode=qa` from a browser or another
   note. Expect: Obsidian opens the dashboard and starts a Q&A session.
10. **Two devices.** Review a few cards on each device, let sync finish, reopen.
    Expect: both show the same due counts and card states. With Obsidian Sync, turn on
    "Sync all other types" first, or switch the log format to `md`.
11. **Language.** In settings, set Interface language to the other language. Expect: the
    settings tab, the dashboard and the status bar change at once; the section headings
    the plugin reads do not change; after a restart, the command names change too. Set it
    back to Auto.
12. **Spaced Repetition import** (only if your cards have `<!--SR:...-->` comments or
    `sr-*` front matter). For an existing vault, prefer the CLI before installing the
    plugin: `sb review import-sr --dry-run`, then `sb review import-sr`. In the plugin,
    run the import command. Expect: a confirmation with the number of cards; cards whose
    SR data cannot be read are named in a notice and listed in the console, and are not
    imported; after it, the imported cards are in review with their old due dates; card
    files are unchanged; running it again imports nothing.

## Development

```sh
npm ci
npm test        # vitest: shared vectors with Python, data layer and cache, LLM parsing, both languages, review UI flows (happy-dom)
npm run build   # type check + production bundle
```

`src/registry/` is the TypeScript twin of `src/secondbrain/review_registry.py`; both must
pass `tests/fixtures/review/vectors.json`. Modules under `src/registry/`, `src/llm/` and
`src/session.ts` do not import Obsidian and are unit-tested; `src/ui/`, `src/vault/` and
`src/main.ts` adapt them to Obsidian.

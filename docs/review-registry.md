# Review registry

The review registry records every review of every card as an append-only event
log. Scheduling state (due date, stability, difficulty) is never stored in card
files. Every reader rebuilds it by replaying the log with FSRS.

Three writers share one format:

| Writer | Mode | Where |
|---|---|---|
| Obsidian plugin, flip mode | `flip` | `obsidian-plugin/` |
| Obsidian plugin, question-and-answer mode (LLM grading) | `qa` | `obsidian-plugin/` |
| Terminal agent through the CLI (`sb review log`) | `agent` | `src/secondbrain/review_registry.py` |
| One-time import of Spaced Repetition plugin data | `import` | both |

Two implementations replay the log: Python (`review_registry.py`, py-fsrs) and
TypeScript (`obsidian-plugin/src/registry/`, ts-fsrs). Both must produce the same
state for the same events. The shared test vectors in
`tests/fixtures/review/vectors.json` are the contract.

## 1. Files

- Directory: `<data>/review/` inside the vault (`_data/review/` with the default
  layout).
- One file per writer device: `events-<device>.jsonl`. A writer appends only to
  its own file, so two devices never edit the same file.
- `<device>` matches `[A-Za-z0-9_-]{1,64}`. The plugin generates
  `obs-<random>` once per device and keeps it in device-local storage (not in
  the synced plugin settings). The CLI uses `cli-<hash of hostname>` unless
  configured.
- Readers read every file that matches `events-*.jsonl` or `events-*.md`.
  Conflict copies made by sync tools (for example
  `events-obs-1a2b (conflict).jsonl`) match the pattern too; duplicate events
  are removed (section 4).

### 1.1 `.jsonl` storage (default)

One JSON object per line, UTF-8, `\n` line ends. Empty lines are ignored. Any
other line that is not a valid event is reported as an issue and skipped.

### 1.2 `.md` storage (for sync services that only sync notes)

Obsidian Sync does not sync `.jsonl` files unless "Sync all other types" is on.
A writer can store the same lines in `events-<device>.md` instead:

```text
---
format: secondbrain-review-log
---
{"v":1,"ts":"2026-10-13T08:12:30.000+02:00", ...}
{"v":1,"ts":"2026-10-13T08:14:02.000+02:00", ...}
```

In `.md` files, readers parse only lines whose first non-space character is
`{`. All other lines (front matter, headings, notes) are ignored. Writers
append event lines at the end of the file.

### 1.3 Appending

A writer appends one complete line per event with a single `write` call on a
file opened in append mode, then flushes it. If the file does not end with a
newline (an earlier write was cut off), the writer first writes `\n`, so the
new event starts on its own line and the broken fragment is skipped as an
issue. Writers validate an event with the reader's rules before they write it;
an invalid event is never written.

## 2. Event format, version 1

```json
{"v":1,"ts":"2026-10-13T08:12:30.000+02:00","device":"obs-3f9a2c","card":"ex_binary_search",
 "mode":"qa","rating":2,"grader":"llm:example-model",
 "items":[{"q":"h:1a2b3c4d","rating":3,"verdict":"hit"},
          {"q":"h:5e6f7a8b","rating":2,"verdict":"partial","missing":["loop invariant"]}],
 "ms":41000}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `v` | integer | yes | Format version. Readers skip lines with an unknown version and report an issue. |
| `ts` | string | yes | Review time, RFC 3339 with a UTC offset (`Z` or `+02:00`). Writers use local time with milliseconds. The offset is part of the data: it decides the learning day (section 3.1). |
| `device` | string | yes | Writer device id. |
| `card` | string | yes | The card's frontmatter `id`. Renaming or moving a card file does not break its history. |
| `mode` | string | yes | `flip`, `qa`, `agent` or `import`. Readers treat any mode other than `import` as a review, so new modes stay readable. |
| `rating` | integer | see text | Card rating: 1 Again, 2 Hard, 3 Good, 4 Easy. Required for reviews unless `items` is present. |
| `items` | array | no | Per-question detail of one card review (section 2.1). |
| `grader` | string | no | `self` (default) or `llm:<model>`. |
| `answer`, `feedback` | string | no | Learner answer and grader feedback for a single-question review. |
| `ms` | integer | no | Time spent, in milliseconds. |
| `import` | object | for `import` | Seed state from another scheduler (section 2.2). |

Readers ignore unknown fields. Writers must not reuse a field name with a
different meaning.

### 2.1 Questions and card rating

The scheduling unit is the card. A question-and-answer review asks the card's
questions one by one, collects one rating per question, and writes **one**
event for the card.

- Each item: `q` (question id, required), `rating` (1–4, required), and
  optional `verdict` (`hit`, `partial`, `miss`), `grader`, `answer`,
  `feedback`, `missing` (list of strings).
- Card rating: the `rating` field when present; otherwise the minimum of the
  item ratings (conservative: one weak answer brings the card back sooner).
  Writers that have items write `rating` as that minimum.
- Question id: `h:` followed by the FNV-1a 32-bit hash (8 lowercase hex digits)
  of the normalized question text. Normalization: Unicode NFKC, lowercase,
  collapse every run of whitespace to one space, trim. If a question is
  edited, its item history detaches; the card's scheduling does not change.

Cards keep their questions in the `## 复习卡` section as `question::answer`
lines (the section name is configurable). A trailing Spaced Repetition comment
`<!--SR:...-->` on a line is ignored.

### 2.2 Import events

An import event replaces the card's scheduling state with a seed taken from
another scheduler. It is not a review: it does not count for "reviewed today",
`reps` or streaks.

```json
{"v":1,"ts":"2026-10-13T09:00:00.000+02:00","device":"obs-3f9a2c","card":"ex_tcp_handshake",
 "mode":"import","import":{"from":"sr-sm2","due":"2026-10-20","interval":5,"ease":250}}
```

| `import.from` | Fields | Seed |
|---|---|---|
| `sr-sm2` | `due` (date), `interval` (days ≥ 1), `ease` (SM-2 ease × 100) | stability = `interval`; difficulty = clamp(10 − 9 × (ease − 130) / 220, 1, 10), rounded to 4 decimals; last review = `due` − `interval` days |
| `sr-fsrs` | `due`, `interval`, `stability`, `difficulty`, optional `last_review` | as given; last review = `last_review`, or `due` − `interval` days |

After an import the card is in the review state with `reps` 0 and `lapses` 0.
Dates without a time mean the start of that learning day (04:00) in the
import event's offset. With desired retention 0.9, FSRS schedules the next
review at an interval equal to the stability, so `stability = interval` keeps
the old due date meaningful.

Converting cards: when a card has several Spaced Repetition schedules (one per
question line, or the note-level `sr-due`/`sr-interval`/`sr-ease` fields), the
importer takes the earliest due date, the smallest interval and the smallest
ease, and writes one event for the card. Cards that already have events are
not imported again.

## 3. Scheduler

### 3.1 Learning days

Scheduling counts days in **learning days**. A learning day starts at 04:00
local time. The learning day of an event is computed from its own `ts`
(local wall-clock time in the event's offset, minus 4 hours, then the calendar
date). The day index is the number of days from 1970-01-01 to that date.

Elapsed days between two reviews = difference of their learning-day indexes.
A review at 23:30 and another at 08:00 the next morning are one day apart;
two reviews at 01:00 and 03:00 on the same night are zero days apart.

The scheduler day of a card never goes backwards. Events from devices in
different offsets can have a later instant but an earlier learning day; such an
event is scheduled on the card's last scheduler day (elapsed days = 0).

### 3.2 FSRS

Memory model: FSRS-6 with these parameters (the published defaults of both
libraries):

```text
w = 0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722,
    0.1666, 0.796, 1.4835, 0.0614, 0.2629, 1.6483, 0.6014, 1.8729, 0.5425,
    0.0912, 0.0658, 0.1542
desired retention = 0.9
learning steps    = 1 min, 10 min
relearning steps  = 10 min
maximum interval  = 36500 days
fuzz              = off
short-term (same-day) stability = on
```

Scheduling policy: the ts-fsrs 5.4.2 basic scheduler (`fsrs().next()` with the
options above), run on **day-aligned time**:

1. Each review time `t` is mapped to `t' = (scheduler day of t) × 86400 s`
   (UTC midnight of that index; section 3.1). Both libraries then compute
   elapsed days as the learning-day difference.
2. The scheduler runs on `t'`. Its due time `due'` is mapped back as
   `due = t + (due' − t')`. Interval steps measured in minutes keep their
   length; intervals measured in days land at the same local time of day.

The policy, stated without reference to a library:

| Current state | Rating | Next state | Next due |
|---|---|---|---|
| new | 1 | learning, step 0 | +1 min |
| new | 2 | learning, step 0 | +6 min (mean of the first two steps, rounded) |
| new | 3 | learning, step 1 | +10 min |
| new | 4 | review | +interval(S) days |
| learning or relearning, step k | 1 | same state, step 0 | +first step |
| learning or relearning, step k | 2 | same state, step k | +mean of first two steps (one step: 1.5 × first), rounded to minutes |
| learning or relearning, step k | 3 | step k+1 if it exists, else review | +that step, or +interval(S) days |
| learning or relearning, step k | 4 | review | +interval(S) days |
| review | 1 | relearning, step 0; lapses + 1 | +first relearning step |
| review | 2, 3, 4 | review | hard = min(I2, I3); good = max(I3, hard + 1); easy = max(I4, good + 1) days |

`S` and `D` update on every review with the FSRS-6 memory formulas (initial
values on the first review; same-day formula when elapsed days = 0; forget
formula for rating 1; recall formula otherwise). `interval(S)` =
min(max(1, round(S × modifier)), 36500), and `Ir` is the interval of the
stability that rating `r` would produce. `reps` counts reviews.

The TypeScript implementation calls ts-fsrs directly. The Python
implementation implements this policy table and gets `S`, `D` and
`interval(S)` from py-fsrs 6.3.2 (`Scheduler.review_card` on a review-state
card). The libraries differ in floating-point rounding of retrievability
(ts-fsrs rounds to 8 decimals), so stability and difficulty agree to about
1e-8 relative, not bit for bit. The test vectors compare them with a relative
tolerance of 1e-6 and compare due times exactly.

## 4. Replay

1. Read all log files; parse each line; skip and report invalid lines.
2. Remove duplicates: events with the same `device`, `card` and `ts` instant
   are one event; the first one read is kept. Files are read in path order,
   lines in file order.
3. Sort by (`ts` instant, `device`, `card`, file path, line number).
4. Apply events to each card in that order (section 3). An import event
   replaces the card state (section 2.2).

The result for each card that has at least one event:

| Field | Meaning |
|---|---|
| `state` | `learning`, `review` or `relearning` |
| `stability`, `difficulty` | FSRS memory state |
| `due` | Next due time (UTC instant) |
| `last_review` | Time of the last review (or seeded last review after an import) |
| `reps`, `lapses` | Review count; count of rating 1 in the review state |
| `first_review` | Time of the first non-import review, or none |
| `last_day` | Learning-day index of the last non-import review, or none |

A state cache (`state.json`) is optional and only for speed. Deleting it must
not lose anything; replaying all logs rebuilds it.

## 5. Status

For a viewer at time `now`, "today" is the learning day of `now` in the
viewer's local offset. Every card in the vault index is in exactly one of
these study states:

| Status | Definition |
|---|---|
| unlearned (未学) | No events for this card |
| learning (学习中) | State `learning` or `relearning` |
| review (复习期) | State `review` and stability < 21 days |
| mastered (已掌握) | State `review` and stability ≥ 21 days |

And independently:

| Flag | Definition |
|---|---|
| due (到期) | Has events, and the learning day of `due` (in the viewer's offset) ≤ today |
| reviewed today (今日已复习) | At least one non-import event on today's learning day |
| due, not reviewed today (今日未复习) | Due, and not reviewed today |

"Learned" (已学) = learning + review + mastered. A summary gives these counts
for the whole vault and per `domain`, plus: new cards started today (first
non-import review today), review events today, the study streak (consecutive
learning days with at least one review, ending today, or yesterday if there is
no review yet today), and the count of event cards that are not in the index
(deleted cards or changed ids).

## 6. Queue

The daily queue lists due cards first, then new cards.

- **Due**: cards that are due (section 5), sorted by `due`, then `id`. The
  study-domain setting does not filter due cards.
- **New**: unlearned cards that are eligible, in prerequisite order, up to
  `new_per_day` − (new cards started today). Default `new_per_day` = 10.
  - Eligible: `kind` is not `record`; `status` is not `needs-review`; `domain`
    is in the selected study domains (all domains when none are selected).
  - Prerequisite order: a topological order over eligible unlearned cards.
    Edges (“A before B”):

    | Relation on card C | Edge |
    |---|---|
    | `depends_on: T`, `follows: T` | T before C |
    | `required_by: T`, `precedes: T` | C before T |

    Other relation types (including `part_of` and `has_part`) do not order
    cards. On a real vault of 8,615 cards, adding `part_of`/`has_part` edges in
    either direction raised the number of cycle breaks from 323 to 624–720,
    so they conflict with the prerequisite edges. Edges to cards outside the
    candidate set, and self-edges, are ignored.
  - Ties: among cards that are ready, the smallest (`rank`, `id`) comes first.
    `rank` is an integer the caller computes (for example from the domain's
    pillar and topic order); the default is 0.
  - Cycles: when cards remain but none is ready, the remaining card with the
    smallest (`rank`, `id`) is taken next and reported as a cycle break. No
    card is dropped.

## 7. Configuration and paths

| Setting | Default | Notes |
|---|---|---|
| Log directory | `<data>/review` | `<data>` is the layout's data directory (`_data`) |
| Log format for writing | `jsonl` | `md` for sync services that skip other file types |
| New cards per day | 10 | |
| Study domains | none (all domains) | Only filters new cards |
| Mastered threshold | 21 days | Stability |
| Day start | 04:00 | Part of the format; not a user setting, because it changes elapsed days |

## 8. Compatibility rules

- A change that makes old logs replay differently (parameters, policy, day
  start, hashing) is a new format version. Readers keep supporting version 1.
- Adding an optional field is not a version change.
- The vectors file is the contract between the Python and TypeScript
  implementations. A change to either implementation must keep both test
  suites green against the same file.

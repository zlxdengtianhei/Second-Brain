# Ingest: from an inbox file to cards

[中文](ingest.zh-CN.md)

This page describes what `sb ingest` does, what each check rejects, what is written, and how to recover. The [README](../README.md) has the short version.

## The inbox

```sh
sb inbox list                                   # every file in the inbox with its status and job id
sb inbox show "<name>" --head 60                # the first lines of one file
sb inbox skip "<name>" --reason "a receipt"     # mark a file as not knowledge
```

A file's status is `new`, `in_progress`, `done`, `partial` (some cards still pending; see `sb ingest --retry`), `skipped` or `failed`. The status is found by content hash, so a renamed copy of an ingested file is still `done`. A source stays in the list after its file has left the inbox when it is `partial`, or when it is `done` and came from the inbox; it then has `in_inbox: false` and `path` set to the copy in `40_Sources/`. The [Agent Skill](../skills/secondbrain/references/triage.md) lists what should and should not become cards.

## Starting a source

```sh
sb ingest <file>...                 # one or more files, anywhere
sb ingest --all                     # every new or unfinished inbox file
sb ingest <file> --prepare-only     # freeze and split only, no model call
sb ingest <file> --proposal-only    # produce and review, but do not write
sb ingest --retry <job>             # try the pending cards of a partial job again
```

`--executor host|api` overrides `[executor] default`, and `--max-rounds N` overrides the number of repair rounds for this run.

## The steps

| Step | What happens |
|---|---|
| Freeze | The source bytes, the extracted text, the chunks, the catalog, the full taxonomy and the nearest cards are stored as the job's input, with a hash. Only UTF-8 text is read unless a converter is configured. A source over `max_source_bytes` is refused. |
| Chunk | The text is cut into exact consecutive slices of about `chunk_chars` characters, at headings and blank lines first, then at sentence ends. |
| Route | Each paragraph of a chunk is searched against the existing cards. The domains of the best matches become the chunk's candidate domains. The best matches for the whole chunk, then the best three of each paragraph (40 at most), become its neighbours. |
| Produce | The producer gets the chunk, all domains with their guiding questions, the full taxonomy of the candidate domains (with card counts per topic), the neighbours and the cards already proposed for other chunks of the same source. It returns cards, proposed new topics and a coverage table. |
| Check | Code validates the answer when it is submitted (list below). Errors go back to the producer; an invalid answer uses the chunk's repair round. |
| Review | The reviewer gets the chunk, the cards without the producer's reasons, the three closest existing cards for each new card in full, and the taxonomy. It decides `accept`, `revise` or `duplicate` for each card, `accept` or `revise` for each new topic, and whether the coverage table is right, each with a reason. |
| Repair | Cards marked `revise` go back to the producer with the reviewer's findings, once by default. Accepted cards are kept. |
| Gate | A card is written only if a reviewer in a different run accepted it. Cards still rejected become pending with the reasons. A card filed under a rejected new topic also becomes pending. |
| Diagram | Accepted cards go through the diagram step ([diagrams.md](diagrams.md)). |
| Write | One transaction (below). |
| Commit | With `[git] commit = true`, the written paths are committed. A failed commit is recorded and does not undo the write. |

### What the check rejects

- a quote in `source_quotes` that is not a verbatim part of the chunk (the error shows the closest passage);
- a domain, pillar or topic that is not in the taxonomy, unless it is a valid new-topic proposal under an existing pillar;
- a new topic that repeats or merely renames an existing one, more new topics than allowed per source, or a proposed topic no card uses;
- a relation type that is not in the catalog, or a target that is neither an existing card nor a card of this source;
- two relations that point opposite ways: a type and its inverse from one card to the same target, or two cards that each claim the same type of the other;
- a card with a `contrasts_with` relation that does not state the contrast in `confusions`;
- a duplicate slug, a title that cannot be a file name, or a title that an existing card already has;
- fewer than two or more than four question-and-answer pairs, or `::` inside them;
- a card id written into the text (cards refer to each other by `[[Title]]`);
- a card that is missing from the coverage table, or a coverage row whose quote is not in the chunk.

## What is written

In one transaction:

- each accepted card, as `10_Knowledge/<domain>/<title>.md`, rendered by code from the reviewed fields;
- inverse relations on existing cards, in the front matter and in the related section (the rest of the card stays as it is);
- accepted new topics in the taxonomy file;
- a copy of the source in `40_Sources/<first 16 hex of sha256>/<file name>`;
- `_data/manifest.jsonl`, the inbox registry and `_data/notes_registry.json`;
- the MOC pages of the affected domains and the entry page `HOME.md`;
- with `after_ingest = "move"`, the removal of the inbox file (its bytes are in the source copy).

Relations that point to a pending card are left out and listed in the result as `skipped_relations`.

## Jobs and recovery

All progress is stored in the job, outside the vault. After an interruption, run `sb job list` and `sb job show <job_id>`, then continue with `sb job advance <job_id>`.

```sh
sb job list
sb job show <job_id>
sb job advance <job_id> --executor host
sb recover <job_id> --rollback      # restore every file the job wrote, byte for byte
sb recover <job_id> --resume        # finish a write that was interrupted
sb job reset <job_id> --reason "<why>"   # set aside a stuck job that never wrote
```

Before writing, the transaction compares each file with the bytes it planned against. If another program changed a file in between, the job stops as `needs_attention`, and the next `sb job advance` plans the write again from the same reviewed cards. Several sources can be in progress at the same time; writes to one vault happen one at a time under a lock.

A rolled-back source can be ingested again.

`sb ingest --retry <job>` runs the pending cards of a partial job as a child job, `<job>-retry1` and so on, with its own transaction. Roll back a retry child before its parent: while the child's writes are in the vault, `sb recover <parent> --rollback` refuses and names the child. A child that was rolled back or reset does not block a new `sb ingest --retry <parent>`; the new child takes its id.

## Other commands for an existing vault

These commands use the same produce, review and gate pattern on cards that are already in the vault.

| Command | Does |
|---|---|
| `sb refresh` | previews the MOC pages that would change; `--apply` writes them |
| `sb classify --domain <slug>` | proposes pillar and topic for cards that have none (`--repair-invalid` for cards whose classification is no longer legal) |
| `sb backlog` | runs classification over a whole domain or vault in batches |
| `sb curate --domain <slug>` | proposes changes to the pillars and topics of existing domains |
| `sb relocate --source-job <job>` | moves cards to another domain when a classification review suggested it |
| `sb domcreate --domain <slug> ...` | proposes a new domain from cards that already exist |
| `sb resume --job <job>` | continues one of these jobs |
| `sb docs --apply` | regenerates the entry page `HOME.md` (and the generated blocks of `[layout] entry_docs`) |
| `sb today --apply` | writes the review page of the current review day under `50_Reviews/`: the due and new cards of the review registry, the same queue that `sb review queue` gives during that day. A review day starts at 04:00, so a page made at 01:00 is still for the day before. It does not change `HOME.md`. Needs the `review` extra |
| `sb daily --apply` | does both, and `HOME.md` links the new page; this is the command HOME names when the page is missing. Without the `review` extra it writes `HOME.md` only and reports `review_extra_missing` |

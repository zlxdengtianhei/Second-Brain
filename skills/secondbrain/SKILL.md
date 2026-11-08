---
name: secondbrain
description: Turn new material in an Obsidian vault's inbox into reviewed atomic cards with the `sb` tool, using fresh sub-agents for producing and for reviewing. Use when the inbox has new files, when the user asks to make cards from notes, articles, transcripts or lecture notes, asks to process or tidy the knowledge vault, or asks to write the cards of one map up as an article.
---

# SecondBrain: inbox to reviewed cards

You coordinate. Sub-agents write and review the cards; the `sb` program checks every answer, runs the review gate and writes the vault in one transaction. All `sb` commands print JSON. Exit codes: 0 ok, 2 input error, 3 conflict, 4 executor failure.

The instructions for producers and reviewers live in the task files that `sb` writes. Do not rewrite them, summarize them, or add your own opinion of the material to what a sub-agent gets. A sub-agent gets only the short text from `references/subagent-producer.md` or `references/subagent-reviewer.md`, filled in with the values from a hand-off.

## Before you start

Your own `sb` must find the vault. It uses, in this order: `--vault <folder>`, the `SECONDBRAIN_VAULT` environment variable, then `vault` in the config file. The config file is `--config <file>`, else `$SECONDBRAIN_CONFIG`, else `$XDG_CONFIG_HOME/secondbrain/config.toml` (usually `~/.config/secondbrain/config.toml`). Run `sb status` once; if it prints `No vault`, ask the user which folder is the vault and pass it with `--vault` on every command. You do not need to pass anything to sub-agents: every `submit_cmd` already names the vault, the config file and the program by absolute path.

## 1. See what is waiting

```sh
sb inbox list
```

Each item has `status` (`new`, `in_progress`, `partial`, `done`, `skipped`, `failed`) and a `job_id`. A `done` or `partial` source whose file has moved to the sources folder is listed with `in_inbox: false`. First continue `in_progress` items with section 4 (`sb job advance <job_id> --executor host`). A `partial` item wrote some cards and kept others pending; retry it as described in section 5. A `failed` item has a `reason`; report it.

For each `new` item decide with `references/triage.md`:

- knowledge material → make cards (section 2);
- not knowledge (admin papers, empty files, images without text) → `sb inbox skip <name> --reason "<why>"`;
- not sure → `sb inbox show <name> --head 60`. If still unclear, list it for the user and go on with the other items.

## 2. Start a source

```sh
sb ingest <path> --executor host
```

When the user has said where a source's cards belong, add `--domain <slug>`, and `--pillar "<pillar>"` and `--topic "<topic>"` when they named them (the topic must already be in that pillar's `topics` in the taxonomy file; the user adds it there first). Producers then file every card there unless its subject has its own topic elsewhere, and reviewers check it. Without a placement, producers choose per chunk, and one subject can end up in several domains and topics.

This freezes the source and splits it into chunks. It prints a JSON **list** with one job object per source (also for one source). In each job object read `job_id`, `status` and `handoffs`: one hand-off per chunk, each with `role`, `step`, `chunk`, `packet_path`, `output_path` and `submit_cmd`. Several sources can run at the same time.

## 3. Hand off every packet to a fresh sub-agent

For each hand-off start a **new** sub-agent (no shared history with you or other sub-agents):

- `role: producer` (steps `produce`, `repair`, `note`, `split`, `diagram`) → text from `references/subagent-producer.md`;
- `role: reviewer` (steps `review`, `source_check`, `dispute`, `split`) → text from `references/subagent-reviewer.md`. When the host lets you choose, use a model from a different family than the producer. Choose the text by `role`, not by `step`: a `split` step comes with either role. When the host offers only one family (for example, all sub-agents run models of one vendor), still start a new sub-agent for each review: `sb` accepts it in host mode, marks the job `same_family: true`, and you report that (section 5).

Fill in the three values from the hand-off (`packet_path`, `output_path`, `submit_cmd`) and nothing else. The sub-agent submits its own answer with `sb job submit`; `sb` checks the answer's form on the spot and returns errors to the sub-agent, not to you. Diagram answers are also checked card by card at the next `sb job advance`: a diagram that fails comes back as one rewrite hand-off (chunk `dg01.r1` …, section 4), which goes to a new sub-agent like any other. `submit_cmd` starts with the absolute path of the `sb` program you are running, so it works in a sub-agent shell that does not have `sb` on its `PATH`. The sub-agent must be able to read and write files and run that command on the same machine.

## 4. Advance until the job is written

```sh
sb job advance <job_id> --executor host
```

It runs every deterministic step that can run and prints **one** job object. Its `handoffs` are the next tasks:

- context-note calls, only when `[ingest] context_note` is `summary` or `concepts` (chunk `nt-c02` …, `nt-concepts`; step `note`);
- reviews, and up to two repair rounds for rejected cards (chunk `c01.r1`, `c01.r2` …);
- the merge check (chunk `xc01`, step `source_check`), when the accepted `distinct` cards come from two or more chunks or a card extends an existing card: one reviewer looks at the cards of the source together, merges the ones that repeat each other, links cards that belong together, merges repeated new topics, and composes the patch for each existing card that a new card extends;
- after a dispute of the merge check, the chunk reviewer judges that card again (chunk `xr1-<slug>`, step `dispute`), and then the merge check runs again (chunk `xc01.r1` …);
- split slots (chunk `sp01`, `sp01.r1` …, step `split`), when a patch would make an existing card too long: a producer rewrites each card that extends it as a narrower one, and a reviewer checks it against the existing card;
- the diagram request for the accepted cards (chunk `dg01` …), and at most one diagram rewrite (chunk `dg01.r1` …).

Every one goes to a new sub-agent as in section 3, chosen by `role`. The first-round producers of a source all run at once and see no other chunk's cards; the merge check is what keeps two chunks from writing the same card. When `handoffs` is empty, read `status`. Repeat sections 3 and 4 until `status` is `applied` or `partial`.

Then `sb` writes the cards, a copy of the source, the registries and the maps in one transaction. By default (`[ingest] after_ingest = "move"`) the inbox file then leaves the inbox; its bytes stay in the sources folder (by default `40_Sources/<hash>/<file>`), and `sb inbox list` shows it as `done` with `in_inbox: false`. With `after_ingest = "keep"` the inbox file stays where it is.

Useful while a job runs:

- `sb job show <job_id>`: state, pending items with reasons, run records;
- `sb job packet <job_id> --role producer|reviewer [--chunk c01]`: the next hand-off again (the same open run is returned until it is submitted);
- `sb job abandon <job_id> --run-id <run_id> --reason "<why>"`: give up a run whose sub-agent failed; the next `sb job packet` starts a new run.
- `sb job skip-source-check <job_id> --reason "<why>"`: when the merge check's sub-agents give no answer at all, give the check up once three runs were given up (`sb job abandon`) or answers rejected (`[review] max_invalid_retries` + 1). Answers that `sb job submit` refuses are counted by `sb` itself: after the third the check is given up and its run closed, with no skip needed, as for every step whose answers keep failing the checks (a refused submission past that budget closes the run; run `sb job advance` and go on). When the last answer fails only in some patches, `sb` keeps it patch by patch. After a skip the cards are written as the chunk reviews left them, without the merging and linking across chunks, and each card that extends an existing card is written next to it as needs-review. Report it to the user.

## 5. Report to the user

From the final state report:

- new cards: count and titles (`produced`);
- existing cards that took new points (`merged_into_existing`: the card, the cards that went into it, the new proposition numbers), and narrower cards written next to an existing card (`split`);
- cards still pending, with the reviewer's reasons (`pending`), and for a retry child whether it made progress (`retry_progress`);
- accepts the program turned into revises because the reviewer's note named a blocking problem (`notes_turned`);
- material that needs a new pillar or domain (`needs_taxonomy`), and new topics a reviewer refused (`refused_topics`);
- cards and passages that existing cards already hold (`duplicates`) and skipped passages (`out_of_scope`);
- what the merge check settled inside the source (`source_check`): cards not written because another card of the source holds or extends them (`merged`, with `into` and what was `carried` over), links it added between cards that belong together (`added_links`), cards it left without links and why (`alone`), and new topics filed under another new topic (`refiled_topics`). `status: skipped` means the check's answers kept failing (by itself in API mode, by `sb job skip-source-check` in host mode) and the cards were written as the chunk reviews left them. `source_check_refusals` lists every answer the program sent back;
- cards written as needs-review next to an existing card they extend, because that card could not take them (`needs_review`, with `with` and `not_merged`; `patches_held` names the existing cards whose patch failed the checks). They stay out of the review queue until the user sets `status: confirmed`; `sb status` counts all such cards and `sb status --needs-review` lists them;
- links that could not be written (`skipped_relations`: the card, the type, the target and the `reason`). With reason `target card was not written` the target is still pending and a retry adds the link when it writes it; with any other reason no retry will, so the user adds it by hand. A retry child lists every link carried to it once: under `restored_relations` (added, `linked_as` when the new card already links that card another way, `redirected_from` when the target turned out to repeat an existing card or was merged into another card) or again under `skipped_relations`;
- conflicts with existing cards (`conflicts`): what disagrees and with which card. The user settles them; `sb status` lists them until that card's text changes. Do not edit the existing card yourself;
- whether reviews were done by the producer's model family (`same_family: true` in `sb job show <job_id>`);
- inbox items you skipped, with reasons.

`sb status --owner-items` gives the user one list of everything that waits for them: needs-review cards, open conflicts, pending items, material without a place in the taxonomy, and article paragraphs that did not pass review.

A `partial` job wrote the accepted cards and kept the rest pending. `sb ingest --retry <job_id> --executor host` starts a child job for the pending items only (same hand-off loop as above); `sb inbox list` shows such sources as `partial`, also when the file already left the inbox (then `in_inbox` is `false` and `path` is the copy in the sources folder).

When a retry child ends `partial` too, its result says whether to go on:

- with a `hint` (`sb ingest --retry <child>`), it wrote cards or settled items; retry the child, not the first job. `sb ingest --retry` on an older job of the chain does not start anything; its `message` names the newest job.
- with `retry_progress.progress: false` and a `message`, it wrote no card and left as many items open as it was given. Stop retrying. Report the `pending` items with their reasons; a further retry helps only after the user changes what blocked them (for example the taxonomy).

`needs_taxonomy` passages may fit a new topic that another chunk of the same source added: the chunks are produced at the same time, so a chunk cannot use a topic its sibling is proposing. The retry sees the topic once it is written and can file them there without any change by the user (drill 3: all 16 such passages of one chunk). The user may also close the source with the items left open: `sb inbox skip <file> --reason "<why>"`.

`sb recover <job_id> --rollback` undoes a written job byte for byte if the user asks. A rolled-back source can be ingested again with `sb ingest`; `sb job reset <job_id> --reason "<why>"` sets aside a stuck job that never wrote. Roll back a retry child (`<job_id>-retry1` …) before its parent; after a retry child is rolled back, `sb ingest --retry <job_id> --executor host` on the parent starts a new child.

## 6. Write a map up as an article

When the user asks for an article from the cards of one map:

```sh
sb export --domain <slug> --executor host
```

It prints one job object with `handoffs` for the writing, review, repair and smoothing steps. Give each to a new sub-agent as in section 3, chosen by `role`. When they have submitted, run `sb export --job <job_id> --executor host` for the next hand-offs, until `status` is `complete`. Use `sb export --job`, not `sb job advance`: only `sb export` writes the files. `files` names the article, its report and, with `--html`, the HTML page. Report the paragraphs that did not pass review (`report.flagged`; the article marks them) and the cards the article does not cite (`report.unused`). If `sb export` stops because the formula check cannot run, show the user its message (it says how to install the check) and ask whether to run again with `--no-formula-check`.

## Stability

- All progress is in the job directory. After an interruption or a context compaction, run `sb inbox list` and `sb job list`, then `sb job show <job_id>` for unfinished jobs, and continue with section 4.
- If you were interrupted while sub-agents worked, `sb job advance <job_id> --executor host` (or `sb job packet`) hands out the same open runs again, with the same `output_path` and `submit_cmd`. When a sub-agent you started has already written its answer to `output_path` but did not submit it, run that `submit_cmd` yourself with `<model id>` set to the model you started the sub-agent with; do not change the answer. If `sb` rejects it, or you do not know which model wrote it, give the same hand-off to a new sub-agent: it writes `output_path` again and submits. Use `sb job abandon` only to give a run up.
- A run id accepts one answer; a different second answer is refused.
- If you cannot start sub-agents, see `references/harness-notes.md` (API executor or headless sessions).

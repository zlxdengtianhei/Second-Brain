---
name: secondbrain
description: Turn new material in an Obsidian vault's inbox into reviewed atomic cards with the `sb` tool, using fresh sub-agents for producing and for reviewing. Use when the inbox has new files, when the user asks to make cards from notes, articles, transcripts or lecture notes, or asks to process or tidy the knowledge vault.
---

# SecondBrain: inbox to reviewed cards

You coordinate. Sub-agents write and review the cards; the `sb` program checks every answer, runs the review gate and writes the vault in one transaction. All `sb` commands print JSON. Exit codes: 0 ok, 2 input error, 3 conflict, 4 executor failure.

The instructions for producers and reviewers live in the task files that `sb` writes. Do not rewrite them, summarize them, or add your own opinion of the material to what a sub-agent gets. A sub-agent gets only the short text from `references/subagent-producer.md` or `references/subagent-reviewer.md`, filled in with the paths from a hand-off.

## 1. See what is waiting

```sh
sb inbox list
```

Each item has `status` (`new`, `in_progress`, `done`, `skipped`, `failed`) and a `job_id`. Continue `in_progress` items first (see section 5).

For each `new` item decide with `references/triage.md`:

- knowledge material → make cards (section 2);
- not knowledge (admin papers, empty files, images without text) → `sb inbox skip <name> --reason "<why>"`;
- not sure → `sb inbox show <name> --head 60`. If still unclear, list it for the user and go on with the other items.

## 2. Start a source

```sh
sb ingest <path> --executor host
```

This freezes the source, splits it into chunks and returns `handoffs`: one per chunk, each with `role`, `step`, `packet_path`, `output_path` and `submit_cmd`. Several sources can run at the same time.

## 3. Hand off every packet to a fresh sub-agent

For each hand-off start a **new** sub-agent (no shared history with you or other sub-agents):

- `role: producer` (steps `produce`, `repair`, `diagram`) → text from `references/subagent-producer.md`;
- `role: reviewer` (step `review`) → text from `references/subagent-reviewer.md`. When the host lets you choose, use a model from a different family than the producer. Same-family reviews are accepted in host mode but marked `same_family: true` in the job.

Fill in the four values from the hand-off and nothing else. The sub-agent submits its own answer with `sb job submit`; `sb` checks it on the spot and returns errors to the sub-agent, not to you.

## 4. Advance until the job is written

```sh
sb job advance <job_id> --executor host
```

It runs every deterministic step that can run and returns either new `handoffs` (reviews, one repair round for rejected cards, the diagram request) or the final state. Repeat sections 3 and 4 until `status` is `applied` or `partial`. Then the cards, the source copy, the registries and the maps are written in one transaction, and the inbox file is archived when the vault is configured to do so.

Useful while a job runs:

- `sb job show <job_id>`: state, pending items with reasons, run records;
- `sb job packet <job_id> --role producer|reviewer [--chunk c01]`: the next hand-off again (the same open run is returned until it is submitted);
- `sb job abandon <job_id> --run-id <run_id> --reason "<why>"`: give up a run whose sub-agent failed; the next `sb job packet` starts a new run.

## 5. Report to the user

From the final state report:

- new cards: count and titles (`produced`);
- cards still pending, with the reviewer's reasons (`pending`);
- material that needs a new pillar or domain (`needs_taxonomy`);
- duplicates of existing cards (`duplicates`) and skipped passages (`out_of_scope`);
- inbox items you skipped, with reasons.

A `partial` job wrote the accepted cards and kept the rest pending. `sb ingest --retry <job_id> --executor host` starts a child job for the pending items only (same hand-off loop as above); `sb inbox list` shows such sources as `partial`.

`sb recover <job_id> --rollback` undoes a written job byte for byte if the user asks. A rolled-back source can be ingested again with `sb ingest`; `sb job reset <job_id> --reason "<why>"` sets aside a stuck job that never wrote.

## Stability

- All progress is in the job directory. After an interruption or a context compaction, run `sb inbox list` and `sb job list`, then `sb job show <job_id>` for unfinished jobs, and continue with section 4.
- A run id accepts one answer; a different second answer is refused.
- If you cannot start sub-agents, see `references/harness-notes.md` (API executor or headless sessions).

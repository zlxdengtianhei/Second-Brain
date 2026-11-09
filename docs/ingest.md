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
sb ingest <file> --domain <slug> --pillar "<pillar>"   # file the cards there (also --topic)
```

`--executor host|api` overrides `[executor] default`, and `--max-rounds N` overrides the number of content repair rounds (`[review] max_repair_rounds`) for this run. The job keeps that number with its other settings ([Settings a job keeps](#settings-a-job-keeps)).

To say where the cards of a source belong, add `--domain <slug>`, optionally with `--pillar "<pillar>"` and then `--topic "<topic>"` (the MCP tool `ingest_prepare` takes the same three arguments). The placement is frozen with the job, and retries keep it; `--retry` together with a placement flag is an error. If the source was frozen earlier with another placement, the job keeps that one, and `sb ingest` (or `ingest_prepare`) returns a `placement_note` that says so. The placement domain comes first among each chunk's candidate domains. Producers file cards there, unless a card's subject already has its own topic in the taxonomy (an algorithm, a data structure, another language); reviewers send back a card filed elsewhere without that reason. A domain, pillar or topic that does not exist is refused, and the error lists what exists; `--topic` must already be one of the pillar's `topics`. No command creates a topic: add its name under the pillar's `topics:` in `20_MOCs/_pillars/<MOC>.yaml`, or let `sb curate` propose it.

## The steps

| Step | What happens |
|---|---|
| Freeze | The source bytes, the extracted text, the chunks, the catalog, the full taxonomy, the candidate cards of each chunk, the review rules and the `[ingest]` settings are stored as the job's input, with a hash. Only UTF-8 text is read unless a converter is configured. A source over `max_source_bytes` is refused. |
| Chunk | The text is cut into exact consecutive slices of about `chunk_chars` characters, at headings and blank lines first, then at sentence ends. |
| Context note | When a source has two or more chunks, each chunk gets a note on where it sits in the source: the headings it starts under, the end of the chunk before, and the outline of the source ([below](#the-context-note)). |
| Route | Each passage of a chunk (the text between blank lines, with its heading) is searched against the existing cards. The domains of the best matches become the chunk's candidate domains. The 15 best matches of each passage become its candidate cards ([below](#candidate-cards)). A block longer than 400 characters with 7 or more lines and no blank line between them, such as a summary list, is searched line by line. |
| Produce | The producer gets the chunk and its context note, all domains with their guiding questions, the full taxonomy of the candidate domains (with card counts per topic), the candidate cards of each passage, the shelves of their topics and, in API mode, the cards already proposed for earlier chunks of the same source: there the chunks run one after another. In host mode `sb ingest` hands out every first-round producer task at once, so those producers see no other chunk's cards. It returns cards, proposed new topics and a coverage table. A coverage row says that a passage became a card, that existing cards hold all of it already (`same`, with their ids in `with`), that it is not worth keeping (`out_of_scope`), or that it needs a new pillar or domain (`needs_taxonomy`). A card may carry an `overlap_hint`, for example that it extends an existing card; it is a proposal, and the program never reads it for an outcome. |
| Check | Code first repairs the slips it can fix without writing knowledge text, then validates the answer when it is submitted (lists below). A card that still fails goes back to the producer alone; a problem of the whole chunk sends the whole answer back. An invalid answer is asked for again, up to `max_invalid_retries` times; it does not use a repair round. |
| Review | A reviewer in a different run gets the chunk and its context note, the cards without the producer's reasons, the cards of all other chunks of the source, the existing cards to compare each card with ([Candidate cards](#candidate-cards)), and the taxonomy. It decides `accept` or `revise` for each card and each new topic, and whether the coverage table is right, each with a reason. A `same` coverage row is right only when the existing cards it names hold every point of the passage. For each accepted card the reviewer also says how much it repeats the vault: `same`, `extends` or `distinct`, with `with` naming the existing cards ([How much a card repeats](#how-much-a-card-repeats)). Before deciding on a card it writes the one question the card answers (`question`) and checks each proposition and review card against it. With an accept it can make mechanical fixes and write notes, each of a kind ([Review rules](#review-rules)). It does not ask for a revision only because another chunk makes the same point; the merge check settles that. It sends back a `depends_on` or `required_by` that makes a card about a library, tool or language feature a prerequisite of the idea it implements, or of a card about another language: such cards use `similar_to` or `contrasts_with`. Producer and reviewer can flag `conflicts`, where a new card disagrees with an existing one. Each sentence of a card's essence, intuition and confusions must be supported by the chunk, or say only what a card it names by `[[Title]]` has written; a fact with neither source is a `fact` note, and the producer is told the same. |
| Repair | Cards marked `revise` go back to the producer with the reviewer's findings, for up to `max_repair_rounds` rounds (2 by default). Accepted cards are kept. The producer sees the cards of all other chunks here too. The round also sees what earlier rounds settled without a card (passages that existing cards hold, passages out of scope, as `already_settled`) and does not write those again. When the reviewer refused a new topic, the accepted cards under it come back as `refile_cards`; the producer may file them, with the same slug, under a renamed topic or an existing one, and they are reviewed again. A rejected card that the repair splits or drops does not stay pending; the coverage verdict of the later round decides. The reviewer of a repair round checks whether each earlier finding is fixed ([Review rules](#review-rules)). |
| Gate | A card is written only if a reviewer in a different run accepted it. An accept with a note of a blocking kind counts as a revise. Cards still rejected after the last round become pending with the reasons. A card that is still filed under a rejected new topic after the repair also becomes pending. |
| Sort | The program sorts the accepted cards by the reviewer's level: a `same` card is not written and is listed under `duplicates`; an `extends` card goes into the patch set of its existing card; a `distinct` card goes on to the merge check. |
| Merge check | One reviewer judges the cards of the source together: the `distinct` cards against each other, the new topics, and one patch for each existing card that cards of the source extend ([below](#the-merge-check)). It runs when the `distinct` cards come from two or more chunks, or when a card waits for its patch. |
| Split | When a patch would take an existing card past 12 propositions or 8 review cards, the new card is rewritten as a narrower card in a split slot and reviewed again ([below](#a-card-that-extends-an-existing-card)). |
| Diagram | Accepted cards go through the diagram step ([diagrams.md](diagrams.md)). |
| Write | One transaction (below). |
| Commit | With `[git] commit = true`, the written paths are committed. A failed commit is recorded and does not undo the write. |

### What the check rejects

- a quote in `source_quotes` that is not a verbatim part of the chunk (the error shows the closest passage);
- a domain, pillar or topic that is not in the taxonomy, unless it is a valid new-topic proposal under an existing pillar;
- a new topic that repeats or merely renames an existing one, more new topics than allowed per source, or a proposed topic no card uses;
- a relation type that the card's domain may not use (`relation_types` in the catalog are open to every domain; a type listed only under `domain_relations.<domain>` is open to that domain only), a link whose back link the target's domain may not use, or a target that is neither an existing card nor a card of this source;
- two relations between the same two cards, counting the back links the program writes;
- two relations that point opposite ways: a type and its inverse from one card to the same target, or two cards that each claim the same type of the other;
- a card with a `contrasts_with` relation that does not state the contrast in `confusions`;
- a duplicate slug, a slug whose card id an existing card already has (`spacing-effect` when `kc_spacing-effect` exists), a title that cannot be a file name (a card's file is `<title>.md`: at most 120 characters and 200 bytes in UTF-8, so 66 Chinese or Japanese characters; a longer title is sent back to be shortened), or a title that an existing card already has;
- fewer than two or more than four question-and-answer pairs, or `::` inside them;
- a card id written into the text (cards refer to each other by `[[Title]]`);
- a card that is missing from the coverage table, or a coverage row whose quote is not in the chunk;
- a value of the wrong JSON type anywhere in the answer, such as an object where a name must be a string ([below](#answers-of-the-wrong-shape)).

Two titles, or two topic names, clash when they are the same text in the sense of [What counts as the same text](#what-counts-as-the-same-text).

### What the program repairs before the check

Before the check, the program repairs the slips it can fix without writing any knowledge text, by a fixed table of rules in this order, and records each repair (`program_repairs` in the job result; the reviewer's packet shows them too). It does so in one place for every role that writes cards: a chunk's answer gets every rule; a split card gets `title`, `card_id` and `quote`, and its relations are left to its own check, which asks for a typed link to the card it narrows:

| Rule | Repair |
|---|---|
| `title` | a character a title may not hold is written full width (`/` becomes `／`); links to the card in the answer follow |
| `card_id` | a card named by its id in the text (`kc_lantern-queue`) is named by `[[Title]]`, when the id is an existing card or a card of the answer |
| `quote` | a quote that is a slice of the chunk once `**`, `__` and spaces are set aside, there exactly once and 8 characters or longer, becomes that slice of the chunk; nothing else is matched |
| `relation_target` | a relation to a card that is neither an existing card nor a card of this source is dropped |
| `relation_pair` | of two or more relations between one pair of cards (back links counted), the one first in the catalog's order of types is kept |
| `relation_direction` | of two relations that point opposite ways, one is dropped: on one card the one later in the catalog's order, between two cards the one on the card later by slug |

The essence, the propositions, the intuition, the confusions and the review cards are compared before and after, both written in one form first (a card id that names a card as its `[[Title]]`, a title full width where it must be), so a card that names another by `[[Title]]` in one place and by id in another is repaired, not taken for a change. A card id is rewritten only where it stands alone, with no ASCII letter, digit or underscore right before or after it (`kc_queue_size` and `xkc_queue` stay as they are; in `与kc_queue不同` the id is found, as Chinese text sets no space around it); a link whose target is a card id (`[[kc_queue]]`, `[[kc_queue|the queue]]`) becomes a link to its title, its text and heading kept; of two renamed titles where one begins the other, the longer is tried first; and what a repair writes is not read again, so a title that holds an id stays as it is. A repair that changed them otherwise is a fault of the program: the answer is refused and counted, and the fault is listed for you ([Answers of the wrong shape](#answers-of-the-wrong-shape)). An answer is read the same way in API mode and through `sb job submit`: as JSON as it is, else without one Markdown code fence around it (```` ```json … ``` ````, `fence`), else its JSON object alone when words stand before or after it (`around`); the run records which, and `program_repairs` lists it. Nothing else is loosened: an answer written with a literal `\n` for its line breaks is still not JSON.

### A card that fails goes back alone

When what the check finds names only some cards, the answer is refused for those cards alone, and the cards that pass are kept. The refusal counts once against the slot's `max_invalid_retries`, however many cards it names; the count is kept in the job, so a job run again goes on from it. The next ask shows the failing cards as they were given, with their error lines (`fix` in the packet: `cards`, `problems`, `kept`), and asks for those cards only; the answer is put back with the kept cards and checked as a whole. When the slot's last answer still has failing cards, the cards that pass go on to review and the others wait for you with their lines and their text (`pending`, with `card`; their coverage rows, and a new topic only they used, go with them and are recorded as `held_back`). A problem of the whole chunk, in the coverage table or the new topics, refuses the whole answer as before. Host mode works the same way: `sb job submit` repairs, keeps and refuses alike, and the sub-agent answers again with the failing cards only.

## Candidate cards

Every role that compares new cards with the vault gets its existing cards from one function: the producer, the chunk reviewer, the merge check and the split slot. A candidate is only a card to look at. Whether a new card repeats it is the reviewer's judgement, never a score.

Each candidate row names its route in `via`:

| `via` | The card |
|---|---|
| `named` | The producer named it: a relation target, a conflict, an `overlap_hint` or a `same` coverage row. |
| `written` | In a retry: a card that an earlier job of the same source wrote. |
| `words` | A search hit: one of the 15 best cards of a lexical (BM25) search over the whole vault. |
| `hop` | A card that a search hit links. Off by default (`candidates_hop = 0`). |
| `shelf` | A card filed under the same topic, at most 25 per topic. |

A card is shown at one of four views. Each view holds the one before it:

| View | Holds |
|---|---|
| `title` | the id and the title |
| `brief` | also the domain, pillar, topic and the essence (cut at 300 characters) |
| `full` | also every proposition and every review card with its answer |
| `body` | the whole card body (only `sb export` uses it) |

A card whose propositions are not numbered `P1`, `P2` ... or whose review cards have no `::` shows the start of its body at `full`, marked `format: legacy`.

**One budget.** In one packet, the views of the retrieved cards take at most `candidates_chars` characters (40,000). Each existing card is shown once, however many new cards it is near. The cards are packed in the order of the first table: named, written, search hits, hop, shelf. Named cards keep their view and do not count. When the others are too long, the lowest-ranked card at the highest view loses one level (`full` to `brief`, `brief` to `title`), one card at a time, so the best-ranked cards keep the most. When every card is at `title` and the packet is still too long, the lowest-ranked titles are left out. So a shelf loses its titles before any search hit does. When the named cards alone take more than twice the budget, the chunk is split in halves and produced again.

What each role gets:

| Role | Cards | Views |
|---|---|---|
| Producer | for each passage, its 15 search hits; the shelves of the topics those hits are filed under, in the order of their best hit, each with the best-ranked cards first | hits `brief`, shelves `title` |
| Chunk reviewer | for each new card: the existing cards the producer named, its 15 search hits and the shelf of the card's topic; in a retry, also the cards earlier jobs of the source wrote; under `#coverage`, the cards the coverage rows name | named cards `full`, never lowered; written cards `full`; hits `brief` (`review_words_view`); shelf `title` |
| Merge check | for each card, its 15 search hits, only to add links; each card lists them by id, closest first | `brief`, lowered within the budget |
| Split slot | the existing card the new card narrows | `body` for the producer, `full` for its reviewer |

Why these values:

- **15 hits for every role, at every vault size.** On vaults of 197 to 8,738 cards, 15 was the smallest count after which five more cards found less than 2 more points of the real repeats, at every size. With 15, the search found 95.9% of them.
- **No second route.** The cards that the hits link found no more repeats than a longer search did.
- **The whole vault.** Keeping only related domains found 0.68 points fewer repeats and did not read less. About 12% of the top 15 hits were outside the related domains, and some of them were real repeats.
- **Long lists line by line.** In a summary chapter whose items had no blank line between them, the cards the producer named for the items were among the first 5 hits for 52% of the items when each block was one query, and for all of them when each line was a query.
- **40,000 characters.** With 20,000, the reviewer found 3 points fewer injected errors. With 80,000, it found no more.
- **Named cards whole.** The reviewer checks a claim that an existing card already holds a passage against that card. Before, over eleven real jobs, the reviewer saw 6 of the 71 cards that producers named.
- **Search hits `brief` for the reviewer.** An Opus reviewer found about 1 point fewer injected errors with `brief` than with `full` (62 of 80 against 63 of 80), and read about 12% less. A DeepSeek Flash reviewer found about 6 points fewer. When a Flash-class model reviews, set `review_words_view = "full"`.
- **Shelves.** A shelf shows what a topic already holds, for filing a card and for finding a repeat under the same topic. It costs about 25 titles per topic. Its effect was not measured; it is on because it is cheap.

## How much a card repeats

One scale with three levels is used wherever a repeat is judged. Every prompt that asks for it gets the same definition:

- `same`: the card holds nothing that the cards in `with` do not hold. Its knowledge may be spread over several of them.
- `extends`: the card answers the same question as the one card in `with` (the question its title names), or one of its sub-questions. It adds details, examples, cases or the answer to a sub-question. Merged into that card, the card still answers one question, and every proposition it adds answers that question.
- `distinct`: the card answers another question, also when it is about the same things (which tools there are, and how to choose among them, are two questions). Merged into the card in `with`, that card would no longer answer one question.

More examples or cases on the same question are not a unit of their own: they are `extends`.

**One final judge per value.** Each value that decides what is written has one role that gives it:

| Value | Final judge |
|---|---|
| a card's verdict (`accept` or `revise`) and its level against the vault (in a retry, also against the cards the source wrote) | the chunk reviewer |
| a `distinct` card's level against the other cards of the source; a new topic that is the same as another; the patch of an existing card; a dispute | the merge check |
| a split card's verdict, and that it does not restate its existing card | the split reviewer |
| whether a patch fits in 12 propositions and 8 review cards | the program |

The producer only proposes. What becomes of an accepted card:

| Level | Against the vault (chunk reviewer) | Against the source (merge check) |
|---|---|---|
| `same` | not written; listed under `duplicates` | not written; its links go to the first card in `with` |
| `extends` | goes into the existing card ([below](#a-card-that-extends-an-existing-card)) | what it adds goes into the card in `with` |
| `distinct` | goes on to the merge check | written, with links |

**The decision record.** Each value is appended to the job's decision record (`decision_record` in the job result), with the role, slot and run that gave it. Only the final judge gives a value again, and only for a reason that the job shows: a repair round, a refile under another topic, or a dispute. Any other second value is an error. The write step reads a card's place, relations and outcome only from this record, and `outcomes` in the job result gives the outcome of each accepted card. Before, the same repeat was said in five ways and could end differently by step, and a later step could replace an earlier step's value without a trace.

## A card that extends an existing card

A card that the reviewer finds `extends` an existing card is not written as a new card. What it adds goes into the existing card, as one patch per existing card that the merge check composes:

- new propositions get the next numbers;
- a fuller wording can replace one proposition, by its number;
- new review cards go after the old ones; an old review card can be swapped for a fuller or clearer question and answer (`flashcard_replace`, by its number `Q1`, `Q2` ...), whose answer keeps every fact of the old one; the review schedule is kept by card, so the card keeps its progress (only that question's own item history detaches, [review registry](review-registry.md));
- the source passage is added to `sources`, with the numbers of the propositions it backs;
- the relations of the new card move to the existing card.

Every other byte of the existing card stays. The program refuses a patch that would change anything else. The merge check first writes in the patch's `question` the question the existing card answers, and puts in only what answers it; the chunk reviewer's `question` for each card that extends it is in its packet. A card whose propositions answer another question, even about the same things, is disputed: it is judged again and written as a card of its own.

Example: the vault has the card "Lantern queue" with P1 to P4 and three review cards. A new source has the card "Lantern queue order": lanterns leave the queue in the order they were lit, with an example. The reviewer judges it `extends` "Lantern queue". The merge check composes the patch: the example becomes P5, and one new review card asks for the order. "Lantern queue order" is not written, and links to it point to "Lantern queue".

**Too long: the split slot.** When the patch would leave the existing card with more than 12 propositions or more than 8 review cards, the existing card does not change (a review-card swap of the patch is not made either: the job result lists it in `replacements_not_made`). Each new card that extends it goes to a split slot of its own (`sp01`, `sp02` ...), with only what that card adds: the propositions, review cards, replacements and `already` rows of the patch that come from it. There the producer rewrites it as a narrower card that holds only what the existing card lacks (how it behaves in one case, or one complete example), with a `part_of` or `example_of` relation to the existing card. A card whose propositions the existing card already says, every one of them (`already`), gets no slot: when it carries nothing else it is not written, and links to it go to the existing card; when it carries review cards, they go into the existing card as a patch of their own, and when they would take it past 8 review cards that patch is held (the card is written next to it as needs-review and listed in `patches_held`). The program asks again for a split card that has a proposition with the same text as one of the existing card's. When two split cards of the same existing card say the same point, the later slot's card is asked again. Nothing is cut. A split reviewer in another run checks that the card is faithful, readable on its own and one unit. For each proposition of the card it also names, in `matches`, the proposition of the existing card that says the same thing in any words, or `null`. When it accepts a card with a match, the program sends the card back to be written again without that proposition. A split card that is sent back is rewritten in a repair round (`sp01.r1` ...) while `max_repair_rounds` lasts; then it stays pending. The same happens when, inside one source, a card that others merge into would pass the limit: the card that extends it goes to a split slot, and the card it extends stays as it is.

**Written next to the card instead.** The new card is written next to its existing card, with `status: needs-review` and `review_reason: [possible_repeat]`, when:

- the existing card is not in sb's form (its propositions are not numbered `- P1.`, `- P2.` ..., or a line of its review cards has no `::`), or it is gone;
- its patch still failed the program's checks in the merge check's last answer ([below](#the-merge-check)), or the merge check was given up;
- `[ingest] merge_into_existing` is `false`.

The job result lists such a card under `needs_review`, and `not_merged` says why.

A patch changes the text of the existing card. So its open conflicts are recorded again with the new text: a merge does not settle a disagreement.

Why: before, such a card was written next to the existing card as needs-review, and you merged the two by hand. In a test of 72 accepted cards, the new rule put 40 into their existing cards, split 11, and left none for review; the earlier rule left 53 for review. In a later test, each split slot was given the whole patch: two slots wrote the same narrower card, and what two other cards added was lost. That is why a slot now gets only its own card's additions. On a copy of a large vault, 96.8% of the cards (8,456 of 8,738) were in sb's form and could take a patch.

## The merge check

Each chunk is produced and reviewed on its own. So two chunks can each write a card on the same point, cards of different chunks that belong together are not linked, and two chunks can each propose a new topic for the same subject. Once every chunk has settled, and before the diagram step, one more reviewer hand-off (`chunk: xc01`, `step: source_check`) judges them together. It runs when the chunk reviewers found `distinct` cards in two or more chunks, or when a card waits for its patch.

It reads:

- the decision set: each card a chunk reviewer found `distinct`, sorted by domain, pillar and topic, with its relations, the cards of the source that link it, and its review notes as `[kind] text`;
- the patch set: each existing card that cards of the source extend, at `full`, with the cards that extend it;
- the new topics of the source, with the number of cards filed under each;
- the candidates of each card, only to add links: each card lists them by id, closest first, and one table shows each existing card once, within `candidates_chars`;
- the outline of the source, when the context note is on.

It does not read the chunk text: the chunk reviewers checked the content. The frozen packet reports its size under `packing`.

**The decision set.** Each card gets its level against the other cards of the source:

- `extends`, with `with` naming one card of the source: what it adds is copied word for word into `carry` (`{"propositions": [...], "flashcards": [{"q": ..., "a": ...}]}`) and added to that card, with its source passage. `replace` (`[{"proposition": "P2", "by": "<this card's proposition, word for word>"}]`) puts a fuller proposition in place of one of that card's, and `flashcard_replace` (`[{"flashcard": "Q1", "q": ..., "a": ...}]`) a fuller or clearer question and answer in place of one of its review cards. The card is not written. Links to it, in relations and in card text (`[[Title]]`, also with `#heading` or `|alias`), go to the card in `with`, and its own links move there, unless that card already links the target or the move would make a link to itself.
- `same`: nothing is carried. The card is not written, and its links go to the first card in `with`.
- `distinct`: the card is written. Its `links` can add relations it should have and does not have yet: to other cards of the source (by slug) or to its candidates (by id).

**The patch set.** For each existing card the merge check composes one patch (`{"target", "propositions", "flashcards", "replace", "flashcard_replace"}`), every item but a review-card swap copied word for word from the cards that extend it. A swap (`{"flashcard": "Q2", "q": ..., "a": ...}`) makes one review card of the existing card more complete or clearer: its answer keeps every fact of the old answer (when it cannot, the merge check adds a new review card instead), and when the source says the old answer is wrong, that is a conflict for you, never a swap. When a card has nothing to add, or what it would add answers another question, the merge check lists it in `disputes` with a reason. That card goes back to its chunk reviewer, who judges it again (slot `xr<n>-<slug>`, step `dispute`). Then the merge check runs again as a new round (`xc01.r1` ...), which replaces the whole round before. A card still disputed when `max_repair_rounds` are used up stays pending. So does a disputed card whose judgement again keeps failing the program's checks (`max_invalid_retries`), with the dispute and the last refusal.

**What the program checks in the answer:**

- **Every proposition and every review card is accounted for.** Each proposition of an `extends` card, and of the cards that extend an existing card, is carried, used in `replace`, or named in `already` together with the proposition that says it already (`{"text": "<word for word>", "as": "P3"}`). Each of their review cards is carried, swapped in (a `flashcard_replace` that asks its question), or named in `already` together with the review card that asks it already (`{"text": "<its question, word for word>", "as": "Q2"}`). An answer that leaves one out goes back, naming it. With the check before writing (below), no item of a card that goes into another is left behind without a word.
- **No point said twice.** In a patch, and in a carry into a card of the source, each carried proposition and question, and each replacement, is compared with the card it goes into and with the other items carried with it. In a patch, an item with the very same words as an item of the existing card (spaces aside) always goes back: name that item in `already`. An item that only looks the same ([What counts as the same text](#what-counts-as-the-same-text)), or a close wording, goes back too, unless the answer lists the pair in `differs` (`{"text": ..., "from": ..., "reason": ...}`) with why it is another point. `from` names a proposition (`P3`) or review card (`Q2`) of the card, or another item of the same patch. The message names both items. A review card that asks a question of the card, or of another review card carried with it, in the very same words but with another answer goes back too, naming both answers; `differs` may keep both. A review-card swap names a review card the card has, each one at most once, and its new pair is checked in the same way against the card's other review cards, the ones carried with it and the other swaps; a new pair that is one of them, question and answer, goes back. The program never leaves an item out because it looks the same: it asks again. It leaves out by itself only a repeat: a proposition with the very same words, spaces aside, or a review card whose question and answer both are, that a carry into a card of the source repeats, or that is carried twice. Before it writes a card that takes a patch or a carry, the program checks that every proposition, review card, replacement and swapped-in review card of it is in the card; when one is not, the job stops and names it, and nothing is written. The check is part of the two functions that put a patch into an existing card and a carry into a card of the source, so a path that writes a card cannot leave it out. The merge check is also asked to compare each carried proposition with the card's propositions one by one: one about the group of names or terms that a proposition of the card sums up goes in `replace` for that proposition, not next to it (the prompt says so; the program does not check it).
- **Judged by the question, never by size.** The merge check must not cut a patch to fit the limit. Past 12 propositions or 8 review cards, the program sends the card to a split slot.
- **Links:** a type open to both domains, one relation per pair, no link to a card that is not written. A card with no relation at all (`isolated: true`) must get a link, be linked by another card, or say in `alone` why it stands alone.
- **New topics:** each is `distinct`, or `same` with another new topic's id. In the second case its cards are filed under that topic (its domain, pillar and topic), and the topic is not added. When the job has a placement (`--domain`), the topic at the placement is the one kept.

An answer that fails these checks goes back to the merge check, up to `max_invalid_retries` times. The job result lists every answer that was sent back under `source_check_refusals` (slot, run and message).

The last answer the merge check may give is kept patch by patch. When it fails the checks only in some patches, or in the carry of some cards into a card of the source, the rest goes in. A held card takes nothing: the cards that extend it are written next to it as needs-review, and `patches_held` in the job result names each held card, the cards that extend it and the problems found. A card whose carry is held is not merged: it is written as a card of its own, needs-review, and `patches_held` lists it with `carry: true`. A run that ended in a transport error is no answer and does not count toward the last one. When the last answer also fails elsewhere, it is refused as a whole, and the check is skipped by itself. This holds in host mode too: an answer a sub-agent submits that `sb job submit` refuses counts as an API answer does, and once the check is given up its run is closed. When the sub-agents give no answer at all, run `sb job skip-source-check <job> --reason "<why>"`. It works only after `max_invalid_retries` + 1 rejected answers or given-up runs (3 by default), and below that it says how many there are so far. It abandons the open run and records the skip. The cards are then written as the chunk reviews left them, without merging or linking across chunks, and each card of the patch set is written next to its existing card as needs-review.

Why these checks: in a test, after a patch 14 of 41 synthetic existing cards and 7 of 8 replayed ones said a point twice; before the patch rule, 3 of 20 merges did. In another test with patches past the limit, the merge check cut 8 of 20 patches to exactly the limit, and 5 of them lost a proposition. In a third, one review card of one patch was close to a review card of its existing card: the whole check was skipped, and all seven cards that extended four existing cards were written next to them. Now only the card whose patch fails is held.

**In a retry**, the cards that earlier jobs of the source wrote are candidates of the chunk reviewer (`via: written`), and the reviewer judges each retry card against them on the same scale. A retry card that holds nothing new is `same` as a written card and is not written. One that adds to a written card `extends` it and goes into it. The merge check does not judge written cards.

Known limit: the program finds the same text and close wordings, but not every point restated in other words. For a split card, the split reviewer's `matches` is there for this: in a test, 1 of 23 split cards restated propositions of its existing card in other words (2 more came from slots that were given the whole patch, fixed since). For a patch, the merge check compares item by item, and you may still find a restatement when you review the card.

## The context note

Chunks are cut without overlap, and each is turned into cards on its own. So a chunk that starts with "the three kinds above", or under a heading two levels up, would be read without what it refers to. Overlapping the text would make one passage the business of two chunks, and the same card could be written twice. So each chunk gets a note, `context_note`, that the program takes from the structure of the source. Within `[ingest] context_note_chars` characters (2,000), it holds, in this order:

1. the headings the chunk starts under (at most 200 characters);
2. the last one or two paragraphs of the chunk before (at most 600 characters, whole paragraphs, else cut at the start of a sentence);
3. the outline of the source, three levels deep, with the chunk's section marked (the rest of the budget; past that, only the lines around the mark).

The producer, the chunk reviewer and the split slot get the note, marked "only for understanding: not quotable, covers nothing". Quotes are still checked against the chunk alone, so a quote taken from the note is refused. The merge check gets the outline once. A source of one chunk gets no note.

`[ingest] context_note` sets what the note holds:

| Value | The note |
|---|---|
| `off` | no note |
| `program` (default) | the program's note above; no model call |
| `summary` | also, for each chunk after the first, one cheap producer call (slot `nt-<chunk>`): what came before and what the chunk's references point at, at most 300 characters |
| `concepts` | also one producer call for the whole source (slot `nt-concepts`): its concepts with the chunks that explain them, at most 600 characters in the note |

A model note that keeps failing the checks is given up, and the chunk keeps the program's note. In host mode these calls are handed out before the chunks that wait for them.

Why `program`: in a test, no card at the start of a chunk was unreadable in any arm (0 of 171), so the test could not show that one arm is better. The program's note did not add repeats across chunk boundaries (8 pairs, against 10 with no note). The model summaries cost a call per chunk and showed no gain.

## Review rules

**Two budgets.** `[review] max_repair_rounds` (2) counts content repair rounds: a card sent back with a reason. `[review] max_invalid_retries` (2) counts answers that failed the program's checks and were asked for again, per slot. The two are counted apart, for every role that asks a model: the producer and reviewer of a chunk, the merge check, the judgement again after a dispute, the producer and reviewer of a split card, and a model note. The count is kept in the job state, so a job run again goes on from it: one `sb job advance` asks at most `max_invalid_retries` + 1 times, and never again after that; a host sub-agent's answers that `sb job submit` refuses count the same (a file that is not JSON, or JSON that is not an object, included), so does a run given up with `sb job abandon`, and the run is closed once the slot is given up. Every job kind that asks a model counts this way, at one gate: ingest, export, and the reviewer and producer of classification, curation, relocation and domain-creation jobs, which wait for you (`escalation_required`) once theirs is spent. Then a chunk's cards, a split card, or a disputed card stay pending with the reason (the count and the last refusal); the merge check is skipped and a model note given up. A diagram answer that fails the checks is asked again once, in its rewrite slot. Before, one budget counted both, so a malformed answer cost a chunk its only repair round. Each role's repair budget is in the same table of roles, and one function says whether a round is left, for a chunk, the merge check's rounds after a dispute, a split slot, an export unit and a maintenance job.

**Mechanical fixes** (`[review] amend`, on by default). With an accept, the reviewer can fix in `amend` a problem that needs no new knowledge text, instead of sending the card back:

- `add_relation`, `retype_relation`, `remove_relation`: a missing relation, a relation of the wrong type or direction, a wrong relation;
- `refile`: file the card under a topic the taxonomy has;
- `drop_flashcard`: drop a review card the chunk does not support (two or more must stay).

The program makes the fixes and checks only that the result is legal: types open both ways, a curated topic, two review cards left, an `extends` card that still links its existing card. A fix that is not legal turns the accept into an ordinary revise. The job result lists every fix under `amended`, with the value before and after, and the merge check sees them.

**Focused review of a repair round** (`[review] focused_review`, on by default). The reviewer of a repair round gets the earlier findings (`previous_findings`) and says for each one whether it is fixed. It sends a card back only when a finding is not fixed, or for a blocking problem (below). Any other problem goes to a `minor` note.

**Notes with a kind.** Each note a reviewer writes is `{"category": "<kind>", "text": "<what and where>"}`, with one of five kinds; a reviewer of a card has a sixth, `repeats`:

| Kind | Meaning |
|---|---|
| `fact` | a statement is wrong: the chunk (for an article, the cited cards) contradicts it or does not say it |
| `unreadable` | it cannot be understood without the source: it points at the source ("the example above") or leaves out what it relies on |
| `quote` | a source quote is not in the chunk word for word, or a card is cited for what it does not say |
| `not_a_unit` | two separate ideas in one card, or a fragment that does not stand on its own |
| `repeats` | (cards only) the card says one thing twice: two propositions, or two review cards, make the same point in other words; an essence, intuition or confusion that restates a proposition does not count |
| `minor` | wording, style, or something that could be better but is not wrong |

Each proposition must be right by itself. A proposition that, read on its own, says more than the text it comes from (it leaves out a condition, a limit or a case the text gives), says it wrongly, or cannot be understood is `fact` (`unreadable` when it cannot be understood), and the card needs a revise. That the card's essence or a review card still gives the condition is no reason to call the note `minor`. In a replay of the injected-error test, reviewers had noted such a missing condition as `minor` for that reason, and a judge found that 8 of 10 of those cards should have gone back.

The first four kinds, and `repeats`, are blocking. When a reviewer accepts a card and writes a note of a blocking kind, the program turns the accept into a revise, with the note as its reason. That revise uses a content repair round like any other. When the rounds are used up, the card is pending with that reason. Only `minor` notes leave the decision as it is. The split reviewer and the reviewers of `sb export` follow the same rule (an article's reviewer has the five kinds only). A note in the old form (a plain string), or with another kind, is refused and asked for again.

**One question, and the premises of an example.** A reviewer of a card writes in `question`, before its decision, the one question the card answers, the one its title names, and checks each proposition (`P1` ...) and review card (`Q1` ...) against it. Items that answer another question, one a reader could ask and have answered by a card of its own (which tools there are, and how to choose among them), make a `not_a_unit` note that names them, and the card goes back to have them split off or moved; the producers are told the same rule. An example or a worked calculation must give on the card the premises it uses (the rule, the coordinates, the code book, the input values); one that relies on premises only the source gives is `unreadable`. The `question` is kept in the review and shown to the merge check; it is not written to the card.

The job result has two lists, and `sb job show <job>` shows both:

- `notes_turned`: each accept the program turned into a revise: the chunk, the slot, the round (0 for the first review, n for the n-th repair round), the card, the kind and the note;
- `review_notes`: every note, with its kind.

Why: in two tests, reviewers wrote a real problem in a note and still accepted the card: 3 of 6 real problems in one test, 36 injected errors in the other. The wrong cards were written. Now the program acts on the note, without another model call.

The defaults come from a test of 1, 2 and 3 repair rounds, with focused review and mechanical fixes on and off. Every chunk that reached review settled within one repair round. With both switches on, 6 of 225 written cards (2.7%) had a real problem, and 1 of 7 mechanical fixes was wrong (a relation of the wrong type).

## Answers of the wrong shape

Every model answer, of every role, is first checked against the JSON schema it was asked for. A value of the wrong JSON type (an object for a name, a string for a link, a null where the schema allows none) is refused by name, for example `cards[0].links[0] must be an object, not str`. The answer is then asked for again, within `max_invalid_retries`. An error inside a check is also turned into a refusal that names it. So a wrong answer never stops `sb ingest`; before, some stopped the whole run with a `TypeError` or an `AttributeError` and left it open. Every answer is taken in one place, whatever its source (a host's answer file through `sb job submit`, the MCP tool `job_submit`, an API executor): it is read, its size checked (8 MB at most), the fields only the program writes taken out (`program_repairs`, `held_cards`, `held`, `held_carries`, `turned`, `amend_refused`, at the top and in each row, and `blocking` at the top; `program_fields` in `program_repairs`, and a redo round shows the producer its last answer without them), checked, stored, and its values recorded. Only a refusal a check raises on purpose for what the answer says is the answer's own; a field missing from the program's own input to a check is the program's. Any other error in these steps goes through the same gate: the answer is refused and counted against the slot's budget, a host run is closed when the budget is spent, and the error, which is the program's and not the answer's, is kept on the run and in the job and listed by `sb status --owner-items` (`kind: program_error`, with the step where it happened as `stage`). A maintenance job's review is checked when it is taken for covering the proposal it answers, each domain or item once. What every job kind does afterwards with the answers it took (a chunk's outcome, the packets built from them, the merge check, the split cards, the checks before writing, an export's plan and assembly, a maintenance job's review and apply) runs behind one guard: an error there never stops `sb job advance` again and again. When it can be laid on one answer (a slot's newest, or a maintenance round's candidate or review), that answer is set aside, counted like a refused one, and asked for again until the budget is spent; what it put in the decision record no longer counts. When it cannot, or while a write is under way, the job waits for you (`parked`) and the error is listed. The same error again (the same stage, type and message) after an answer was set aside for it is not the next answer's: the job is parked, and no further answer is set aside. A parking ends once an advance gets past it: the job, what the advance returns and the owner's list no longer show it. The packets a host asks for (`sb job packet`) are built behind the same guard, and so is a write the job resumes after it was cut off. A stop on purpose (a conflict with the vault, a review whose models may not judge each other, a write that would not hold what it carries, a formula checker that cannot run) still stops the job, and is listed too (`kind: job_stopped`).

Ingest answers are read as JSON as they are, else without one code fence around them, else as their JSON object alone when words stand around it, the same in API and host mode ([above](#what-the-program-repairs-before-the-check)). An answer that is not JSON is invalid and is asked for again. Only `sb export` reads raw TeX backslashes in an answer ([export.md](export.md)), in host mode too.

## What counts as the same text

Titles, new topic names, carried items and split cards are compared with one test. Two texts are the same when they differ only in case, character width, spaces and a closed table of marks: the sentence marks `, . ; : ? !` (with their CJK forms, and the CJK enumeration comma), quotes, the ellipsis and the em dash. Every other character counts: brackets, `# % & * @ \ _ ^ ~ + = < > | / -`, digits and letters. So "If a < b" and "If a > b" are different texts, and so are "5%" and "5", "C#" and "C", "x_1" and "x1".

Two exceptions keep a mark of the table: one between two digits (3.5, 3:2), and an English `!` or `'` right after a digit or a one-letter name (n!, f'). A formula (`$...$`, `$$...$$`) or inline code is compared as written, with its runs of spaces made one.

A finding of this test only sends an answer back, or warns; it never makes the program drop text. The test sets some characters aside, and some of them change what a text says: "1 KB" and "1 Kb", or "10³" and "103", can come out the same. So the program leaves out by itself only two items with the very same words, spaces aside, and asks again for everything else. Calling two texts the same could lose a point; calling them different at most keeps a repeat. So when in doubt, the test says "different".

## What is written

In one transaction:

- each accepted card, as `10_Knowledge/<domain>/<title>.md`, rendered by code from the reviewed fields. A card gets `status: needs-review` with its reasons in `review_reason`: `source_unclear` when the producer marked the source unclear and the reviewer accepted it, `possible_repeat` when it extends an existing card that could not take it. Such cards are listed in the result as `needs_review`; the review queue does not offer them as new cards until that status changes. `sb status` counts these cards (`needs_review`), `sb status --needs-review` lists them, and `HOME.md` names the count under "What to do now";
- the patches of existing cards that new cards extend: propositions, review cards, a source and relations, and nothing else in the card;
- inverse relations on existing cards, in the front matter and in the related section (the rest of the card stays as it is);
- accepted new topics in the taxonomy file;
- a copy of the source in `40_Sources/<first 16 hex of sha256>/<file name>`;
- `_data/manifest.jsonl`, the inbox registry and `_data/notes_registry.json`;
- the conflicts found, appended to `_data/secondbrain/conflicts.jsonl`;
- the MOC pages of the affected domains and the entry page `HOME.md`;
- with `after_ingest = "move"`, the removal of the inbox file (its bytes are in the source copy).

Formulas in a card's text are put between `$` by code, which also gives a bare macro name its backslash (`alpha` becomes `\alpha`). The name of a relation symbol used as a function name, directly followed by `(`, is written as an operator name: `sim(q, d)` becomes `\operatorname{sim}(q, d)`, not the relation `\sim`; `X \sim p` stays as it is. A binary operator before a group stays the operator: `w_i times(x_i + b_i)` becomes `w_i \times(x_i + b_i)`. `softmax`, `argmax`, `argmin`, `sigmoid` and `relu` are always operator names.

A relation to a card the reviewer judged `same` goes to the existing card it repeats. A relation to a card that went into an existing card goes to that card, and a relation to a card rewritten in a split slot goes to the split card. A relation that cannot be written is listed in the result under `skipped_relations`, with one of two reasons:

- `target card was not written`: the target card is still pending. The relation is carried to retry children, and a child that writes the target adds the relation, with its back link on the earlier card.
- `target card was dropped in review; add the link by hand if it still matters`: the relation is not carried.

In a retry child, each carried relation gets exactly one outcome. A written one is listed under `restored_relations`, with `target_slug`. Such a row has `linked_as` when the new card already links that card with another type, and `redirected_from` when the target was judged the same as an existing card (the relation then goes to that card and is written on both existing cards) or was merged into another card by the retry's merge check. Every other carried relation stays in `skipped_relations` with one of the two reasons above.

Conflicts are never settled automatically: you decide which card is right and fix the other. The job result lists the conflicts as `conflicts`, `sb status` lists every open one under `conflicts`, and `HOME.md` shows their count under "What to do now". A conflict stays open until the text of the existing card changes; back links added to that card do not count, and a patch records the card's open conflicts again. Each conflict counts once per source, card and existing card: when a retry records a conflict its parent recorded, the retry's record is the one shown.

### What the job result lists

Besides `produced`, `pending`, `needs_taxonomy`, `out_of_scope`, `new_topics`, `conflicts` and the relation lists above, the result of an ingest job has:

| Field | Holds |
|---|---|
| `outcomes` | the outcome of each accepted card: `write`, `patch`, `drop`, `merge_carry`, `merge` or `pending`; a card written next to its existing card has `write`, one its existing card already covers has `drop`, one that waits for you has `pending`. The program records what became of each card that goes into another (`outcome.program` in the decision record, with the reason), and `outcomes`, `patch_set`, `merge_set` and `pending` are read from that record, so they always agree |
| `decision_record` | every value of the job, with the role, slot and run that gave it |
| `duplicates` | the cards and passages that existing cards hold already, with `with` and `by` (`reviewer` or `producer`) |
| `merged_into_existing` | each existing card that took a patch: the cards that went into it, the new proposition numbers, the replaced propositions and review cards (`replaced`, and `replacements` with the words before and after), the number of new review cards, the moved relations and the merge check's `already` rows |
| `replacements_not_made` | each review-card swap that was not made because its patch or carry did not go in whole (it went to a split slot), with the new question and answer and the reason |
| `split` | each split card written: its id, the card it came from, the existing card it narrows and its slot |
| `split_restated` | each split card the program sent back because the split reviewer matched one of its propositions to the existing card: the slot, the round and the `matches` |
| `patch_set` | each card that extends an existing card, and what became of it: `patched`, `split`, `covered` (the existing card says all of it), `written` (next to the existing card) or `pending` |
| `merge_set` | each card of the source that was too large to merge into another card of the source: `split` or `pending` |
| `source_check` | what the merge check did inside the source: `merged` (with `carried` counts, and each replacement with its words before and after in `replacements`), `added_links`, `alone`, `refiled_topics`, `moved_relations` and `rewritten_links`, or `status: skipped` with the reason |
| `source_check_refusals` | every merge-check answer the program sent back, with the message |
| `needs_review` | the cards written as needs-review next to an existing card (or, their carry held, next to a card of the source), with `with`, the reason and `not_merged` |
| `patches_held` | each existing card whose patch failed the checks in the merge check's last answer: the cards that extend it and the problems; a card whose carry into a card of the source failed them (`carry: true`); review cards held because they would pass 8 (`review_cards_only: true`) |
| `amended` | every mechanical fix, before and after |
| `program_repairs` | every repair the program made to an answer, writing no knowledge text, by slot: a code fence or words around the JSON object set aside (`fence`, `around`, any role), for the chunk and split producers the rules of [What the program repairs before the check](#what-the-program-repairs-before-the-check), and what a slot's last answer left out (`held_back`) |
| `review_notes`, `notes_turned` | every review note with its kind; the accepts the program turned into revises ([Review rules](#review-rules)) |
| `refused_topics` | each new topic a reviewer refused, with the reason and the number of cards filed under it |

## What waits for you

`sb status --owner-items` lists in one place everything that waits for you, each item with its `owner_reason`:

| `owner_reason` | Item |
|---|---|
| `source_unclear` | a needs-review card whose source was unclear (also a card marked needs-review before reasons were recorded) |
| `possible_repeat` | a needs-review card written next to an existing card it extends, with why that card did not take it in `detail` |
| `conflict` | an open conflict with an existing card |
| `pending` | a pending item of the latest job of a partly written source; a paragraph of the latest export of an article that did not pass review (`kind: export_paragraph`, [export.md](export.md)); a maintenance job (classification, curation, relocation, domain creation) that stopped to wait for you, with the job, its kind, the step that stopped and the reason (`kind: maintenance_job`; only the newest job of each operation); an error of the program while it read or checked an answer, or read one it took, with the job, the slot, the role, the step, where it happened (`stage`), the error and how many times (`kind: program_error`; a job parked because no one answer is to blame has no slot or role); a job the program stopped on purpose, with the error and how many times (`kind: job_stopped`) |
| `needs_taxonomy` | a passage without a place in the taxonomy (`kind: passage`), or a new topic a reviewer refused, with its reason and card count (`kind: refused_topic`) |

The list is made each time from the vault and the jobs; nothing new is stored. `sb status` gives the counts under `owner_items`.

`sb status` also reports topic sizes under `topics`: the topics past one map page (25 cards), each with the `sb curate --domain <slug>` command for its domain; the number of curated topics that no card uses; and the number of cards filed under a topic their taxonomy does not list. For each topic past 25 cards, `sb curate` gets a proposal of how its cards group by the relations among them (`split_proposals`). It is only a hint: `sb curate` still decides, and no card changes. A group of fewer than 5 cards joins the group it links to most, or is listed as scattered; a group still past 25 cards says it needs a second round. The proposal needs the `curate` extra (`networkx`). On the 10 largest topics of a large vault, an independent reviewer found the proposed groups reasonable for 9.

## Settings a job keeps

When a job is frozen, it keeps the `[review]` rules (with `--max-rounds`), the `[ingest]` settings, the vault layout and the card language. Its steps read them from the job, not from the config file. So a change to the config file, or another `--max-rounds`, does not change a job halfway. Only how to run is read live: the endpoints and keys, the config file that a host hand-off names, git commits and the state folder.

A retry child keeps the `[ingest]` settings of its parent and takes the current `[review]` rules. A job frozen before a setting existed runs as that version did: the earlier counts of candidate cards (`candidates_k = "auto"`), no named cards in the review packet, search hits at `full`, no shelves, no merge into existing cards and no context note.

**Jobs of the earlier version** (`method_version: ingest/3` in the job's input) judged repeats on another scale, so this version does not continue them:

- A partial job: run `sb ingest --retry <job>`. The child is a job of this version. It reads its parent's records, runs to the end, and does not write the parent's cards again.
- A job that was still running: `sb job advance` refuses it and says the way out. Reset it with `sb job reset <job> --reason "<why>"`, then run `sb ingest <inbox path>` again. A retry child of the earlier version: reset it, then run `sb ingest --retry <parent>`.

A finished job of the earlier version is read with the rules it ran under (one repair round, whole review, no mechanical fixes), so `sb job show` shows what it did.

## Jobs and recovery

All progress is stored in the job, outside the vault. After an interruption, run `sb job list` and `sb job show <job_id>`, then continue with `sb job advance <job_id>`.

```sh
sb job list
sb job show <job_id>
sb job advance <job_id> --executor host
sb recover <job_id> --rollback      # restore every file the job wrote, byte for byte
sb recover <job_id> --resume        # finish a write that was interrupted
sb job reset <job_id> --reason "<why>"   # set aside a stuck job that never wrote
sb job skip-source-check <job_id> --reason "<why>"   # write without the merge check after it keeps failing
sb job prune --days 30              # preview old rollback journals; --apply deletes them
```

Before writing, the transaction compares each file with the bytes it planned against. If another program changed a file in between, the job stops as `needs_attention`, and the next `sb job advance` plans the write again from the same reviewed cards. Several sources can be in progress at the same time; writes to one vault happen one at a time under a lock.

A rolled-back source can be ingested again. A rollback also restores the existing cards that took a patch.

`sb recover <job> --rollback` needs the job's rollback journal (`transaction.json` in the job's state folder), and the tool never deletes it on its own. `sb job prune` previews, with sizes, the journals that may go: finished writes older than `--days` (default 30) that are not the newest write of their source or operation. A source's parent and retry jobs count as one source; other jobs are grouped by operation, such as refresh or daily. `sb job prune --apply` deletes those journals and their write plans; such a write can no longer be undone, and its cards stay. `sb status` shows the state folder under `state_dir`: its size, the size of each top-level folder (`parts`, such as `jobs` and `cache`) and what `sb job prune` would free now (`prunable_mb`). The cache holds the parse cache and the search index; it grows with the vault and is rebuilt if deleted.

`sb ingest --retry <job>` runs the pending cards of a partial job as a child job, `<job>-retry1` and so on, with its own transaction. Roll back a retry child before its parent: while the child's writes are in the vault, `sb recover <parent> --rollback` refuses and names the child. A child that was rolled back or reset does not block a new `sb ingest --retry <parent>`; the new child takes its id.

A retry child sees what earlier jobs of the source settled without a card (passages that existing cards hold, passages out of scope) and does not write those again. Its result has `retry_progress`: `open_before`, `open_after`, `cards_written` and `progress`. When the child wrote no card and left as many items open as it was given (`progress: false`), the result has a `message` instead of a retry `hint`: another retry would likely repeat it. Read the reasons in `pending` (`sb job show <job>`), and retry only after changing what blocked them, such as the taxonomy for a new topic; or leave them open and close the source with `sb inbox skip <file> --reason "..."`. `sb inbox list` and `sb ingest <source>` say the same. `sb ingest --retry` on a job that was already retried does not start another child: it returns a short summary whose `message` names the newest job of the chain, with a `hint` to retry that job when it is partial and made progress.

## Other commands for an existing vault

These commands use the same produce, review and gate pattern on cards that are already in the vault.

| Command | Does |
|---|---|
| `sb refresh` | previews the MOC pages that would change; `--apply` writes them. In a vault with MOC pages from elsewhere, the first `--apply` also adds the Breadcrumbs field `BC-list-note-field: down` to each page ([details](new-vault.md#4-check-and-generate-the-maps)) |
| `sb classify --domain <slug>` | proposes pillar and topic for cards that have none (`--repair-invalid` for cards whose classification is no longer legal) |
| `sb backlog` | runs classification over a whole domain or vault in batches |
| `sb curate --domain <slug>` | proposes changes to the pillars and topics of existing domains; for a topic past 25 cards it gets a proposal of how to split it ([above](#what-waits-for-you)) |
| `sb relocate --source-job <job>` | moves cards to another domain when a classification review suggested it |
| `sb domcreate --domain <slug> ...` | proposes a new domain from cards that already exist |
| `sb resume --job <job>` | continues one of these jobs |
| `sb export --domain <slug>` | writes one map's cards up as an article ([export.md](export.md)) |
| `sb docs --apply` | regenerates the entry page `HOME.md` (and the generated blocks of `[layout] entry_docs`) |
| `sb today --apply` | writes the review page of the current review day under `50_Reviews/`: the due and new cards of the review registry, the same queue that `sb review queue` gives during that day. A review day starts at 04:00, so a page made at 01:00 is still for the day before. It does not change `HOME.md`. Needs the `review` extra |
| `sb daily --apply` | does both, and `HOME.md` links the new page; this is the command HOME names when the page is missing. Without the `review` extra it writes `HOME.md` only and reports `review_extra_missing` |

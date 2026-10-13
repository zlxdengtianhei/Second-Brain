## Task: turn this chunk into atomic cards

A card is one unit of knowledge that someone can recall on its own: it answers one clear question, makes sense without the source, and keeps the limits, conditions and counterexamples the source gives.

For each card give:
- `slug`: short lowercase id part, `[a-z0-9-]`, unique within this source. The program adds the id prefix; never write ids yourself.
- `title`: a precise title that is also the file name. Do not use `/ \ : * ? " < > | # ^ [ ]`. It must not repeat an existing card title.
- `kind`: concept | mechanism | contrast | derivation | fact. `status`: confirmed, or needs-review when the source is unclear or conflicts with existing cards (say where in a proposition).
- `domain`, `pillar`, `topic`: classify into an existing domain and one of its curated pillar/topic pairs (`candidate_taxonomies` lists the likely domains; `domains` lists all). Choose the domain by what the knowledge is used as, not by where the source comes from.
- If no existing topic fits, you may propose a new topic under an existing pillar in `new_topics` (at most the number in `rules.new_topics_per_source` for the whole source) and use it on the card. A new topic needs a clear boundary and must not duplicate or merely rename an existing topic; the `cards` counts show how full each topic is. Knowledge that needs a new pillar or domain becomes a coverage row with disposition `needs_taxonomy` and no card.
- `essence`: one sentence. `propositions`: 1–12 statements, each recallable on its own.
- `intuition` (why it works, or a way to picture it) and `confusions` (what it is easily mixed up with, and the difference): write them whenever the chunk or the `neighbors` support them; leave them out rather than write filler. A card with a `contrasts_with` relation states that contrast in `confusions`.
- `flashcards`: 2–4 `{q, a}` pairs on the key propositions. Do not use `::` inside them.
- `relations`: typed links to existing card ids (from `neighbors`) or to slugs of cards of this source (`other_chunks_cards` or this answer). Add a short `note` saying why. `relation_types` gives the meaning of each type as `A type B`: A is the card you are writing and B is the `target` (in a gloss, "the former" / 前者 is A and "the latter" / 后者 is B). Use a type only when that sentence is true in this direction; the program writes the inverse link on the target card. A prerequisite (`depends_on`) needs a real logical dependency; the order of the source is not one. Mark weak, speculative links with `"tier": "extension"`.
- `neighbors` lists existing cards close to each part of the chunk. Link a card to every neighbour it is closely related to (prerequisite, part, contrast, similar idea). When a neighbour already holds the same knowledge, write a `duplicate` coverage row instead of a card.
- Refer to other cards only by `[[Title]]` in text, never by id.
- `source_quotes`: one or more passages copied character for character from the chunk text. The first one locates the card in the source.
- `reason`: why this is one unit (kept from the reviewer; for the record).

Then `coverage`: walk through the whole chunk and say how each part is handled, each row with a verbatim `source_quote`:
- `card` (list the card slugs), `duplicate` (the knowledge already exists: list existing ids in `duplicate_of`), `out_of_scope` (not knowledge worth keeping, e.g. greetings or logistics), or `needs_taxonomy` (give `suggested_pillar`).
Every card must appear in a `card` row. Do not drop knowledge because the chunk is long, and do not pack a whole chapter or question bank into one card.

Do not write diagrams or Markdown sections; the program renders the card from your fields.

## Task: turn this chunk into atomic cards

A card is one unit of knowledge that someone can recall on its own: it answers one clear question, makes sense without the source, and keeps the limits, conditions and counterexamples the source gives. Say the thing itself; never point at the source ("this handout", "the problem above", "the original template").

For each card give:
- `slug`: short lowercase id part, `[a-z0-9-]`, unique within this source. The program adds the id prefix; never write ids yourself.
- `title`: a precise title that is also the file name. Do not use `/ \ : * ? " < > | # ^ [ ]`. It must not repeat an existing card title.
- `kind`: concept | mechanism | contrast | derivation | fact. `status`: confirmed, or needs-review when the source is unclear.
- `domain`, `pillar`, `topic`: classify into an existing domain and one of its curated pillar/topic pairs (`candidate_taxonomies` lists the likely domains; `domains` lists all). Choose the domain by what the knowledge is used as, not by where the source comes from.
- `placement`, when the packet has it: the owner chose where this source's cards go. File each card under that domain and pillar, and under its topic when one is given, unless the card is about a subject that already has its own topic in the taxonomy (an algorithm, a data structure, another language); file such a card where that subject's cards are. With a pillar but no topic, choose among the pillar's topics, and put a new topic, if one is needed, under that pillar.
- If no existing topic fits, you may propose a new topic under an existing pillar in `new_topics` (at most the number in `rules.new_topics_per_source` for the whole source) and use it on the card. A new topic needs a clear boundary and must not duplicate or merely rename an existing topic; the `cards` counts show how full each topic is. Knowledge that needs a new pillar or domain becomes a coverage row with disposition `needs_taxonomy` and no card.
- `essence`: one sentence. `propositions`: 1–12 statements, each recallable on its own.
- `intuition` (why it works, or a way to picture it) and `confusions` (what it is easily mixed up with, and the difference): write them whenever the chunk or the `neighbors` support them; leave them out rather than write filler. A card with a `contrasts_with` relation states that contrast in `confusions`.
- `flashcards`: 2–4 `{q, a}` pairs on the key propositions. Do not use `::` inside them.
- `relations`: typed links to existing card ids (from `neighbors`) or to slugs of cards of this source (`other_chunks_cards` or this answer). Add a short `note` saying why. `relation_types` gives the meaning of each type as `A type B`: A is the card you are writing and B is the `target` (in a gloss, "the former" / 前者 is A and "the latter" / 后者 is B). Use a type only when that sentence is true in this direction; the program writes the inverse link on the target card. `relation_types` are open to every domain; `domain_relation_types` lists extra types open only to cards of the named domain, and the target's domain must allow the inverse too, so they mostly link cards of that domain. A prerequisite (`depends_on`, or `required_by` the other way) needs a real logical dependency; the order of the source is not one. A card on a library, tool or language feature is not a prerequisite of the idea it implements, nor of a card about another language: use `similar_to` or `contrasts_with` there. Link each card to the cards of this answer it belongs with; a card with no relation at all is rare. Mark weak, speculative links with `"tier": "extension"`.
- `neighbors` lists existing cards close to each part of the chunk. Link a card to every neighbour it is closely related to (prerequisite, part, contrast, similar idea). When a neighbour already holds the same knowledge, write a `duplicate` coverage row instead of a card; so too when the card would add only a detail to it.
- `conflicts`: when the chunk disagrees with an existing card on a fact (a number, a definition, a claim one of them gets wrong), list `{"with": "<existing id>", "detail": "<what each says>"}` on the card. Never put such a note into the card's propositions or flashcards; the existing card is not changed, the owner settles it.
- One relation per target: pick the type that fits best.
- Refer to other cards only by `[[Title]]` in text, never by id.
- `source_quotes`: one or more passages copied character for character from the chunk text. The first one locates the card in the source.
- `reason`: why this is one unit (kept from the reviewer; for the record).

Then `coverage`: walk through the whole chunk and say how each part is handled, each row with a verbatim `source_quote`:
- `card` (list the card slugs), `duplicate` (the knowledge already exists: list existing ids in `duplicate_of`), `out_of_scope` (not knowledge worth keeping, e.g. greetings or logistics), or `needs_taxonomy` (give `suggested_pillar`).
Every card must appear in a `card` row. Do not drop knowledge because the chunk is long, and do not pack a whole chapter or question bank into one card.

Do not write diagrams or Markdown sections; the program renders the card from your fields.

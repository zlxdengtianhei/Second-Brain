## Task: independently review proposed cards for this chunk

You did not write the proposal and you do not see the author's reasoning. Read the chunk text yourself and judge every card on its own.

For each card in `candidate.cards` decide `accept` or `revise`:
- `accept`: one recallable unit, faithful to the chunk, readable without the source, correctly classified (domain by use, legal or well-proposed topic), relations that make sense and point the right way, 2–4 useful flashcards.
  - Faithful: check the `source_quotes`, every proposition, every flashcard question and answer, and every sentence of the essence, the intuition and each confusion against the text. Each sentence must be supported by the chunk's text, or say only what a library card it names by `[[title]]` has written (that card is shown in `candidate_cards`); the premises of an example may also come from a card of this source it names by `[[title]]` (shown in `other_chunks_cards`). A sentence that is neither (a fact, a number, an example or a contrast that comes from general knowledge, not from the chunk or the named card) is a reason to revise, also in the intuition and the confusions. Check each example on its own: a string, number, code snippet or output must be what the text shows and must do what the card says (for instance, the spaces in a string literal). A flashcard the text does not support is a reason to revise. Compare the card's numbers, formulas and claims with the propositions of its `candidate_cards` as well: where they disagree, record a conflict (below).
  - Readable without the source: a card that points at the source instead of saying the thing ("this handout", "the problem above", "the original template", 本讲义, 该题) is a reason to revise.
  - Direction: read each relation as `A type B`, with A this card and B the target, using the meaning in `relation_types` ("the former" / 前者 is A). A relation that is true only the other way round is a reason to revise, and so is a `depends_on` or `required_by` that makes a card on a library, tool or language feature a prerequisite of the idea it implements or of a card about another language (that is `similar_to` or `contrasts_with`). (The program already refuses a type that the card's domain does not use: `domain_relation_types` types are open only to their domain.)
  - Placement: when the packet has `placement`, a card filed outside it is right only when its subject already has its own topic where it is filed; otherwise revise and name the placement.
  Existing cards: `candidates` lists for each proposed card the existing cards to compare it with, and `candidate_cards` shows each one once. `via` says where a card comes from: `named`, the producer named it (in a relation, a conflict or an overlap hint), shown whole with its propositions and review cards; `words`, found by search; `hop`, linked from a found card; `shelf`, filed under the same topic (title only); `written`, in a retry, a card an earlier job of this source wrote. Under `#coverage` are the existing cards the coverage rows say already hold a passage. When the packet runs long, the less close found cards show the essence or the title only; named cards are always whole. Every claim that an existing card already holds something (a coverage row, an overlap hint) is checked against that card's propositions here; one that the card does not hold is a reason to revise.
  When a card overlaps a candidate card in part and has no relation to it, `revise` and name the existing card id with the relation type to add.
- `revise`: anything else. Give a concrete, checkable reason (what is wrong, where), not general advice.
  When the card fits another domain better, put that domain's slug in `suggested_domain`, also when it is not among `taxonomies`: `domains` lists every domain with its question. Choose by what the knowledge is used as: a card about a general tool or library belongs where that tool's other cards are, even when the source used it to build something else. The producer then gets that domain's taxonomy in the repair round.

`context_note`, when present, is the program's note on where this chunk sits in the source: the headings it is under, the end of the chunk before, the source's outline (in some runs a short summary of what came before, or the source's concepts by chunk). Use it only to understand the chunk, for instance what "the three kinds above" are: it is not part of the chunk, so do not quote it, do not write cards from it, and it covers no passage.

<!-- notes.definition -->

<!-- notes.cards -->
Notes go on the card they are about; they are recorded for the owner and shown to the check of the whole source.

`other_chunks_cards` lists the cards proposed for the other chunks of this source. Do not revise a card only because one of them makes the same point: once every chunk is reviewed, a check of the whole source decides which of such cards are written. A relation to one of them must still make sense.

`overlap` and `with`, on every accepted card: how much it repeats the existing cards, with `with` naming the existing cards (ids from `candidate_cards`) it is compared with. You decide this for the card against the vault; the program then acts on it:
- `same`: the card is not written; the cards in `with` already ask for everything it would. Say in the reason where they hold it.
- `extends`: what the card adds (its propositions and review cards, word for word) goes into the one card in `with`, which keeps its own words; the card must link that card. When that card cannot take more, the card is rewritten as a narrower card next to it.
- `distinct`: the card is written; `with` may name the related existing cards or be empty.

<!-- overlap.definition -->

A relation note of the card saying that the other card "already covers" it, or that the two "measure the same thing", points to `same` or `extends`. A level is never a reason to revise: revise only when the card itself is wrong; judge its level once it is right.

`conflicts`, on any card: when the chunk or the card disagrees with an existing card on a fact (a number, a definition, a direction, a claim one of them gets wrong), add `{"with": "<existing id>", "detail": "<what each says, and which the chunk supports>"}`. The card's own `conflicts` come from the producer and are recorded anyway; repeat the ones you confirm, with your own detail. A conflict does not change your decision; the program records it for the owner, who settles it. Do not merge or correct the existing card.

For each proposed new topic (`candidate.new_topics`) decide `accept` or `revise`: is the boundary clear, is it really missing from the taxonomy (not a synonym or a sub-case of an existing topic), and would more than this one card belong there over time?

`coverage`: `accept` when the cards and coverage rows together cover the knowledge in the chunk worth keeping; `revise` with the missing or mishandled passages otherwise. A row that says existing cards already hold a passage is right only when those cards (shown under `#coverage`) hold every point of it; name the point they lack otherwise. When `already_accepted_cards` and `already_settled` are present (a repair round), they count toward coverage: the cards accepted in earlier rounds, and the passages earlier rounds handled without a card (`duplicates` of existing cards, `out_of_scope` material). A candidate card may repeat an accepted card to file it under another topic; judge it like any other card.

Do not rewrite the cards; only judge them.

## Task: independently review proposed cards for this chunk

You did not write the proposal and you do not see the author's reasoning. Read the chunk text yourself and judge every card on its own.

For each card in `candidate.cards` decide:
- `accept`: one recallable unit, faithful to the chunk, correctly classified (domain by use, legal or well-proposed topic), relations that make sense and point the right way, 2–4 useful flashcards.
  - Faithful: check the `source_quotes`, every proposition, and every flashcard question and answer against the text. Check each example on its own: a string, number, code snippet or output must be what the text shows and must do what the card says (for instance, the spaces in a string literal). A flashcard the text does not support is a reason to revise.
  - Direction: read each relation as `A type B`, with A this card and B the target, using the meaning in `relation_types` ("the former" / 前者 is A). A relation that is true only the other way round is a reason to revise.
- `duplicate`: the same knowledge already exists; give the existing card id in `duplicate_of`. `near_duplicates` shows the closest existing cards with their full text. A related card that adds something new is not a duplicate.
  When a card overlaps a near duplicate in part and has no relation to it, `revise` and name the existing card id with the relation type to add, or say that the two cards should be merged.
- `revise`: anything else. Give a concrete, checkable reason (what is wrong, where), not general advice.
  When the card fits another domain better, put that domain's slug in `suggested_domain`, also when it is not among `taxonomies`: `domains` lists every domain with its question. Choose by what the knowledge is used as: a card about a general tool or library belongs where that tool's other cards are, even when the source used it to build something else. The producer then gets that domain's taxonomy in the repair round.

For each proposed new topic (`candidate.new_topics`) decide `accept` or `revise`: is the boundary clear, is it really missing from the taxonomy (not a synonym or a sub-case of an existing topic), and would more than this one card belong there over time?

`coverage`: `accept` when the cards and coverage rows together cover the knowledge in the chunk worth keeping; `revise` with the missing or mishandled passages otherwise. When `already_accepted_cards` is present, those cards count toward coverage.

Do not rewrite the cards; only judge them.

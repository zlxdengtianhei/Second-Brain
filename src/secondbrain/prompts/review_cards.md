## Task: independently review proposed cards for this chunk

You did not write the proposal and you do not see the author's reasoning. Read the chunk text yourself and judge every card on its own.

For each card in `candidate.cards` decide:
- `accept`: one recallable unit, faithful to the chunk (check the `source_quotes` and the propositions against the text), correctly classified (domain by use, legal or well-proposed topic), relations that make sense, 2–4 useful flashcards.
- `duplicate`: the same knowledge already exists; give the existing card id in `duplicate_of`. `near_duplicates` shows the closest existing cards with their full text. A related card that adds something new is not a duplicate.
- `revise`: anything else. Give a concrete, checkable reason (what is wrong, where), not general advice.

For each proposed new topic (`candidate.new_topics`) decide `accept` or `revise`: is the boundary clear, is it really missing from the taxonomy (not a synonym or a sub-case of an existing topic), and would more than this one card belong there over time?

`coverage`: `accept` when the cards and coverage rows together cover the knowledge in the chunk worth keeping; `revise` with the missing or mishandled passages otherwise. When `already_accepted_cards` is present, those cards count toward coverage.

Do not rewrite the cards; only judge them.

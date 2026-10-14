## Task: independently review proposed cards for this chunk

You did not write the proposal and you do not see the author's reasoning. Read the chunk text yourself and judge every card on its own.

For each card in `candidate.cards` decide:
- `accept`: one recallable unit, faithful to the chunk, readable without the source, correctly classified (domain by use, legal or well-proposed topic), relations that make sense and point the right way, 2–4 useful flashcards.
  - Faithful: check the `source_quotes`, every proposition, and every flashcard question and answer against the text. Check each example on its own: a string, number, code snippet or output must be what the text shows and must do what the card says (for instance, the spaces in a string literal). A flashcard the text does not support is a reason to revise. Compare the card's numbers, formulas and claims with the propositions of its `near_duplicate_cards` as well: where they disagree, record a conflict (below).
  - Readable without the source: a card that points at the source instead of saying the thing ("this handout", "the problem above", "the original template", 本讲义, 该题) is a reason to revise.
  - Direction: read each relation as `A type B`, with A this card and B the target, using the meaning in `relation_types` ("the former" / 前者 is A). A relation that is true only the other way round is a reason to revise, and so is a `depends_on` or `required_by` that makes a card on a library, tool or language feature a prerequisite of the idea it implements or of a card about another language (that is `similar_to` or `contrasts_with`). (The program already refuses a type that the card's domain does not use: `domain_relation_types` types are open only to their domain.)
  - Placement: when the packet has `placement`, a card filed outside it is right only when its subject already has its own topic where it is filed; otherwise revise and name the placement.
- `duplicate`: the same knowledge already exists; give the existing card id in `duplicate_of`. `near_duplicates` names the closest existing cards for each candidate card, and `near_duplicate_cards` gives each one's title, essence and propositions (when the packet runs long, the less close ones show the essence only). Judge `duplicate` also when the card repeats an existing card and adds no recallable unit of its own; say in the reason what it adds, so the owner can merge it into that card.
  When a card overlaps a near duplicate in part and has no relation to it, `revise` and name the existing card id with the relation type to add.
- `revise`: anything else. Give a concrete, checkable reason (what is wrong, where), not general advice.
  When the card fits another domain better, put that domain's slug in `suggested_domain`, also when it is not among `taxonomies`: `domains` lists every domain with its question. Choose by what the knowledge is used as: a card about a general tool or library belongs where that tool's other cards are, even when the source used it to build something else. The producer then gets that domain's taxonomy in the repair round.

`other_chunks_cards` lists the cards proposed for the other chunks of this source. Do not revise a card only because one of them makes the same point: once every chunk is reviewed, a check of the whole source decides which of such cards are written. A relation to one of them must still make sense.

`overlap`, on an accepted card that has a relation to an existing card (the validator asks for it then):
- `distinct`: the card holds a recallable unit the linked cards do not have; they are related, not the same.
- `needs_review`: the card mostly repeats one existing card (put its id in `overlap_with`; the card must link it) and you cannot tell whether the two should be merged. Both cards are kept and linked, and the new one is written with `status: needs-review` for the owner to merge or keep. A relation note of the card saying that the other card "already covers" it, or that the two "measure the same thing", points here or to `duplicate`.

`conflicts`, on any card: when the chunk or the card disagrees with an existing card on a fact (a number, a definition, a direction, a claim one of them gets wrong), add `{"with": "<existing id>", "detail": "<what each says, and which the chunk supports>"}`. The card's own `conflicts` come from the producer and are recorded anyway; repeat the ones you confirm, with your own detail. A conflict does not change your decision; the program records it for the owner, who settles it. Do not merge or correct the existing card.

For each proposed new topic (`candidate.new_topics`) decide `accept` or `revise`: is the boundary clear, is it really missing from the taxonomy (not a synonym or a sub-case of an existing topic), and would more than this one card belong there over time?

`coverage`: `accept` when the cards and coverage rows together cover the knowledge in the chunk worth keeping; `revise` with the missing or mishandled passages otherwise. When `already_accepted_cards` and `already_settled` are present (a repair round), they count toward coverage: the cards accepted in earlier rounds, and the passages earlier rounds handled without a card (`duplicates` of existing cards, `out_of_scope` material). A candidate card may repeat an accepted card to file it under another topic; judge it like any other card.

Do not rewrite the cards; only judge them.

## Task: judge a card against the existing cards again, after the merge check disputed it

You accepted `card` and judged it `extends` the existing card in `with`, so its additions were to go into that card. The merge check, which composes that addition, could not: `dispute` says why (nothing to carry, or what would be carried answers another question). Judge the card's level against the existing cards again, with the same scale:

<!-- overlap.definition -->

Answer `{"slug": ..., "overlap": ..., "with": [...], "reason": "..."}`: `with` names existing cards by id (`candidate_cards` shows them). `extends` again sends the card back to the merge check; `distinct` writes it as a card of its own; `same` does not write it.

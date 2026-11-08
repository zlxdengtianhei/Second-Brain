## Task: independently review a narrower card written next to the card it extends

`card` was rewritten from a card that extends `existing_card`, because `existing_card` could not take more. You did not write it and you do not see the author's reasoning. Read `chunk.text` yourself.

Answer `{"decision": "accept" | "revise", "restates": true | false, "reason": "...", "notes": [...]}`:
- `accept` when the card is faithful to the chunk (its source quotes, every proposition, every question and answer), readable without the source, one recallable unit, and does not restate the propositions of `existing_card`.
- `restates`: true when one of its propositions says what a proposition of `existing_card` says, in any words. A card that restates is revised, never accepted. Do not judge it otherwise against `existing_card`: being narrower than that card is what it is meant to be.
- `revise` with a concrete, checkable reason otherwise.
- `matches`: for each proposition of `card` (`P1` is the first), the proposition of `existing_card` that says the same thing in any words, or `null` when none does: `[{"proposition": "P1", "existing": "P9"}, {"proposition": "P2", "existing": null}]`. A card with a proposition that matches is sent back to be written again without it.

<!-- notes.definition -->

`context_note`, when present, is the program's note on where the chunk sits in the source: the headings it is under, the end of the chunk before, the source's outline (in some runs a short summary of what came before, or the source's concepts by chunk). Use it only to understand `chunk.text`, for instance what "the three kinds above" are: it is not part of the chunk, so do not quote it, do not write cards from it, and it covers no passage.

## Task: independently review a narrower card written next to the card it extends

`card` was rewritten from a card that extends `existing_card`, because `existing_card` could not take more. You did not write it and you do not see the author's reasoning. Read `chunk.text` yourself.

Answer `{"decision": "accept" | "revise", "restates": true | false, "reason": "..."}`:
- `accept` when the card is faithful to the chunk (its source quotes, every proposition, every question and answer), readable without the source, one recallable unit, and does not restate the propositions of `existing_card`.
- `restates`: true when one of its propositions says what a proposition of `existing_card` says, in any words. A card that restates is revised, never accepted. Do not judge it otherwise against `existing_card`: being narrower than that card is what it is meant to be.
- `revise` with a concrete, checkable reason otherwise.

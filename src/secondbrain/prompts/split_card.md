## Task: rewrite a card as a narrower card next to the card it extends

`card` was found to extend `existing_card`: it asks the same question and adds details, examples, cases or the answer to a sub-question. Those additions would leave `existing_card` with more than 12 propositions or 8 review cards, so it is not changed; write the additions as a card of their own instead.

Write one card, in `card`:
- It answers one question that is narrower than the question of `existing_card`: how that thing behaves in one case, one complete example of it, the answer to one of its sub-questions. Its title names that narrower question.
- It holds only what `existing_card` does not say (`carry` lists what the patch would have added), plus the least it needs to be read on its own. Do not restate a proposition of `existing_card`, not even in other words; the program refuses a proposition that has the same words as one of them.
- `relations`: link `existing_card` (by its id) with `part_of` or `example_of`, whichever is true read as `A type B` with A this card; add other relations only when `card` had them.
- `source_quotes`: passages copied character for character from `chunk.text`; the first one locates the card in the source.
- `propositions`: 1–12; `flashcards`: 2–4 `{q, a}` pairs without `::`; `slug`: a new slug for this source; `kind`, `essence` and, when the chunk supports them, `intuition` and `confusions` as for any card. Refer to other cards only by `[[Title]]`.

When `previous_candidate` and `review_findings` are present, an independent review rejected your previous card; fix what it names.

`context_note`, when present, is the program's note on where the chunk sits in the source: the headings it is under, the end of the chunk before, the source's outline (in some runs a short summary of what came before, or the source's concepts by chunk). Use it only to understand `chunk.text`, for instance what "the three kinds above" are: it is not part of the chunk, so do not quote it, do not write cards from it, and it covers no passage.

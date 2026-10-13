## Task: independently review a classification proposal

You did not write the proposal. Read every card body and the full taxonomy, then judge each item on its own.

Output: `{"items": [{"id", "decision", "reason"}]}`

- Cover every candidate id exactly once.
- `decision` is `accept` or `revise`. Give a concrete, checkable reason for each item.
- Revise when the category is wrong or missing, the card belongs in another domain, the quote is not verbatim, or a gap is hidden behind a nearby label.
- Do not rewrite the proposal; only judge it.

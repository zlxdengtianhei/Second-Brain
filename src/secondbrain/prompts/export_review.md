## Task: review one unit of an exported article for faithfulness

Another model wrote the paragraphs in the packet from knowledge cards. Check each paragraph against the cards: only whether it is faithful to them, not its style.

Packet fields: `paragraphs` (each with `id`, `text` and `cites`, the cards it names with `[[…]]`); `cards` (the bodies of the cited cards and of the other cards of this unit); `unit_cards` (the cards this unit must cover).

For each paragraph decide:
- `accept`: every statement is supported by the cards it cites (paraphrase is fine) and nothing contradicts them.
- `revise`: a statement is in none of the cited cards (an added fact, number, example, name, or an unsupported connection between ideas; a prerequisite or contrast stated the wrong way round); it contradicts a card; it cites the wrong card; it changes a formula; or it uses a card of this unit without citing it.

Reason: for `revise`, quote the problem words and say what the card says instead, or that no cited card says it. For `accept`, one short sentence. Be concrete. Do not ask for style changes, and do not ask for content the cards do not have.

<!-- notes.definition -->
For a paragraph, `not_a_unit` does not apply.

Answer: `{"items": [{"id": "<paragraph id>", "decision": "accept" | "revise", "reason": "…", "notes": [...]}]}` with exactly one item per paragraph.

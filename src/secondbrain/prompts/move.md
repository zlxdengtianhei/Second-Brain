## Task: confirm card moves to another existing domain

Output: `{"items": [{"id", "input_sha256", "disposition", "domain", "pillar", "topic", "evidence_quote", "reason"}]}`

- Return every input card id exactly once; `input_sha256` must equal the card's `sha256`.
- The destination domain is fixed per card in `destinations`. Check whether the card really fits there and pick an existing curated pillar/topic of that domain: `disposition: assigned`.
- No fitting topic, weak reason to move, or the card should stay: `disposition: taxonomy_gap` with a concrete reason. Never move to a new domain.
- `evidence_quote` must be copied verbatim from the card body.
- A move changes only domain/pillar/topic and the matching flashcards tag; body, id and review progress stay.

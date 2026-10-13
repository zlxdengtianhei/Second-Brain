## Task: classify existing cards

Output: `{"items": [{"id", "input_sha256"?, "disposition", "pillar", "topic", "evidence_quote", "reason", "suggested_pillar"?, "suggested_topic"?, "suggested_domain"?}]}`

- Return every input card id exactly once.
- `disposition` is `assigned`, `taxonomy_gap` or `needs_domain_review`.
- `assigned` only when a curated pillar/topic pair of the card's current domain truly fits. Never fill a generic placeholder.
- No fitting topic: `taxonomy_gap` with `pillar` and `topic` null and a concrete reason. A missing taxonomy file is a gap, not a reason to invent a domain.
- The card belongs in another existing domain: `needs_domain_review` with `suggested_domain`.
- `evidence_quote` must be copied character for character from the card body.
- `input_sha256` may be omitted; if given it must equal the card's `sha256` in the packet.
- Never overwrite an existing non-empty classification. Different mechanisms are not merged because of similar words.
- When several cards lack a topic, look for a shared boundary first instead of one pillar per card.

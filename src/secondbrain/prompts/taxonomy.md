## Task: curate the taxonomy of existing domains

Output: `{"items": [{"domain", "taxonomy": {"title", "narrative", "pillars": [{"name", "order", "why", "topics": [str]}], "bridges"?}, "reason", "coverage_ids": [id]}]}`

- One item per domain in `domains`, each exactly once. Never add a domain.
- Domains that share one MOC must carry an identical taxonomy.
- `title` equals the domain's MOC name in the catalog. The narrative explains the learning order; each pillar has a concrete `why` and non-empty, unique topics; pillar names and orders are unique; orders are positive integers.
- Keep every existing pillar/topic that a card uses. Keep unknown fields of the current taxonomy unchanged.
- `coverage_ids` cites existing cards of this MOC as evidence for new topics.
- Draw boundaries around groups of cards: avoid one pillar per card, synonym topics, and new categories that hide cards from the wrong domain.
- `split_proposals`, when present, lists the topics past one map page (25 cards) and how their cards group by the relations among them: each group with its cards' titles, groups still past 25 marked `needs_second_round`, and cards that fit no group under `scattered`. It is a hint, not a decision: add a topic for a group only when its cards answer one kind of question you can name; a group marked `needs_second_round` may need more than one topic. Cards keep their topic until they are classified again.

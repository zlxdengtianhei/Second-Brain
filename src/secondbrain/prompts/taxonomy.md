## Task: curate the taxonomy of existing domains

Output: `{"items": [{"domain", "taxonomy": {"title", "narrative", "pillars": [{"name", "order", "why", "topics": [str]}], "bridges"?}, "reason", "coverage_ids": [id]}]}`

- One item per domain in `domains`, each exactly once. Never add a domain.
- Domains that share one MOC must carry an identical taxonomy.
- `title` equals the domain's MOC name in the catalog. The narrative explains the learning order; each pillar has a concrete `why` and non-empty, unique topics; pillar names and orders are unique; orders are positive integers.
- Keep every existing pillar/topic that a card uses. Keep unknown fields of the current taxonomy unchanged.
- `coverage_ids` cites existing cards of this MOC as evidence for new topics.
- Draw boundaries around groups of cards: avoid one pillar per card, synonym topics, and new categories that hide cards from the wrong domain.

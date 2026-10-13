## Task: propose a new domain

Output: `{"domain", "moc", "question", "track", "taxonomy": {"title", "narrative", "pillars": [{"name", "order", "why", "topics": [str]}]}, "relation_words"?: [{"type", "inverse", "gloss"}], "migrations": [{"id", "pillar", "topic"}], "boundary_reason", "difference_reason", "impact_reason"}`

- `domain`, `moc`, `question` and `track` must equal the values given in the packet.
- `taxonomy` is the complete taxonomy of the new domain (same rules as taxonomy curation: narrative, unique pillars with a concrete why, non-empty topics; no one-card pillars or synonym topics).
- `migrations` gives the new pillar/topic for each evidence card that moves. Use only evidence card ids from the packet and categories from your taxonomy. Do not move cards that do not belong.
- Add `relation_words` only when the existing relation vocabulary cannot express a relation of this domain; each needs an inverse and a gloss.
- `boundary_reason`: which cards come in, which stay, and the criterion. `difference_reason`: why this cannot stay a pillar of the current domain. `impact_reason`: broken links, review progress, projections.

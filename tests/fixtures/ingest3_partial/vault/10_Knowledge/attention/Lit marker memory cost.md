---
id: kc_lit-marker-cost
title: Lit marker memory cost
kind: fact
domain: attention
pillar: Token routing
topic: Markers
tags:
- review
- flashcards/attention
sources:
- path: 40_Sources/cde2d135ac446f37/lantern_queues.md
  sha256: cde2d135ac446f374c1ae622a3df4543b657c881cabbe87480b7eae19bcec806
  locator: A writer lights a marker every eight items.
relations:
- type: depends_on
  target: kc_lantern-queue
  note: markers belong to the queue
- type: part_of
  target: card_exist
  note: weak guess
  tier: extension
status: confirmed
created: 2026-11-08
---
## Essence
Each lit marker costs one extra word, one marker per eight items.

## Propositions
- P1. A writer lights a marker every eight items.
- P2. Each marker costs one word of memory; 800 items carry 100 markers.

## Related
- depends_on:: [[Lantern queue]] (markers belong to the queue)

<details><summary>Extension: possible relations (1)</summary>

- [[已有卡]] — part_of (weak guess)

</details>

## Review cards
How often is a marker lit?::Every eight items.
How many markers do 800 items carry?::100.
What does one marker cost?::One extra word of memory.

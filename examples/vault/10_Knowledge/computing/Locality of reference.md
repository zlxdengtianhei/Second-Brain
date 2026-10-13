---
id: kc_locality-of-reference
title: Locality of reference
kind: concept
domain: computing
pillar: Systems
topic: Caching
tags:
- review
- flashcards/computing
sources:
- path: 40_Sources/40169515563ffea6/Caches.md
  sha256: 40169515563ffea61cc6daf6316d688d750bff6170c44dbb565859ba90c95d1c
  locator: 'Caches work because programs do not access data at random. Temporal locality: data used recently is likely to be used again soon. Spatial locality: data stored near recently used data is likely to be used soon, which is why caches load whole blocks rather than single bytes.'
relations:
- type: depends_on
  target: kc_cache-basics
  note: Locality explains why caches work.
- type: required_by
  target: kc_lru-eviction
status: confirmed
created: 2026-10-13
---
## Essence
Caches work because programs do not access data at random: they reuse recent data (temporal locality) and use nearby data (spatial locality).

## Propositions
- P1. Caches work because programs do not access data at random.
- P2. Temporal locality: data used recently is likely to be used again soon.
- P3. Spatial locality: data stored near recently used data is likely to be used soon.
- P4. Because of spatial locality, caches load whole blocks rather than single bytes.
- P5. A loop over an array benefits from both temporal and spatial locality.
- P6. A program that jumps at random through a large data set gets little benefit from any cache.

## Intuition and contrasts
- Intuition: If access were random, a small cache would rarely hold what comes next.
- Contrast: Temporal locality is about reusing the same item soon; spatial locality is about using neighbouring items soon.

## Related
- depends_on:: [[Cache as a small fast copy of a larger slower store]] (Locality explains why caches work.)
- required_by:: [[LRU eviction and its implementation]]

## Review cards
What is temporal locality?::Data used recently is likely to be used again soon.
What is spatial locality, and what cache design choice does it explain?::Data stored near recently used data is likely to be used soon, so caches load whole blocks rather than single bytes.
Which kind of program gets little benefit from any cache?::A program that jumps at random through a large data set.

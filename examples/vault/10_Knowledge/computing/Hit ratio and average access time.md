---
id: kc_hit-ratio
title: Hit ratio and average access time
kind: derivation
domain: computing
pillar: Systems
topic: Caching
tags:
- review
- flashcards/computing
sources:
- path: 40_Sources/40169515563ffea6/Caches.md
  sha256: 40169515563ffea61cc6daf6316d688d750bff6170c44dbb565859ba90c95d1c
  locator: The hit ratio is the share of requests served from the cache. The average access time is roughly hit ratio × cache time + (1 − hit ratio) × slow-store time.
relations:
- type: depends_on
  target: kc_cache-basics
  note: Hit ratio is defined through hits and misses.
status: confirmed
created: 2026-10-13
---
## Essence
The hit ratio is the share of requests served from the cache, and because the slow store is much slower, small changes in it change average access time a lot.

## Propositions
- P1. The hit ratio is the share of requests served from the cache.
- P2. Average access time is roughly hit ratio × cache time + (1 − hit ratio) × slow-store time.
- P3. The slow store can be about a hundred times slower, so small changes in the hit ratio matter a lot.
- P4. With a 1 ns cache in front of a 100 ns memory, a 90 percent hit ratio gives about 10.9 ns per access on average.
- P5. With the same 1 ns cache and 100 ns memory, a 99 percent hit ratio gives about 2 ns.

## Intuition and contrasts
- Intuition: Each miss costs about a hundred hits, so even a few misses dominate the average.

## Related
- depends_on:: [[Cache as a small fast copy of a larger slower store]] (Hit ratio is defined through hits and misses.)

## Review cards
How is average access time estimated from the hit ratio?::Roughly hit ratio × cache time + (1 − hit ratio) × slow-store time.
With a 1 ns cache and 100 ns memory, what is the average access time at a 90 percent hit ratio?::About 10.9 ns.
With a 1 ns cache and 100 ns memory, what is the average access time at a 99 percent hit ratio?::About 2 ns.
Why do small changes in hit ratio matter so much?::Because the slow store can be a hundred times slower, so each miss costs far more than a hit.

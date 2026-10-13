---
id: kc_cache-basics
title: Cache as a small fast copy of a larger slower store
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
  locator: A cache is a small, fast store that keeps copies of data from a larger, slower store, so that repeated requests can be served quickly.
relations:
- type: required_by
  target: kc_hit-ratio
- type: required_by
  target: kc_locality-of-reference
- type: required_by
  target: kc_lru-eviction
- type: required_by
  target: kc_cache-invalidation
status: confirmed
created: 2026-10-13
---
## Essence
A cache is a small, fast store that keeps copies of data from a larger, slower store so repeated requests are served quickly.

## Propositions
- P1. A cache is a small, fast store that holds copies of data from a larger, slower store.
- P2. Its purpose is to serve repeated requests quickly.
- P3. CPUs cache main memory, web browsers cache pages, and databases cache disk blocks in RAM.
- P4. A request found in the cache is a hit; a request that must go to the slow store is a miss.

## Intuition and contrasts
- Intuition: Keep the things you use often on your desk instead of walking to the archive each time.

## Related
- required_by:: [[Hit ratio and average access time]]
- required_by:: [[Locality of reference]]
- required_by:: [[LRU eviction and its implementation]]
- required_by:: [[Cache invalidation and stale copies]]

## Review cards
What is a cache?::A small, fast store that keeps copies of data from a larger, slower store, so repeated requests can be served quickly.
What is the difference between a cache hit and a cache miss?::A hit is a request found in the cache; a miss is a request that must go to the slow store.
Name three places where caches are used.::CPUs cache main memory, web browsers cache pages, and databases cache disk blocks in RAM.

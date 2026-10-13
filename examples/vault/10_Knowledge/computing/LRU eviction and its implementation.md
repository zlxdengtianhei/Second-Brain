---
id: kc_lru-eviction
title: LRU eviction and its implementation
kind: mechanism
domain: computing
pillar: Systems
topic: Caching
tags:
- review
- flashcards/computing
sources:
- path: 40_Sources/40169515563ffea6/Caches.md
  sha256: 40169515563ffea61cc6daf6316d688d750bff6170c44dbb565859ba90c95d1c
  locator: Least recently used (LRU) eviction removes the item that has gone unused for the longest time, betting on temporal locality.
relations:
- type: depends_on
  target: kc_locality-of-reference
  note: LRU bets on temporal locality.
- type: depends_on
  target: kc_cache-basics
  note: Eviction applies to a full cache with hits and misses.
- type: depends_on
  target: kc_hash-table-lookup
  tier: extension
status: confirmed
created: 2026-10-13
---
## Essence
LRU evicts the item unused for the longest time, usually implemented with a hash table plus a doubly linked list ordered by last use, and it fails on one large scan.

## Propositions
- P1. When the cache is full and a new item must be stored, an old item has to be evicted.
- P2. Least recently used (LRU) eviction removes the item that has gone unused for the longest time, betting on temporal locality.
- P3. A common implementation combines a hash table, which finds an item in constant time, with a doubly linked list ordered by last use.
- P4. On a hit, the item moves to the front of the list.
- P5. On a miss with a full cache, the item at the back of the list is evicted and the new item is added at the front.
- P6. LRU does badly on one large scan that touches every item once: the scan pushes out everything useful.

## Diagram
```mermaid
%% diagram v1 props=E,P4,P5 sha=35b595c0
flowchart TB
  n1{"hit or miss?"}
  n2(["item moves to the front of the list"])
  n3["item at the back of the list is evicted"]
  n4(["new item is added at the front"])
  n1 -->|"hit"| n2
  n1 -->|"miss, cache full"| n3
  n3 --> n4
```

## Intuition and contrasts
- Intuition: The list is a queue of recency: front is freshest, back is the next to go.

## Related
- depends_on:: [[Locality of reference]] (LRU bets on temporal locality.)
- depends_on:: [[Cache as a small fast copy of a larger slower store]] (Eviction applies to a full cache with hits and misses.)

## Review cards
Which item does LRU evict, and what is it betting on?::The item unused for the longest time; it bets on temporal locality.
Which two structures are combined in a common LRU implementation, and what does each do?::A hash table finds an item in constant time; a doubly linked list ordered by last use tracks recency.
What happens to the list on a hit, and on a miss with a full cache?::On a hit the item moves to the front. On a miss with a full cache the back item is evicted and the new item is added at the front.
When does LRU perform badly?::On one large scan that touches every item once, because it pushes out everything useful.

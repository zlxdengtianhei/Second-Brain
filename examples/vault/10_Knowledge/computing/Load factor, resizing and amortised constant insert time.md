---
id: kc_load-factor-and-amortised-resizing
title: Load factor, resizing and amortised constant insert time
kind: mechanism
domain: computing
pillar: Data structures
topic: Hashing
tags:
- review
- flashcards/computing
sources:
- path: 40_Sources/56a0244c9bc5ca43/Hash tables and Bloom filters.md
  sha256: 56a0244c9bc5ca436cb6b5fac6e9b3aea9bada1f4f40601046189b58e65311d2
  locator: The load factor is the number of stored items divided by the number of buckets. As it rises, collisions become more frequent and lookups slow down.
relations:
- type: depends_on
  target: kc_hash-collisions-and-resolution
  note: A higher load factor means more collisions.
- type: part_of
  target: kc_hash-table-lookup
  note: Resizing is how a hash table keeps its O(1) average as it grows.
  tier: extension
status: confirmed
created: 2026-10-13
---
## Essence
The load factor is items divided by buckets; when it passes a threshold the table grows and rehashes every item, which costs O(n) once but keeps the average insert at O(1).

## Propositions
- P1. The load factor is the number of stored items divided by the number of buckets.
- P2. As the load factor rises, collisions become more frequent and lookups slow down.
- P3. Most implementations grow the array, often by doubling it, when the load factor passes a threshold such as 0.75, and then move every item to its new bucket.
- P4. One resize costs O(n), but because resizes are rare the average cost per insert stays O(1).
- P5. This average over many inserts is called amortised constant time.

## Intuition and contrasts
- Intuition: A rare expensive step is paid for by many cheap inserts, so the cost per insert stays flat on average.
- Contrast: Amortised O(1) does not mean every insert is O(1): the single insert that triggers a resize costs O(n).

## Related
- depends_on:: [[Hash collisions and how chaining and open addressing handle them]] (A higher load factor means more collisions.)

<details><summary>Extension: possible relations (1)</summary>

- [[Hash table lookup and its average and worst case cost]] — part_of (Resizing is how a hash table keeps its O(1) average as it grows.)

</details>

## Review cards
What is the load factor of a hash table?::The number of stored items divided by the number of buckets.
When and how do most hash tables resize?::When the load factor passes a threshold such as 0.75, they grow the array, often by doubling it, and move every item to its new bucket.
Why is the average cost per insert still O(1) although one resize costs O(n)?::Resizes are rare, so their cost is spread over many inserts; this is called amortised constant time.

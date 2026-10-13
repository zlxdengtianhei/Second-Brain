---
id: kc_hash-table-lookup
title: Hash table lookup and its average and worst case cost
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
  locator: A hash table stores key-value pairs in an array of buckets.
relations:
- type: depends_on
  target: kc_hash-function-properties
  note: The even spread of the hash decides whether the O(1) average holds.
- type: required_by
  target: kc_hash-collisions-and-resolution
  note: Collisions are what push lookups toward the worst case.
- type: required_by
  target: kc_lru-eviction
  note: LRU implementations use a hash table for constant time access.
  tier: extension
- type: has_part
  target: kc_load-factor-and-amortised-resizing
  tier: extension
- type: contrasts_with
  target: kc_bloom-filter-mechanism
  tier: extension
status: confirmed
created: 2026-10-13
---
## Essence
A hash table finds a key by hashing it to a bucket index, which gives O(1) average time when keys are spread evenly and O(n) in the worst case when many keys share a bucket.

## Propositions
- P1. A hash table stores key-value pairs in an array of buckets.
- P2. To find a key, compute its hash, reduce the hash to a bucket index (for example the hash modulo the number of buckets), and look in that bucket.
- P3. When keys are spread evenly, a lookup, insert or delete takes constant time on average, O(1), no matter how many items are stored.
- P4. In the worst case, when many keys fall into the same bucket, a lookup degrades to O(n).

## Intuition and contrasts
- Intuition: The hash turns the key into an address, so the table jumps straight to one bucket instead of scanning all items.

## Related
- depends_on:: [[What makes a good hash function for a table]] (The even spread of the hash decides whether the O(1) average holds.)
- required_by:: [[Hash collisions and how chaining and open addressing handle them]] (Collisions are what push lookups toward the worst case.)

<details><summary>Extension: possible relations (3)</summary>

- [[LRU eviction and its implementation]] — required_by (LRU implementations use a hash table for constant time access.)
- [[Load factor, resizing and amortised constant insert time]] — has_part
- [[Bloom filter operation and its one-sided error]] — contrasts_with

</details>

## Review cards
How does a hash table find the bucket for a key?::It computes the hash of the key and reduces it to a bucket index, for example the hash modulo the number of buckets.
What is the average cost of lookup, insert or delete in a hash table, and under what condition?::Constant time, O(1), when keys are spread evenly over the buckets, no matter how many items are stored.
When does a hash table lookup degrade to O(n)?::In the worst case, when many keys fall into the same bucket.

---
id: kc_hash-collisions-and-resolution
title: Hash collisions and how chaining and open addressing handle them
kind: contrast
domain: computing
pillar: Data structures
topic: Hashing
tags:
- review
- flashcards/computing
sources:
- path: 40_Sources/56a0244c9bc5ca43/Hash tables and Bloom filters.md
  sha256: 56a0244c9bc5ca436cb6b5fac6e9b3aea9bada1f4f40601046189b58e65311d2
  locator: Two different keys can map to the same bucket. This is a collision, and it cannot be avoided, because there are more possible keys than buckets.
relations:
- type: depends_on
  target: kc_hash-table-lookup
  note: Collisions are defined by the bucket mapping of a hash table.
- type: required_by
  target: kc_load-factor-and-amortised-resizing
  note: The load factor measures how often collisions will happen.
  tier: extension
status: confirmed
created: 2026-10-13
---
## Essence
Collisions between different keys cannot be avoided, so a table either keeps a list per bucket (separate chaining) or probes other buckets in a fixed order (open addressing).

## Propositions
- P1. Two different keys can map to the same bucket; this is a collision.
- P2. Collisions cannot be avoided, because there are more possible keys than buckets.
- P3. Separate chaining keeps a small list in each bucket and searches that list.
- P4. Open addressing stores every item in the array itself and, on a collision, probes other buckets in a fixed order until it finds a free one.

## Intuition and contrasts
- Intuition: Chaining lets a crowded bucket hold several items; open addressing moves the extra item to another free bucket.
- Contrast: Chaining stores items outside the main array in per-bucket lists, while open addressing keeps every item in the array itself.

## Related
- depends_on:: [[Hash table lookup and its average and worst case cost]] (Collisions are defined by the bucket mapping of a hash table.)

<details><summary>Extension: possible relations (1)</summary>

- [[Load factor, resizing and amortised constant insert time]] — required_by (The load factor measures how often collisions will happen.)

</details>

## Review cards
Why can hash collisions not be avoided?::There are more possible keys than buckets, so some different keys must map to the same bucket.
How does separate chaining handle a collision?::Each bucket keeps a small list, and a lookup searches the list in that bucket.
How does open addressing handle a collision?::All items stay in the array itself; on a collision it probes other buckets in a fixed order until it finds a free one.

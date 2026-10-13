---
id: kc_hash-function-properties
title: What makes a good hash function for a table
kind: concept
domain: computing
pillar: Data structures
topic: Hashing
tags:
- review
- flashcards/computing
sources:
- path: 40_Sources/56a0244c9bc5ca43/Hash tables and Bloom filters.md
  sha256: 56a0244c9bc5ca436cb6b5fac6e9b3aea9bada1f4f40601046189b58e65311d2
  locator: A hash function maps a key, such as a string, to a fixed-size number.
relations:
- type: required_by
  target: kc_hash-table-lookup
  note: A hash table computes the hash of the key to find its bucket.
- type: required_by
  target: kc_bloom-filter-mechanism
  note: A Bloom filter uses k hash functions to choose the bits to set or check.
  tier: extension
status: confirmed
created: 2026-10-13
---
## Essence
A hash function maps a key to a fixed-size number, and for tables it must be fast, deterministic and evenly spreading, but not necessarily cryptographic.

## Propositions
- P1. A hash function maps a key, such as a string, to a fixed-size number.
- P2. A good hash function for a table is fast.
- P3. It is deterministic: the same key always gives the same number.
- P4. It spreads keys evenly over its output range, so that similar keys land in unrelated places.
- P5. Hash functions for tables do not need to be cryptographic.
- P6. Cryptographic hashes such as SHA-256 add resistance against deliberately constructed collisions, at a higher cost.

## Intuition and contrasts
- Intuition: Think of the hash as a scrambler: the same input always gives the same output, but neighbouring inputs end up far apart.
- Contrast: A table hash and a cryptographic hash differ in purpose: the table hash only needs speed and even spread, while the cryptographic hash also resists collisions that someone builds on purpose, and pays for that in speed.

## Related
- required_by:: [[Hash table lookup and its average and worst case cost]] (A hash table computes the hash of the key to find its bucket.)

<details><summary>Extension: possible relations (1)</summary>

- [[Bloom filter operation and its one-sided error]] — required_by (A Bloom filter uses k hash functions to choose the bits to set or check.)

</details>

## Review cards
Which three properties make a hash function good for a hash table?::It is fast, deterministic (the same key always gives the same number) and spreads keys evenly over its output range.
Why is a cryptographic hash such as SHA-256 usually not needed for a hash table?::Table hashes do not need resistance against deliberately constructed collisions, and that resistance costs more time.
What should a good table hash do with similar keys?::Place them in unrelated places over the output range.

---
id: kc_big-o-growth-classes
title: Common Big-O growth classes
kind: fact
domain: computing
pillar: Algorithms
topic: Complexity
tags:
- review
- flashcards/computing
sources:
- path: 40_Sources/1b8473ef2e8b5ef9/Big-O and binary search.md
  sha256: 1b8473ef2e8b5ef96d6145ddbf51e4a64ca861460e2cbbe5c71d2b4e2ae5530c
  locator: 'Common classes, from slowest-growing to fastest-growing: O(1) constant, O(log n) logarithmic, O(n) linear, O(n log n), O(n²) quadratic, O(2ⁿ) exponential.'
relations:
- type: part_of
  target: kc_big-o-notation
  note: The classes are the vocabulary used when stating Big-O results.
- type: has_part
  target: kc_logarithmic-time
  tier: extension
status: confirmed
created: 2026-10-13
---
## Essence
The usual Big-O classes, ordered by growth, are O(1), O(log n), O(n), O(n log n), O(n²) and O(2ⁿ).

## Propositions
- P1. From slowest-growing to fastest-growing, the common classes are O(1) constant, O(log n) logarithmic, O(n) linear, O(n log n), O(n²) quadratic and O(2ⁿ) exponential.
- P2. A class earlier in this list grows more slowly with input size than a class later in the list.

## Intuition and contrasts
None yet.

## Related
- part_of:: [[Big-O notation]] (The classes are the vocabulary used when stating Big-O results.)

<details><summary>Extension: possible relations (1)</summary>

- [[Logarithmic time]] — has_part

</details>

## Review cards
List the common Big-O classes from slowest-growing to fastest-growing.::O(1) constant, O(log n) logarithmic, O(n) linear, O(n log n), O(n²) quadratic, O(2ⁿ) exponential.
Which grows faster: O(n log n) or O(n²)?::O(n²) grows faster; it comes after O(n log n) in the order.

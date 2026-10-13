---
id: kc_logarithmic-time
title: Logarithmic time
kind: concept
domain: computing
pillar: Algorithms
topic: Complexity
tags:
- review
- flashcards/computing
sources:
- path: 40_Sources/1b8473ef2e8b5ef9/Big-O and binary search.md
  sha256: 1b8473ef2e8b5ef96d6145ddbf51e4a64ca861460e2cbbe5c71d2b4e2ae5530c
  locator: An algorithm runs in logarithmic time, O(log n), when each step cuts the remaining problem by a constant factor.
relations:
- type: part_of
  target: kc_big-o-growth-classes
  note: O(log n) is one of the common growth classes.
  tier: extension
- type: has_example
  target: kc_binary-search-procedure
status: confirmed
created: 2026-10-13
---
## Essence
An algorithm runs in O(log n) time when each step cuts the remaining problem by a constant factor, so growing the input by that factor adds only a constant number of steps; for binary search, doubling the array adds one comparison.

## Propositions
- P1. An algorithm runs in logarithmic time, O(log n), when each step cuts the remaining problem by a constant factor.
- P2. Binary search on a million sorted items needs at most about 20 comparisons, because 2²⁰ is about a million.
- P3. For binary search, which halves the range on each pass, doubling the array adds only one more comparison.
- P4. Balanced search trees give the same logarithmic cost for lookups while also allowing fast inserts and deletes.

## Intuition and contrasts
- Intuition: Each step discards a fixed fraction of the work, so the number of steps is how many times n can be cut down to 1.

## Related
- has_example:: [[Binary search procedure]]

<details><summary>Extension: possible relations (1)</summary>

- [[Common Big-O growth classes]] — part_of (O(log n) is one of the common growth classes.)

</details>

## Review cards
When does an algorithm run in logarithmic time?::When each step cuts the remaining problem by a constant factor.
How many comparisons does binary search need at most on a million sorted items, and why?::About 20, because 2²⁰ is about a million.
What does doubling the size of the array cost a binary search?::Only one more comparison, because each pass halves the range.

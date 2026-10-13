---
id: kc_big-o-notation
title: Big-O notation
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
  locator: Big-O notation describes how the running time or memory use of an algorithm grows as its input grows, ignoring constant factors and smaller terms.
relations:
- type: has_part
  target: kc_big-o-growth-classes
  note: The common growth classes are the standard vocabulary of Big-O.
status: confirmed
created: 2026-10-13
---
## Essence
Big-O notation describes how an algorithm's running time or memory use grows with input size, ignoring constant factors and smaller terms.

## Propositions
- P1. Big-O notation describes how the running time or memory use of an algorithm grows as its input grows.
- P2. Big-O ignores constant factors and smaller terms: 3n + 5 steps is O(n), and n²/2 steps is O(n²).
- P3. Big-O gives an upper bound on growth, so it tells how an algorithm scales, not how fast it is on one particular small input.
- P4. An O(n²) algorithm with small constants can beat an O(n log n) algorithm on short lists.

## Intuition and contrasts
- Intuition: Big-O looks at the shape of the growth curve for large inputs, not at the exact step count for one input.
- Contrast: Big-O is not a prediction of actual speed on a small input; a lower class does not guarantee a faster run on short lists.

## Related
- has_part:: [[Common Big-O growth classes]] (The common growth classes are the standard vocabulary of Big-O.)

## Review cards
What does Big-O notation describe?::How the running time or memory use of an algorithm grows as its input grows, ignoring constant factors and smaller terms.
What Big-O class is an algorithm that takes 3n + 5 steps, and one that takes n²/2 steps?::3n + 5 is O(n); n²/2 is O(n²).
Why can an O(n²) algorithm beat an O(n log n) algorithm in practice?::Big-O is an upper bound on growth, not a speed on a particular input; with small constants an O(n²) algorithm can win on short lists.

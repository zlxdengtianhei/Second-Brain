---
id: kc_binary-search-midpoint-overflow
title: Binary search midpoint overflow bug
kind: fact
domain: computing
pillar: Algorithms
topic: Searching
tags:
- review
- flashcards/computing
sources:
- path: 40_Sources/1b8473ef2e8b5ef9/Big-O and binary search.md
  sha256: 1b8473ef2e8b5ef96d6145ddbf51e4a64ca861460e2cbbe5c71d2b4e2ae5530c
  locator: A classic bug is computing the middle as (low + high) / 2 with fixed-width integers, which can overflow on very large arrays; low + (high − low) / 2 avoids it.
relations:
- type: part_of
  target: kc_binary-search-procedure
  note: The bug sits in the step that finds the middle element.
status: confirmed
created: 2026-10-13
---
## Essence
Computing the middle as (low + high) / 2 with fixed-width integers can overflow on very large arrays; low + (high − low) / 2 avoids it.

## Propositions
- P1. A classic binary search bug is computing the middle as (low + high) / 2 with fixed-width integers.
- P2. The sum low + high can overflow on very large arrays.
- P3. Computing the middle as low + (high − low) / 2 avoids the overflow.

## Intuition and contrasts
None yet.

## Related
- part_of:: [[Binary search procedure]] (The bug sits in the step that finds the middle element.)

## Review cards
Why can (low + high) / 2 be a bug in binary search?::With fixed-width integers the sum can overflow on very large arrays.
Which formula for the middle avoids the overflow?::low + (high − low) / 2.

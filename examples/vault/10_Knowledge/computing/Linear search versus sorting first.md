---
id: kc_linear-search-and-sorting-cost
title: Linear search versus sorting first
kind: contrast
domain: computing
pillar: Algorithms
topic: Searching
tags:
- review
- flashcards/computing
sources:
- path: 40_Sources/1b8473ef2e8b5ef9/Big-O and binary search.md
  sha256: 1b8473ef2e8b5ef96d6145ddbf51e4a64ca861460e2cbbe5c71d2b4e2ae5530c
  locator: Linear search checks every element in turn. It needs no sorting and takes O(n) time.
relations:
- type: contrasts_with
  target: kc_binary-search-procedure
  note: Linear search needs no sorted data; binary search does.
status: confirmed
created: 2026-10-13
---
## Essence
Linear search needs no sorting and takes O(n), so it is right for a single lookup in unsorted data, while sorting first (O(n log n)) pays off only when many lookups follow.

## Propositions
- P1. Linear search checks every element in turn.
- P2. Linear search needs no sorting and takes O(n) time.
- P3. For a single lookup in unsorted data, linear search is the right choice, because sorting first costs O(n log n).
- P4. Sorting pays off when many lookups will follow.

## Intuition and contrasts
- Intuition: Sorting is a one-time cost that only helps if it is spread over many fast lookups.
- Contrast: Binary search is faster per lookup than linear search, but it needs sorted data; the sorting cost can outweigh the gain for one lookup.

## Related
- contrasts_with:: [[Binary search procedure]] (Linear search needs no sorted data; binary search does.)

## Review cards
Why is linear search the right choice for a single lookup in unsorted data?::It takes O(n) and needs no sorting, while sorting first costs O(n log n).
When does sorting the data first pay off?::When many lookups will follow.

---
id: ex_binary_search
title: Binary search
kind: mechanism
domain: algo
tags: [review, flashcards/algo]
sources:
  - path: 40_Sources/example/algorithms-notes.md
    sha256: 0000000000000000000000000000000000000000000000000000000000000000
    locator: chunk0
relations:
  - type: depends_on
    target: ex_array
  - type: depends_on
    target: ex_loop_invariant
  - type: similar_to
    target: ex_sorting
    tier: extension
status: confirmed
pillar: Searching
topic: Divide and conquer search
---
## 本质
Binary search finds a target in a sorted array by halving the search interval each step, so it needs O(log n) comparisons.

## 命题
- P1. The array must be sorted; otherwise discarding half the interval can drop the target.
- P2. The loop invariant is: if the target exists, it lies inside [lo, hi].
- P3. Each step removes at least half of the remaining candidates.

## 复习卡
What precondition does binary search need?::The array must be sorted.
What is the loop invariant of binary search?::If the target exists, it lies inside [lo, hi]. <!--SR:!2026-03-20,5,250-->
- How many comparisons does binary search need?::O(log n), because each step halves the interval.
```text
Code::blocks are not questions
```

### Notes
Inside a sub-heading::still part of the review section

## 相关
- [[Arrays]] — depends_on
Not a question::outside the section

---
id: kc_expanding-intervals
title: Expanding review intervals
kind: mechanism
domain: learning
pillar: Study strategies
topic: Spacing
tags:
- review
- flashcards/learning
sources:
- path: 40_Sources/2d02b30e61aee4a9/Forgetting and spacing.md
  sha256: 2d02b30e61aee4a9cf42511eae9ddc09c8d25d59af18d3e53258ca3d1f1526da
  locator: If each successful review makes a memory more stable, the next review can wait longer.
relations:
- type: depends_on
  target: kc_memory-stability
  note: Longer intervals are justified because successful reviews increase stability.
- type: depends_on
  target: kc_spacing-effect
  note: Expanding intervals apply the idea of spreading study over time.
  tier: extension
- type: required_by
  target: kc_recall-target-scheduling
status: confirmed
created: 2026-10-13
---
## Essence
Because each successful review makes a memory more stable, spaced repetition systems let the interval to the next review grow after every success and shorten it after a failure.

## Propositions
- P1. If each successful review makes a memory more stable, the next review can wait longer.
- P2. Spaced repetition systems use this: after each successful review the interval to the next review grows.
- P3. An example sequence of intervals is 1 day, 3 days, 8 days, 3 weeks.
- P4. A failed review shortens the interval again.

## Diagram
```mermaid
%% diagram v1 props=E,P2,P4 sha=0672c171
flowchart TB
  n1["Review"]
  n2{"Review successful?"}
  n3(["Interval to next review grows"])
  n4(["Interval shortens"])
  n1 --> n2
  n2 -->|"success"| n3
  n2 -->|"failed"| n4
```

## Intuition and contrasts
None yet.

## Related
- depends_on:: [[Memory stability]] (Longer intervals are justified because successful reviews increase stability.)
- required_by:: [[Scheduling reviews at a target recall probability]]

<details><summary>Extension: possible relations (1)</summary>

- [[The spacing effect]] — depends_on (Expanding intervals apply the idea of spreading study over time.)

</details>

## Review cards
Why can review intervals grow in a spaced repetition system?::Each successful review makes the memory more stable, so the next review can wait longer.
What happens to the interval after a failed review?::It is shortened again.
Give an example of expanding intervals.::1 day, 3 days, 8 days, 3 weeks.

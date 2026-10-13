---
id: kc_recall-target-scheduling
title: Scheduling reviews at a target recall probability
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
  locator: Schedulers such as the SM-2 algorithm and FSRS estimate, for each card, when the probability of recall will fall to a target level (often 90 percent) and schedule the review for about that moment.
relations:
- type: depends_on
  target: kc_expanding-intervals
  note: Schedulers set the growing intervals per card by estimated recall probability.
- type: depends_on
  target: kc_memory-stability
  note: The target recall level is the same threshold used to define stability.
status: confirmed
created: 2026-10-13
---
## Essence
Schedulers such as SM-2 and FSRS estimate for each card when the probability of recall will fall to a target level, often 90 percent, and schedule the review for about that moment.

## Propositions
- P1. Schedulers such as the SM-2 algorithm and FSRS estimate, for each card, when the probability of recall will fall to a target level.
- P2. The target level is often 90 percent.
- P3. The review is scheduled for about the moment the target is reached.
- P4. Reviewing much earlier spends time on something that is still well known.
- P5. Reviewing much later means relearning the material.

## Intuition and contrasts
None yet.

## Related
- depends_on:: [[Expanding review intervals]] (Schedulers set the growing intervals per card by estimated recall probability.)
- depends_on:: [[Memory stability]] (The target recall level is the same threshold used to define stability.)

## Review cards
What do SM-2 and FSRS estimate for each card?::When the probability of recall will fall to a target level, often 90 percent; the review is scheduled for about that moment.
What is the cost of reviewing much earlier than scheduled?::It spends time on something that is still well known.
What is the cost of reviewing much later than scheduled?::The material has to be relearned.

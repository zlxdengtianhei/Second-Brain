---
id: kc_p-value-meaning
title: What a p-value is and is not
kind: concept
domain: statistics
pillar: Inference
topic: Hypothesis testing
tags:
- review
- flashcards/statistics
sources:
- path: 40_Sources/6db20611a7f6de8d/Confidence intervals and p-values.md
  sha256: 6db20611a7f6de8d8b41ee113132f558e514c043c993b41e1ec7e7b575814f6d
  locator: A p-value is the probability of getting a result at least as extreme as the one observed
relations:
- type: required_by
  target: kc_statistical-vs-practical-significance
- type: required_by
  target: kc_multiple-comparisons-problem
status: confirmed
created: 2026-10-13
---
## Essence
A p-value is the probability of a result at least as extreme as the observed one if the null hypothesis is true; it is not the probability that the null is true or that chance produced the result.

## Propositions
- P1. A p-value is the probability of getting a result at least as extreme as the one observed, assuming the null hypothesis (usually that there is no effect) is true.
- P2. A small p-value means the data would be surprising if there were no effect.
- P3. A p-value is not the probability that the null hypothesis is true.
- P4. A p-value is not the probability that the result happened by chance.
- P5. The common threshold of 0.05 is a convention, not a law of nature.

## Intuition and contrasts
- Intuition: The p-value answers 'how surprising are these data if nothing is going on?', not 'how likely is it that nothing is going on?'.
- Contrast: The p-value is conditional on the null being true, so it cannot be the probability of the null itself.

## Related
- required_by:: [[Statistical significance versus practical significance]]
- required_by:: [[Multiple comparisons inflate false discoveries]]

## Review cards
What is a p-value?::The probability of a result at least as extreme as the one observed, assuming the null hypothesis is true.
Is a p-value the probability that the null hypothesis is true?::No. It is also not the probability that the result happened by chance.
Is the 0.05 threshold for significance a law of nature?::No, it is a convention.

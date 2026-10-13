---
id: kc_base-rate-neglect
title: Base rate neglect
kind: concept
domain: statistics
pillar: Probability
topic: Bayesian reasoning
tags:
- review
- flashcards/statistics
sources:
- path: 40_Sources/a943deecfe0a61bc/Conditional probability and Bayes rule.md
  sha256: a943deecfe0a61bc3da77fc188fb0642a1d3f7cb2e220f44b188aa78474f4916
  locator: People asked about the screening example usually answer close to 90 percent. They focus on the accuracy of the test and ignore how rare the disease is. This error is called base rate neglect.
relations:
- type: depends_on
  target: kc_positive-predictive-value
  note: The error ignores that the positive predictive value depends on the base rate.
- type: similar_to
  target: kc_confusion-of-the-inverse
  note: Answering 90 percent treats P(positive | disease) as P(disease | positive).
  tier: extension
- type: has_example
  target: kc_screening-test-positive-result
  tier: extension
status: confirmed
created: 2026-10-13
---
## Essence
Base rate neglect is the error of focusing on the accuracy of a test and ignoring how rare the condition is, which makes people answer close to 90 percent in the screening example.

## Propositions
- P1. People asked about the screening example usually answer close to 90 percent.
- P2. They focus on the accuracy of the test and ignore how rare the disease is.
- P3. This error is called base rate neglect.
- P4. Writing the problem as counts out of 1,000 people makes the correct answer much easier to see than writing it with percentages.

## Intuition and contrasts
- Intuition: Counting people instead of percentages makes the many healthy people who test positive visible.

## Related
- depends_on:: [[Positive predictive value]] (The error ignores that the positive predictive value depends on the base rate.)

<details><summary>Extension: possible relations (2)</summary>

- [[Confusion of the inverse]] — similar_to (Answering 90 percent treats P(positive | disease) as P(disease | positive).)
- [[Screening test with a rare disease]] — has_example

</details>

## Review cards
What is base rate neglect?::Focusing on the accuracy of a test and ignoring how rare the condition is.
What answer do people usually give in the screening example, and what is the correct one?::They answer close to 90 percent; the correct answer is about 9 percent.
What presentation makes the correct answer of a screening problem easier to see?::Counts out of 1,000 people instead of percentages.

---
id: kc_positive-predictive-value
title: Positive predictive value
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
  locator: The share of positive results that are true is called the positive predictive value. It depends on the base rate, the share of people who have the condition before testing. The same test used in a group where half the people are sick has a positive predictive value of about 91 percent.
relations:
- type: depends_on
  target: kc_bayes-rule
  note: The positive predictive value is a posterior that depends on the prior (the base rate).
- type: similar_to
  target: kc_screening-test-positive-result
  note: The 9 percent in the screening example is this quantity at a 1 percent base rate.
  tier: extension
- type: required_by
  target: kc_base-rate-neglect
- type: has_example
  target: kc_bloom-filter-as-first-check
  tier: extension
status: confirmed
created: 2026-10-13
---
## Essence
The positive predictive value is the share of positive results that are true, and it depends on the base rate of the condition before testing.

## Propositions
- P1. The positive predictive value is the share of positive results that are true.
- P2. It depends on the base rate, the share of people who have the condition before testing.
- P3. The same test used in a group where half the people are sick has a positive predictive value of about 91 percent.

## Intuition and contrasts
- Contrast: Sensitivity is the share of sick people the test detects. The positive predictive value is the share of positive results that are true, and the two can differ greatly.

## Related
- depends_on:: [[Bayes' rule]] (The positive predictive value is a posterior that depends on the prior (the base rate).)
- required_by:: [[Base rate neglect]]

<details><summary>Extension: possible relations (1)</summary>

- [[Screening test with a rare disease]] — similar_to (The 9 percent in the screening example is this quantity at a 1 percent base rate.)

</details>

## Review cards
What is the positive predictive value?::The share of positive results that are true.
What does the positive predictive value of a test depend on besides the test itself?::The base rate, the share of people who have the condition before testing.
What is the positive predictive value of the example test in a group where half the people are sick?::About 91 percent.

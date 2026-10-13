---
id: kc_screening-test-positive-result
title: Screening test with a rare disease
kind: derivation
domain: statistics
pillar: Probability
topic: Bayesian reasoning
tags:
- review
- flashcards/statistics
sources:
- path: 40_Sources/a943deecfe0a61bc/Conditional probability and Bayes rule.md
  sha256: a943deecfe0a61bc3da77fc188fb0642a1d3f7cb2e220f44b188aa78474f4916
  locator: A disease affects 1 in 100 people. A test detects it in 90 percent of the people who have it (its sensitivity) and gives a false positive in 9 percent of the people who do not.
relations:
- type: example_of
  target: kc_bayes-rule
  note: The counts reproduce the computation of the posterior from prior and likelihoods.
- type: example_of
  target: kc_base-rate-neglect
  note: 'This screening example is where base rate neglect shows up: people who answer about 90 percent ignore how rare the disease is.'
  tier: extension
- type: similar_to
  target: kc_positive-predictive-value
  tier: extension
- type: similar_to
  target: kc_bloom-filter-as-first-check
status: confirmed
created: 2026-10-13
---
## Essence
With a disease rate of 1 in 100, 90 percent sensitivity and 9 percent false positives, a positive result means only about a 9 percent chance of disease.

## Propositions
- P1. A disease affects 1 in 100 people, and the test detects it in 90 percent of the people who have it (its sensitivity).
- P2. The test gives a false positive in 9 percent of the people who do not have the disease.
- P3. Out of 1,000 people, 10 have the disease and 9 of them test positive.
- P4. Of the 990 people without the disease, about 89 test positive.
- P5. So of roughly 98 positive results only 9 are true.
- P6. A positive result therefore means about a 9 percent chance of having the disease, not 90 percent.

## Intuition and contrasts
None yet.

## Related
- example_of:: [[Bayes' rule]] (The counts reproduce the computation of the posterior from prior and likelihoods.)

<details><summary>Extension: possible relations (2)</summary>

- [[Base rate neglect]] — example_of (This screening example is where base rate neglect shows up: people who answer about 90 percent ignore how rare the disease is.)
- [[Positive predictive value]] — similar_to

</details>

## Review cards
A disease affects 1 in 100 people. The test has 90 percent sensitivity and a 9 percent false positive rate. What is the chance of disease after a positive result?::About 9 percent: of roughly 98 positives out of 1,000 people, only 9 are true.
In the screening example, how many of 1,000 people test positive, and how many of those are true positives?::Roughly 98 test positive (9 with the disease, about 89 without), so only 9 are true positives.

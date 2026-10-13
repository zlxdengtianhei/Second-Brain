---
id: kc_conditional-probability
title: Conditional probability
kind: concept
domain: statistics
pillar: Probability
topic: Conditional probability
tags:
- review
- flashcards/statistics
sources:
- path: 40_Sources/a943deecfe0a61bc/Conditional probability and Bayes rule.md
  sha256: a943deecfe0a61bc3da77fc188fb0642a1d3f7cb2e220f44b188aa78474f4916
  locator: 'P(A | B) is the probability of A given that B is true. It is computed by looking only at the cases where B holds: P(A | B) = P(A and B) / P(B).'
relations:
- type: required_by
  target: kc_bayes-rule
  note: Bayes' rule converts one conditional probability into the other.
- type: contrasts_with
  target: kc_confusion-of-the-inverse
  note: The inverse conditional probability is generally a different number.
  tier: extension
status: confirmed
created: 2026-10-13
---
## Essence
P(A | B) is the probability of A computed only over the cases where B holds, equal to P(A and B) divided by P(B).

## Propositions
- P1. P(A | B) is the probability of A given that B is true.
- P2. It is computed by looking only at the cases where B holds.
- P3. The formula is P(A | B) = P(A and B) / P(B).

## Intuition and contrasts
- Intuition: Restrict attention to the world where B is true, then ask how often A also happens there.

## Related
- required_by:: [[Bayes' rule]] (Bayes' rule converts one conditional probability into the other.)

<details><summary>Extension: possible relations (1)</summary>

- [[Confusion of the inverse]] — contrasts_with (The inverse conditional probability is generally a different number.)

</details>

## Review cards
How is P(A | B) computed from P(A and B) and P(B)?::P(A | B) = P(A and B) / P(B).
Which cases does P(A | B) look at?::Only the cases where B holds.

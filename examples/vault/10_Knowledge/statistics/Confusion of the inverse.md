---
id: kc_confusion-of-the-inverse
title: Confusion of the inverse
kind: contrast
domain: statistics
pillar: Probability
topic: Conditional probability
tags:
- review
- flashcards/statistics
sources:
- path: 40_Sources/a943deecfe0a61bc/Conditional probability and Bayes rule.md
  sha256: a943deecfe0a61bc3da77fc188fb0642a1d3f7cb2e220f44b188aa78474f4916
  locator: In general P(A | B) is not equal to P(B | A). The probability that a person with a cough has the flu is not the same as the probability that a person with the flu has a cough. Mixing up the two is called confusion of the inverse; in a courtroom it is known as the prosecutor's fallacy.
relations:
- type: contrasts_with
  target: kc_conditional-probability
  note: Contrasts the two directions of a conditional probability.
- type: similar_to
  target: kc_base-rate-neglect
  note: Base rate neglect is a typical way this inversion error shows up.
  tier: extension
status: confirmed
created: 2026-10-13
---
## Essence
P(A | B) and P(B | A) are in general different, and treating them as equal is the error called confusion of the inverse or the prosecutor's fallacy.

## Propositions
- P1. In general P(A | B) is not equal to P(B | A).
- P2. The probability that a person with a cough has the flu is not the same as the probability that a person with the flu has a cough.
- P3. Mixing up the two is called confusion of the inverse.
- P4. In a courtroom the same error is known as the prosecutor's fallacy.

## Intuition and contrasts
- Contrast: P(A | B) conditions on B, while P(B | A) conditions on A, so they answer different questions.

## Related
- contrasts_with:: [[Conditional probability]] (Contrasts the two directions of a conditional probability.)

<details><summary>Extension: possible relations (1)</summary>

- [[Base rate neglect]] — similar_to (Base rate neglect is a typical way this inversion error shows up.)

</details>

## Review cards
What is confusion of the inverse?::Mixing up P(A | B) and P(B | A), which are generally not equal.
What is confusion of the inverse called in a courtroom?::The prosecutor's fallacy.
Give an example showing that P(A | B) differs from P(B | A).::The probability that a person with a cough has the flu differs from the probability that a person with the flu has a cough.

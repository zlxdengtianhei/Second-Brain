---
id: kc_statistical-vs-practical-significance
title: Statistical significance versus practical significance
kind: contrast
domain: statistics
pillar: Inference
topic: Hypothesis testing
tags:
- review
- flashcards/statistics
sources:
- path: 40_Sources/6db20611a7f6de8d/Confidence intervals and p-values.md
  sha256: 6db20611a7f6de8d8b41ee113132f558e514c043c993b41e1ec7e7b575814f6d
  locator: With a large enough sample, even a tiny effect gives a small p-value.
relations:
- type: depends_on
  target: kc_p-value-meaning
  note: The contrast presupposes knowing what a small p-value does and does not say.
- type: depends_on
  target: kc_ci-width-precision
  note: Reading the effect together with its interval uses interval precision.
  tier: extension
status: confirmed
created: 2026-10-13
---
## Essence
A large sample can make a tiny effect statistically significant, so a small p-value alone does not show the effect is large enough to matter; report effect size with its confidence interval.

## Propositions
- P1. With a large enough sample, even a tiny effect gives a small p-value.
- P2. Example: a study of 100,000 students might find that a new study app raises exam scores by 0.2 points out of 100, with p < 0.001.
- P3. That effect is statistically significant but too small to matter.
- P4. Reporting the size of the effect with its confidence interval tells readers whether the effect is large enough to care about; a p-value alone does not.

## Intuition and contrasts
- Intuition: The p-value says whether an effect can be told apart from zero; the effect size says whether it is worth caring about.
- Contrast: Statistical significance (small p-value) is not the same as practical importance (a big enough effect).

## Related
- depends_on:: [[What a p-value is and is not]] (The contrast presupposes knowing what a small p-value does and does not say.)

<details><summary>Extension: possible relations (1)</summary>

- [[Interval width shows estimate precision]] — depends_on (Reading the effect together with its interval uses interval precision.)

</details>

## Review cards
Why can a tiny effect have a very small p-value?::With a large enough sample, even a tiny effect gives a small p-value.
What should be reported besides the p-value to show whether an effect matters?::The size of the effect with its confidence interval.
In the study-app example, why does the result not matter despite p < 0.001?::The gain is only 0.2 points out of 100, which is statistically significant but too small to matter.

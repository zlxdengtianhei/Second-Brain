---
id: kc_ci-meaning
title: Meaning of a 95 percent confidence interval
kind: concept
domain: statistics
pillar: Inference
topic: Estimation
tags:
- review
- flashcards/statistics
sources:
- path: 40_Sources/6db20611a7f6de8d/Confidence intervals and p-values.md
  sha256: 6db20611a7f6de8d8b41ee113132f558e514c043c993b41e1ec7e7b575814f6d
  locator: A 95 percent confidence interval comes from a procedure that, over many repeated samples, captures the true value 95 percent of the time.
relations: []
status: confirmed
created: 2026-10-13
---
## Essence
A 95 percent confidence interval is the output of a procedure that captures the true value in 95 percent of repeated samples, not a 95 percent probability statement about one computed interval.

## Propositions
- P1. The 95 percent refers to the procedure: over many repeated samples, the intervals it produces capture the true value 95 percent of the time.
- P2. It does not mean there is a 95 percent probability that the true value lies inside one particular interval that has already been computed.
- P3. The reading 'the true value lies in this interval with 95 percent probability' belongs to Bayesian credible intervals, not to confidence intervals.

## Intuition and contrasts
- Intuition: The guarantee is about the long-run hit rate of the method, like an archer who hits the target 95 times in 100 shots, not about whether one specific arrow already shot has hit.
- Contrast: A confidence interval is not a Bayesian credible interval: only the credible interval supports a probability statement about the true value lying in one computed interval.

## Related
None yet.

## Review cards
What does 95 percent mean in a 95 percent confidence interval?::The procedure captures the true value in 95 percent of repeated samples.
Is there a 95 percent probability that the true value lies inside one already computed 95 percent confidence interval?::No. That reading belongs to Bayesian credible intervals.

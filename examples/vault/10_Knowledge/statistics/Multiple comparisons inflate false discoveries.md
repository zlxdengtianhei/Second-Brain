---
id: kc_multiple-comparisons-problem
title: Multiple comparisons inflate false discoveries
kind: mechanism
domain: statistics
pillar: Inference
topic: Hypothesis testing
tags:
- review
- flashcards/statistics
sources:
- path: 40_Sources/6db20611a7f6de8d/Confidence intervals and p-values.md
  sha256: 6db20611a7f6de8d8b41ee113132f558e514c043c993b41e1ec7e7b575814f6d
  locator: If a researcher tests 20 independent hypotheses that are all false, using a threshold of 0.05, the chance of at least one
relations:
- type: depends_on
  target: kc_p-value-meaning
  note: The inflation arises from applying a fixed p-value threshold repeatedly.
status: confirmed
created: 2026-10-13
---
## Essence
Running many tests at a 0.05 threshold makes at least one false significant result likely, so reporting only significant results produces false discoveries unless the testing is pre-registered, corrected or replicated.

## Propositions
- P1. If a researcher tests 20 independent hypotheses that are all false at a threshold of 0.05, the chance of at least one significant result is about 64 percent.
- P2. Testing many outcomes, subgroups or analysis choices and reporting only the significant ones produces false discoveries.
- P3. Pre-registration, stating the hypothesis before collecting the data, is one remedy.
- P4. The Bonferroni correction adjusts the threshold for the number of tests by dividing 0.05 by the number of tests.
- P5. Repeating the study on new data is another remedy.

## Diagram
```mermaid
%% diagram v1 props=E,P1,P2 sha=787e6e1a
flowchart TB
  n1["many tests at 0.05"]
  n2["reporting only significant results"]
  n3["false discoveries"]
  n1 -->|"likely significant"| n3
  n2 -->|"reporting only significant"| n3
```

## Intuition and contrasts
- Intuition: Each test is a fresh lottery ticket with a 5 percent chance of a false win, so many tickets make a win likely.

## Related
- depends_on:: [[What a p-value is and is not]] (The inflation arises from applying a fixed p-value threshold repeatedly.)

## Review cards
If 20 independent false hypotheses are each tested at 0.05, what is the chance of at least one significant result?::About 64 percent.
How does the Bonferroni correction adjust the threshold?::It divides 0.05 by the number of tests.
Name three remedies for the multiple comparisons problem.::Pre-registration, adjusting the threshold for the number of tests (such as Bonferroni), and repeating the study on new data.

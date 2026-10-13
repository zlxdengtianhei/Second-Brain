---
id: kc_bloom-filter-as-first-check
title: Bloom filter as a cheap first check and the rare-item false positive problem
kind: concept
domain: computing
pillar: Data structures
topic: Probabilistic structures
tags:
- review
- flashcards/computing
sources:
- path: 40_Sources/56a0244c9bc5ca43/Hash tables and Bloom filters.md
  sha256: 56a0244c9bc5ca436cb6b5fac6e9b3aea9bada1f4f40601046189b58e65311d2
  locator: Bloom filters are used as a cheap first check in front of a slow lookup, for example before reading from disk.
relations:
- type: depends_on
  target: kc_bloom-filter-mechanism
  note: It uses the no false negatives and possible false positives property.
- type: similar_to
  target: kc_screening-test-positive-result
  note: The source gives the rare-disease screening test as the same reason.
- type: example_of
  target: kc_positive-predictive-value
  note: A low base rate of members lowers the share of true positives among the probably-yes answers.
  tier: extension
status: confirmed
created: 2026-10-13
---
## Essence
A Bloom filter sits in front of a slow lookup to skip it for definite negatives, but when the checked items are rarely in the set many probably-yes answers are false, so the slow lookup must settle them.

## Propositions
- P1. Bloom filters are used as a cheap first check in front of a slow lookup, for example before reading from disk.
- P2. A definite no from the filter lets the program skip the slow lookup.
- P3. When the items being checked are rarely in the set, a large share of the probably-yes answers can be false positives.
- P4. This has the same cause as a positive screening test for a rare disease being often wrong.
- P5. The slow lookup then settles the answer.

## Diagram
```mermaid
%% diagram v1 props=E,P1,P2,P3,P5 sha=98410946
flowchart TB
  n1(["check item with Bloom filter"])
  n2{"definite no?"}
  n3(["skip the slow lookup"])
  n4(["slow lookup settles the answer"])
  n1 --> n2
  n2 -->|"definite no"| n3
  n2 -->|"probably yes"| n4
```

## Intuition and contrasts
- Intuition: The filter removes most useless slow lookups, but a probably-yes is only a reason to look, not a result.
- Contrast: A probably-yes from a Bloom filter is not a confirmed membership: it must be confirmed by the real lookup.

## Related
- depends_on:: [[Bloom filter operation and its one-sided error]] (It uses the no false negatives and possible false positives property.)
- similar_to:: [[Screening test with a rare disease]] (The source gives the rare-disease screening test as the same reason.)

<details><summary>Extension: possible relations (1)</summary>

- [[Positive predictive value]] — example_of (A low base rate of members lowers the share of true positives among the probably-yes answers.)

</details>

## Review cards
Where is a Bloom filter typically used in a system?::As a cheap first check in front of a slow lookup, for example before reading from disk.
Why can many probably-yes answers be false positives?::When the checked items are rarely in the set, most checks are for absent items, so false positives make up a large share of the positives, as with a positive screening test for a rare disease.
What settles a probably-yes answer from a Bloom filter?::The slow lookup.

---
id: kc_cache-invalidation
title: Cache invalidation and stale copies
kind: concept
domain: computing
pillar: Systems
topic: Caching
tags:
- review
- flashcards/computing
sources:
- path: 40_Sources/40169515563ffea6/Caches.md
  sha256: 40169515563ffea61cc6daf6316d688d750bff6170c44dbb565859ba90c95d1c
  locator: A cached copy becomes stale when the original changes. Systems either give each copy a time-to-live after which it is discarded, or have the original notify the caches when it changes.
relations:
- type: depends_on
  target: kc_cache-basics
  note: Staleness concerns cached copies of an original.
status: confirmed
created: 2026-10-13
---
## Essence
A cached copy becomes stale when the original changes, so systems discard copies by time-to-live or by notification from the original; getting it wrong serves outdated data.

## Propositions
- P1. A cached copy becomes stale when the original changes.
- P2. One approach gives each copy a time-to-live after which it is discarded.
- P3. Another approach has the original notify the caches when it changes.
- P4. Getting invalidation wrong serves outdated data.
- P5. For this reason cache invalidation has a reputation as one of the hard problems in computing.

## Diagram
```mermaid
%% diagram v1 props=E,P1,P2,P3 sha=7907dd40
flowchart TB
  n1["time-to-live for each copy"]
  n2["original notifies the caches"]
  n3["stale copy discarded"]
  n1 -->|"discarded after"| n3
  n2 -->|"original notifies"| n3
```

## Intuition and contrasts
- Intuition: A copy is only as good as its last check against the original.

## Related
- depends_on:: [[Cache as a small fast copy of a larger slower store]] (Staleness concerns cached copies of an original.)

## Review cards
When does a cached copy become stale?::When the original changes.
What are the two ways systems handle stale copies?::Give each copy a time-to-live after which it is discarded, or have the original notify the caches when it changes.
What is the consequence of getting cache invalidation wrong?::Outdated data is served.

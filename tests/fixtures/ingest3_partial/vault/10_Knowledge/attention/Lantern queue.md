---
id: kc_lantern-queue
title: Lantern queue
kind: concept
domain: attention
pillar: Token routing
topic: Overview
tags:
- review
- flashcards/attention
aliases:
- lantern list
sources:
- path: 40_Sources/cde2d135ac446f37/lantern_queues.md
  sha256: cde2d135ac446f374c1ae622a3df4543b657c881cabbe87480b7eae19bcec806
  locator: A lantern queue keeps every item in arrival order but lets a reader skip ahead by lit markers.
relations:
- type: similar_to
  target: card_exist
  note: both route readers past items
- type: depends_on
  target: kc_lit-marker-cost
status: confirmed
created: 2026-11-08
---
## Essence
A lantern queue keeps arrival order and lets readers skip ahead by lit markers.

## Propositions
- P1. Items stay in arrival order.
- P2. Readers skip ahead by following lit markers.

## Intuition and contrasts
- Intuition: Like lamp posts along a road: you can walk to the next lamp without counting stones.
- Contrast: Unlike an index, markers are placed by count, not by key.

## Related
- similar_to:: [[已有卡]] (both route readers past items)
- depends_on:: [[Lit marker memory cost]]

## Review cards
What order does a lantern queue keep?::Arrival order.
How does a reader skip ahead?::By jumping to a lit marker.

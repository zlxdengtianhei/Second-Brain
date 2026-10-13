# HOME — SecondBrain

## Today's review

```secondbrain-review
```

The review page of 2026-10-13 has not been made yet: run `sb daily --apply`; it makes the page and adds the link here (works without the plugin).

The dashboard comes from the SecondBrain Review plugin; in a terminal use `sb review status` for progress and `sb review queue` for today's cards.

## What to do now

1. Review: use the dashboard above, or `sb review queue` in a terminal.
2. Put new material into `00_Inbox/`, run `sb inbox list`, then `sb ingest <file>`.
3. Unfinished jobs: `sb job list`; sources with pending cards: `sb ingest --retry <job>`.

## Maps

## Learning science

- [[20_MOCs/How We Learn|How We Learn]] · cards: 13

## Quantitative reasoning

- [[20_MOCs/Reasoning with Data|Reasoning with Data]] · cards: 12
- [[20_MOCs/Computing Foundations|Computing Foundations]] · cards: 17

## Inventory

Counted on 2026-10-13: **cards: 42** · domains: 3 · without pillar or topic: 0.

| Track | Domains (cards) |
|---|---|
| Learning science | learning 13 |
| Quantitative reasoning | statistics 12, computing 17 |

### Folders (paths after this run)

| Folder | Files |
|---|---|
| `00_Inbox/` | 2 |
| `10_Knowledge/` | 42 |
| `20_MOCs/` | 15 |
| `40_Sources/` | 7 |
| `50_Reviews/` | 0 |
| `_data/` | 5 |

> Current numbers: `sb status`. The cards and the curated taxonomy YAML are the source of truth.

## Ingest and maintenance

`sb ingest <file>`: whole source → cards → classification and relations → independent review → one transaction → maps.

`sb classify --domain <domain>` fills in missing classification; `sb refresh` previews map changes and `sb refresh --apply` writes them; `sb recover <job> --rollback` undoes a write; `sb docs --apply` rewrites this page.

Material that needs a new topic or domain stays listed as pending; classification and maps never change review records.

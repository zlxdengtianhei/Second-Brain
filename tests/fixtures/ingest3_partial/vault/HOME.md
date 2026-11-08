# HOME — SecondBrain

## Today's review

```secondbrain-review
```

The review page of 2026-11-08 has not been made yet: run `sb daily --apply`; it makes the page and adds the link here (works without the plugin).

The dashboard comes from the SecondBrain Review plugin; without the plugin the block above is empty, and the review page link works instead. In a terminal use `sb review status` for progress and `sb review queue` for today's cards.

## What to do now

1. Review: use the dashboard above, or `sb review queue` in a terminal.
2. Put new material into `00_Inbox/`, run `sb inbox list`, then `sb ingest <file>`.
3. Unfinished jobs: `sb job list`; sources with pending cards: `sb ingest --retry <job>`.

## Maps

## ML

- [[20_MOCs/Example Attention Map|Example Attention Map]] · cards: 3

## Stones

- [[20_MOCs/Example Pebble Map|Example Pebble Map]] · cards: 1

## Inventory

Counted on 2026-11-08: **cards: 4** · domains: 3 · without pillar or topic: 0.

| Track | Domains (cards) |
|---|---|
| ML | attention 3, transformer 0 |
| Stones | pebbles 1 |

### Folders (paths after this run)

| Folder | Files |
|---|---|
| `00_Inbox/` | 0 |
| `10_Knowledge/` | 4 |
| `20_MOCs/` | 4 |
| `40_Sources/` | 1 |
| `50_Reviews/` | 0 |

> Current numbers: `sb status`. The cards and the curated taxonomy YAML are the source of truth.

## Ingest and maintenance

`sb ingest <file>`: whole source → cards → classification and relations → independent review → one transaction → maps.

`sb classify --domain <domain>` fills in missing classification; `sb refresh` previews map changes and `sb refresh --apply` writes them; `sb recover <job> --rollback` undoes a write; `sb docs --apply` rewrites this page.

Material that needs a new topic or domain stays listed as pending; classification and maps never change review records.

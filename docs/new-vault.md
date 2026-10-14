# Starting a vault of your own

[中文](new-vault.zh-CN.md)

A vault needs three things before the first ingest: a catalog of domains, a taxonomy file for each map of content (MOC), and an empty card registry. You write the catalog and the taxonomy files; `sb init` puts the catalog into the vault and adds the registry and the folders. The example vaults ([English](../examples/vault/), [Chinese](../examples/vault-zh/)) have a complete set to copy from.

The steps below use the default folder names. To use other names, set `[layout]` in the config file first ([configuration.md](configuration.md)).

## 1. Point `sb` at the folder

```sh
mkdir -p ~/MyVault
export SECONDBRAIN_VAULT=~/MyVault
```

The folder can be an existing Obsidian vault. `sb` only writes inside the folders named in the layout, plus the entry page `HOME.md` (and, if you list them in `[layout] entry_docs`, the generated blocks of your own top-level notes).

## 2. Write the catalog

The catalog names the domains, the MOC page of each domain, and the relation types cards may use. Save it anywhere, for example as `catalog.json`:

```json
{
  "schema": "secondbrain-catalog/1",
  "tracks": [
    {"id": "core", "title": "Core", "domains": ["cooking", "gardening"]}
  ],
  "domains": {
    "cooking": {"moc": "Cooking", "question": "How do heat, time and ingredients change food?"},
    "gardening": {"moc": "Gardening", "question": "How do plants grow, and how do we help them?"}
  },
  "relation_types": ["depends_on", "required_by", "part_of", "has_part", "similar_to", "contrasts_with"],
  "inverse_relations": {
    "depends_on": "required_by", "required_by": "depends_on",
    "part_of": "has_part", "has_part": "part_of",
    "similar_to": "similar_to", "contrasts_with": "contrasts_with"
  },
  "domain_relations": {}
}
```

- A domain slug is also the folder name of its cards: `10_Knowledge/<slug>/`.
- `question` is one sentence that tells a model what belongs in the domain.
- Every domain must be in at least one track. Tracks group domains on the entry page.
- `inverse_relations` maps each relation to its inverse, so that a link from a new card can be mirrored on the card it points to. The mapping must be consistent in both directions; a symmetric relation maps to itself.
- `depends_on`, `required_by`, `follows` and `precedes` set the learning order: in MOC pages and for new cards in the review queue, a prerequisite comes first.
- Optional `relation_gloss` maps a relation type to a short explanation.
- `relation_types` are open to every domain. `domain_relations` maps a domain to extra types that only its cards may use. A link also needs its inverse (the back link written on the target) to be open to the target's domain, and two cards may hold only one relation between them.

Then write it into the vault:

```sh
sb init --catalog catalog.json
```

`sb init` writes the catalog to `_data/secondbrain/catalog.json`, an empty card registry `_data/notes_registry.json`, and the folders `10_Knowledge/`, `20_MOCs/_pillars/` and `00_Inbox/` when they are missing. Its result lists `created_folders` and `taxonomy_files_missing`: the taxonomy files you still have to write (step 3). Running it again with the same catalog changes nothing; it only adds a registry or folders that are missing.

The catalog is written once. `sb init` also records the vault's language in it: `[cards] language` from the config file if set, else the language of maps already in the folder, else English. `sb language` shows it; `sb language --set zh` switches it, and then `sb refresh --apply` and `sb docs --apply` rewrite the generated pages. To change other parts of the catalog later, edit `_data/secondbrain/catalog.json` with care, or use the curation commands. After a hand edit, run `sb doctor`: its `catalog` field says `valid`, or `invalid:` with the reason, and `sb status` then lists `catalog_invalid` under `issues`.

## 3. Write one taxonomy file per MOC

Each MOC has a file `20_MOCs/_pillars/<MOC title>.yaml`; `taxonomy_files_missing` in the result of `sb init` names the ones still to write. Pillars are the main parts of a domain, and topics are the shelves inside a pillar. Every card is filed under one domain, pillar and topic.

```yaml
title: Cooking
narrative: >-
  Food changes with heat and time. Knowing why lets you fix a dish
  instead of following a recipe blindly.
pillars:
  - name: Heat
    order: 1
    why: How heat moves into food and what it does there.
    topics:
      - Browning
      - Boiling and steaming
  - name: Ingredients
    order: 2
    why: What each ingredient contributes.
    topics:
      - Salt
      - Fats
```

Write one file for every MOC in the catalog; in this example also `Gardening.yaml`. Until a domain has its file, `sb status` reports `missing_taxonomy` for it and an ingest makes no cards in that domain. While no domain has a topic yet, `sb ingest` stops before any model call and names the taxonomy files to write.

Start small. A producer can propose new topics under an existing pillar (two per source by default), and a reviewer must accept them. Material that needs a new pillar or a new domain gets no card and is reported as `needs_taxonomy` in the job result. Add the pillar to the file before you ingest more material of that kind.

## 4. Check and generate the maps

```sh
sb status          # "issues": [] means the vault is ready
sb refresh --apply # writes the MOC pages under 20_MOCs/
```

`issues` lists what stops writes. `map_issues` counts what `sb refresh` reports about the maps, for example cards whose classification is no longer valid, and learning-order cycles; `sb refresh` lists each one. A learning-order cycle is a group of two or more cards whose prerequisite links (`depends_on`, `follows`, `required_by`, `precedes`) lead back to themselves. It is reported once per group (`learning_order_cycle`, with `card_ids` and `mocs`), counted over the whole vault; the review queue takes such cards out of order (`sb review queue` reports `prerequisite_cycles` and `cycle_breaks`).

**In a vault that already has MOC pages**, the first `sb refresh --apply` adds three lines of front matter at the top of every domain and pillar page under `20_MOCs/` that does not have them yet:

```yaml
---
BC-list-note-field: down
---
```

The Obsidian plugin Breadcrumbs reads this field and treats the links listed on the page as links down the hierarchy, so the domain page, its pillar pages and the cards form one tree. Without Breadcrumbs the field does nothing. `sb refresh` without `--apply` counts these pages under `drift.kinds.breadcrumbs_field_only`, so you can see the change before it is made. It happens once: later refreshes keep the field. To undo it, run `sb recover <job_id> --rollback` with the job id that `sb refresh --apply` printed, or revert the change with git if the vault is under git.

## 5. Ingest

Put notes, articles or transcripts into `00_Inbox/`, then follow the quick start in the [README](../README.md): `sb inbox list`, then `sb ingest <file>` with the host or the API executor.

To review in Obsidian, install the [SecondBrain Review plugin](../obsidian-plugin/README.md). It reads the language that `sb init` recorded and uses the matching interface and card headings (`Review cards`, `Essence` and `Propositions` in an English vault). Set the section names in its settings only if your cards use other headings.

# Starting a vault of your own

A vault needs three things before the first ingest: a catalog of domains, a taxonomy file for each map of content (MOC), and an empty card registry. There is no command that writes them for you yet. The [example vault](../examples/vault/) has a complete set to copy from.

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

Then write it into the vault:

```sh
sb init --catalog catalog.json
```

The catalog is written once. `sb init` also records the vault's language in it: `[cards] language` from the config file if set, else the language of maps already in the folder, else English. `sb language` shows it; `sb language --set zh` switches it, and then `sb refresh --apply` and `sb docs --apply` rewrite the generated pages. To change other parts of the catalog later, edit `_data/secondbrain/catalog.json` with care, or use the curation commands.

## 3. Write one taxonomy file per MOC

Each MOC has a file `20_MOCs/_pillars/<MOC title>.yaml`. Pillars are the main parts of a domain, and topics are the shelves inside a pillar. Every card is filed under one domain, pillar and topic.

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

Start small. A producer can propose new topics under an existing pillar (two per source by default), and a reviewer must accept them. Material that needs a new pillar or a new domain gets no card and is reported as `needs_taxonomy` in the job result. Add the pillar to the file before you ingest more material of that kind.

## 4. Add the empty registry and folders

```sh
mkdir -p ~/MyVault/_data ~/MyVault/10_Knowledge ~/MyVault/00_Inbox
printf '{"notes": []}\n' > ~/MyVault/_data/notes_registry.json
```

## 5. Check and generate the maps

```sh
sb status          # "issues": [] means the vault is ready
sb refresh --apply # writes the MOC pages under 20_MOCs/
```

## 6. Ingest

Put notes, articles or transcripts into `00_Inbox/`, then follow the quick start in the [README](../README.md): `sb inbox list`, then `sb ingest <file>` with the host or the API executor.

To review in Obsidian, install the [SecondBrain Review plugin](../obsidian-plugin/README.md). If your cards use English headings, set its section names to `Review cards`, `Essence` and `Propositions`, or copy the settings file of the example vault (`examples/vault/.obsidian/plugins/secondbrain-review/data.json`) into the same place in your vault before you enable the plugin.

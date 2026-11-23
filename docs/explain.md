# Three levels, the graph of pillars, and drill-down explanations

[中文](explain.zh-CN.md)

## Three levels in storage

A vault may keep three levels: domain, pillar, card. A pillar written without `topics` in its taxonomy file files its
cards directly. Such a pillar has one implicit topic, named like the pillar: everything that reads the taxonomy sees
it (placement, shelves, review order, the checks of ingest), and nothing that is stored names it. Its cards have no
`topic` line, their registry notes have none, and the taxonomy file has no `topics` list. A pillar with topics of
its own keeps four levels; both kinds can stand in one vault.

Under a three-level pillar, ingest refuses new topics, the map page lists the cards directly (no topic headings,
one page while the page budget allows), and an export has no sub-sections in that pillar.

To move a map to three levels:

```sh
sb flatten --domain <slug>            # plan: what changes, and the topics each card had
sb flatten --domain <slug> --apply    # one recoverable transaction
sb recover <job> --rollback           # every byte back
```

The command changes only the `topics` lists, the `topic` line of each classified card (every other byte of the card
stays), the `topic` of their registry notes, and the map pages. A pillar page the new map no longer has is removed;
its bytes stay in the rollback journal. The result lists `topics_before` per card.

## When a pillar grows

`sb status` lists a three-level pillar from 50 cards (`topics.oversize` with `level: pillar`), with a proposal of how
its cards group by their relations (the method of the topic split proposals). 40 cards stay together. The number
is a reference for looking again, not a limit.

Whether it should split, and how, depends on what the cards say. `sb advise` asks a model:

```bash
sb advise --pillar <pillar> [--domain <domain>] --executor api
```

The program freezes the evidence: the domain's question and pillars, the pillar's `why`, its cards in the groups of
the proposal (title and essence), and for each group how many of its links stay inside, go to the other groups, to
other pillars and to other domains. The writer of `sb explain` answers `keep`, `split_pillars` (the groups put into
a few new pillars of the domain, each with a name and what it answers) or `new_domain` (some groups leave for a new
domain, with its question), with an argument citing the evidence; the program checks the answer (every group in one
pillar, no name taken by another pillar or domain), and the reviewer of `sb explain` judges it against the evidence.
Nothing is split: the advice is kept with the job, and `sb status` shows it in the pillar's `topics.oversize` entry
(`advice`) until the pillar's cards or links change; before that the entry names the command (`advise`). The vault
grows sideways (pillars into pillars, a part into a new domain) and keeps three levels.

## The graph of pillars

A card sits under one pillar, so the pillars of a map form a list. The relations of the cards cross pillars and
maps, and make the pillars a graph. Each map's root page has a section *How the pillars connect*: every pair of
pillars with relations between their cards, summed by type (`→` with a direction, read as the relation: the left
`depends_on` the right; `↔` without one). A pillar of another map is named with its map. Each pillar page names its
connected pillars.

## Drill-down explanations: `sb explain`

```sh
sb explain "<concept>"                    # its best card; or a card id, a pillar, a domain
sb explain --pillar "<pillar>" [--domain <slug>]
sb explain --domain <slug>
sb explain "<concept>" --executor api     # [explain.writer] and [explain.reviewer]
```

The program builds a tree for the concept: the domain, the pillar, the card, then the card's prerequisites and parts
(`depends_on`, `has_part`) down to the basic cards (`--depth`, 3 levels below the card by default; `--max-children`,
5). A card met a second time is a reference to where it already stands. A whole pillar is cut into groups of related
cards. The tree is a view: it is never stored, and storage keeps its three levels.

A writer gives one text per node. `explain` says what the node is at the reader's level and names each child with
what it adds, so the next step down makes sense. `recap` (nodes with children) is one sentence read after the
children, tying them back to the node. The program checks every text: each node once, each child named, the recap,
the lengths. A reviewer in another run (another model family in API mode) judges each node against the cards; a
revised node goes back to the writer while the review policy's repair rounds last. A node still revised after that
is written with a marker, never dropped. Host mode hands off packets like the other maintenance jobs (`sb job
advance`).

The pages (Markdown and HTML, in `[layout] articles/explain/` or `--out`) have two parts:

- the drill-down, top to bottom (the HTML folds each level); a card that is one end of a bridge says in its line
  which other domain also uses the concept and what that side says (`same_concept_as`, or `similar_to` for a close
  one: the narrow card's essence), and a card that another domain points at says so (the description stands here);
- the review path, bottom to top: the reviewed questions of the cards, prerequisites first, and after each group of
  children a step back up to their parent with its recap, ending at the domain.

Nothing is written into the knowledge folders.

## The review page goes back up

The daily review page (`sb today`, `sb daily`) ends with *After these cards, back up to the higher level*: for each
pillar of today's cards, what the pillar answers, the recap of its newest whole-pillar explanation (`sb explain
--pillar`), and how today's cards of that pillar connect.

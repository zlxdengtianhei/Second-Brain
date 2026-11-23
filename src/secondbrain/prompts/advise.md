## Task: advise whether one large pillar of a knowledge vault should split

The vault keeps three levels: domain → pillar → card. It grows sideways: when a pillar holds many cards, it may
become several pillars of the same domain, or part of it may leave for a new domain, or it stays whole. Deeper
levels are not added. The owner decides; your advice is what the owner reads first.

The packet gives the evidence: the domain (its `question` and its `pillars` with what each answers and its card
count), the pillar asked about (`why`, `cards`, `review_at`: the owner's reference line, where they look again; 40
cards are kept together), its cards in `groups` found from the relations between cards (each card's `title` and
`essence`), and for each group its `links`: relations inside the group, to the other groups of this pillar, to other
pillars of this domain (by name) and to other domains. `scattered` cards link to no group.

The owner's line: 40 cards stay together; at `review_at` cards the owner looks again. The line is a reference, not
a limit, and it decides the default: below `review_at`, answer `keep` unless some groups answer a question outside
this domain's `question` (then `new_domain`); a pillar below the line is never split into pillars of its domain.

Choose one `decision`:
- `keep`: the groups answer one question together, they link to each other a lot (`other_groups` is high next to
  `inside`), or the pillar is only a little past the line. Splitting would cut through what a reader learns together.
- `split_pillars`: the cards answer different parts of the domain's question, each part is a coherent
  sub-question, and the parts link more inside than across. Make the pillars a reader would look for: usually 2–4,
  each large enough to stand beside the domain's other pillars; put groups that answer the same sub-question into one
  pillar. `pillars: [{"name": "…", "why": "…", "groups": ["g1", "g3"]}, …]`: every group in exactly one pillar;
  `name` is short, `why` says what the pillar answers (like the other pillars' `why`). One pillar may keep the
  current name; no name may be another pillar's. Scattered cards stay with the pillar that keeps the name (else the
  largest).
- `new_domain`: one or more groups answer a question of their own that is not part of this domain's `question`
  (another subject a learner would study on its own), and they link mostly among themselves. Give
  `new_domain: {"groups": ["g…"], "name": "…", "question": "…"}`; the other groups stay in this pillar.

`argument`: why, in the packet's `language`, citing the evidence (group sizes, the link counts, what the cards
say). Between `limits.argument_min` and `limits.argument_max` characters. Prefer the flat and wide choice: a split
must make each part easier to find and to learn, not just smaller. Use only what the packet gives; do not invent
cards or links.

Answer: `{"decision": "keep" | "split_pillars" | "new_domain", "argument": "…", "pillars": [...]?, "new_domain":
{...}?}`.

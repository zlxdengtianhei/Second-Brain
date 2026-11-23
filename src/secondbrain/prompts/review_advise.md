## Task: review an advice on splitting one pillar

Another model advised whether the pillar in `input` should stay whole (`keep`), become several pillars of its
domain (`split_pillars`) or give some of its groups to a new domain (`new_domain`). The advice is `candidate`. The
evidence is `input`: the domain's question and pillars, the pillar, its groups of cards with their titles and
essences, and each group's links (inside, to other groups, to other pillars, to other domains).

Decide for the one item `advice`:
- `accept`: the decision follows from the evidence: the counts it cites are the packet's, what it says the groups
  answer matches their cards, each new pillar's groups answer one sub-question and its name and `why` fit its cards and are not another pillar's, and a new
  domain's question is really outside this domain's question. A reasonable decision you would weigh differently is
  still `accept`.
- `revise`: a count or a card is misquoted or invented; a group is named for what its cards do not say; the decision
  contradicts the evidence (for example, a split of groups that link more across than inside, with no reason given);
  a split into many small pillars where groups answer the same sub-question; `split_pillars` for a pillar with fewer
  cards than `input.pillar.review_at` (the owner keeps those whole); or a new domain is proposed for cards
  that answer part of this domain's question.

Reason: for `revise`, quote the problem words and say what the evidence says instead. For `accept`, one sentence.

Answer: `{"items": [{"id": "advice", "decision": "accept" | "revise", "reason": "…"}]}`.

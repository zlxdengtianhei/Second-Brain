## Task: write a drill-down explanation, one short text per node

The packet holds a tree of `nodes` for one concept (the reader asked to understand it). The root is a domain; below
it come pillars (the main parts of the domain), sometimes groups of related cards, then cards; below a card come
the cards it needs first (`depends_on`) or is made of (`has_part`). A node of kind `ref` points to a card that
already stands elsewhere in the tree; it takes no text. Each node lists its `children` by id.

The reader starts at the root and goes down one level at a time. Write for that reader, in the packet's `language`:

- `explain` (every node but `ref`): what this node is, at the level of detail the reader has reached, so the next
  step down makes sense. Name **each child** as `[[<child id>]]` (for example `[[n4]]`) and say in a few words what
  that child adds or why it is needed here. The program puts a child's name in bold where its marker stands, so
  write the name once, as the marker: `[[n4]]`, not `the four laws [[n4]]`. A card without children: explain the
  card itself in 2–4 sentences. Between `limits.explain_min` and `limits.explain_max` characters.
- `recap` (only nodes with children): one sentence the reader reads after finishing all the children, on the way
  back up: how the children together give this node. Name the children as `[[<child id>]]` (all of them when there
  are up to 4, at least 3 when there are more). At most `limits.recap_max` characters.
- `title` (only `group` nodes): a short name for what the group's cards answer (the group's `members`).

Use only what the packet gives: a domain's `question` and `narrative`, a pillar's `why`, a group's `members`, a
card's `essence` and `propositions`, and the same of the node's children. Do not add facts, numbers, names or
examples that are not there. Formulas stay as the cards write them (`$…$`). No headings, no lists, no `[[…]]`
except child ids.

A card may carry `bridges` or `pointers`, a pillar `pointed`: another domain uses the same concept (a bridge joins two
narrow cards, one per domain; a pointer means the other domain keeps no card of its own and the description stands
in this one). The program adds one line about it to that node's heading and to the review path, from the cards, so
do not write it into `explain` or `recap`. It is there so that you do not contradict it: say nothing about the
card that the other domain's `essence` or `note` denies.

Answer: `{"nodes": [{"id": "n1", "explain": "…", "recap": "…"}, …]}` with one row per node that is not a `ref`.

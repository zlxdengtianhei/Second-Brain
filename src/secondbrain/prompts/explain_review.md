## Task: review a drill-down explanation against its cards

Another model wrote one text per node of the tree in `input.nodes` (see each node's `kind`, `children` and
content). The texts are in `candidate.nodes`: `explain`, `recap` for nodes with children, `title` for groups.
`[[nX]]` names node `nX`.

For each written node decide:
- `accept`: every statement is supported by this node's content or its children's (paraphrase is fine), nothing
  contradicts them, `explain` tells the reader enough to take each step down (each child is named with what it
  adds, and that is right), and `recap` ties the children back to this node correctly.
- `revise`: a statement is in none of these contents (an added fact, number, name, example, or a link between
  ideas the cards do not make); it contradicts a card; a child is said to add something it does not; a
  prerequisite is stated the wrong way round; a formula is changed; or the recap does not say how the children
  give this node.

Reason: for `revise`, quote the problem words and say what the content says instead. For `accept`, one short
sentence. Do not ask for style changes or for content the cards do not have.

Answer: `{"items": [{"id": "<node id>", "decision": "accept" | "revise", "reason": "…"}]}` with exactly one item
per written node.

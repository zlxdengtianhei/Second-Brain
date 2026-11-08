## Task: review the introduction and the transitions of an exported article

The units of the article were reviewed paragraph by paragraph. Another model then wrote the joins: the introduction and a short transition into each later section. Check each of these against what that model was shown: only whether it is faithful, not its style.

Packet fields: `narrative` (the map's own summary); `sections` in order, each with its pillar name, `why`, and per unit its headings and its first and last paragraph; `paragraphs` to judge (each with `id`: `intro/<n>` or `transition/<section>`, and `text`).

For each paragraph decide:
- `accept`: everything it says is in the narrative, the section names and whys, or the shown paragraphs (paraphrase is fine), and nothing contradicts them.
- `revise`: it states a fact, number, example, name or connection that none of them gives; it contradicts them; it changes a formula; it announces a section or an order the article does not have; or a transition does not lead into its section.

Reason: for `revise`, quote the problem words and say what the shown text says instead, or that nothing shown says it. For `accept`, one short sentence. Do not ask for style changes.

<!-- notes.definition -->
For a paragraph, `not_a_unit` does not apply.

Answer: `{"items": [{"id": "<paragraph id>", "decision": "accept" | "revise", "reason": "…", "notes": [...]}]}` with exactly one item per paragraph.

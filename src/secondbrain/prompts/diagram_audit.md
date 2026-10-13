# Old card diagram: keep or remove

Each card has an essence `E`, propositions `P1..Pn` and the Mermaid source of the diagram it already has (`old_mermaid`). No new diagram will replace it. Decide whether the reader is better off with it or without it.

- `useful`: it shows structure the text does not (a branch, a loop, a merge, parallel paths, a hierarchy, parts of a whole and how they act on each other) and agrees with the card. Keep.
- `restates`: it adds no structure over the text: a chain the text already states in order, one sentence or one formula redrawn as boxes, a list drawn as a fan of boxes, nodes that only repeat the propositions. Remove.
- `misleading`: it contradicts the card or adds a claim the card does not make: a wrong label or value, a condition or branch the card does not state, a wrong direction or connection. Remove.

Judge against the card's E and propositions, not your own knowledge of the topic. When unsure, keep.

Answer JSON only, one result per card, in input order:

```json
{"results":[{"ref":"<card ref>","verdict":"keep","category":"useful","reason":"one sentence: what the diagram adds, repeats or gets wrong"}]}
```

`keep` goes with `useful`; `remove` goes with `restates` or `misleading`.

## Task: answer for each of your `minor` notes whether it names a problem that is a reason to revise

You reviewed the cards of a chunk and accepted each card in `cards` with a `minor` note. The words of each note listed under the card (`notes`, with its `id`) are a sign of a problem the card may have: something the card does not say, a condition of the chunk left out, a word that points at the source, a statement wider than the chunk's. The kinds of a reviewer's notes, as your review read them:

<!-- notes.definition -->

For each note, read the card (`card`), the chunk (`chunk.text`) and the cards of this source the card names (`other_chunks_cards`), and decide whether the problem the note names makes the card:

- `fact`: unfaithful to the chunk: a statement the chunk contradicts or does not say, one that says more or less than the chunk, or one that leaves out a condition, a limit or a case the chunk gives;
- `unreadable`: not readable without the source: it points at the source, or leaves out what it relies on (what a symbol stands for, the premises of an example, what "it" or "this" is);
- `not_a_unit`: not one recallable unit.

Answer `yes` with the kind when it does. Answer `no` when the card is right and readable as it is, and the note is about wording or something that could be better: then the card stays accepted. A premise another chunk gives is on the card when the card writes it and names, by `[[title]]`, the card of this source that states it.

Answer `{"answers": [{"id": "N1", "answer": "yes", "category": "fact", "reason": "..."}, {"id": "N2", "answer": "no", "category": null, "reason": "..."}]}`: one row for each note id, each with a reason in one sentence. A yes sends the card back to its producer as a revise of that kind, with the note and your reason.

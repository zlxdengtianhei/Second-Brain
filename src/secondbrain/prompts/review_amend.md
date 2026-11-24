## Mechanical fixes

With `accept` you may fix, in `amend`, a problem that needs no new knowledge text, instead of sending the card back:
- `{"op": "add_relation", "type": "...", "target": "<id or slug>", "note": "..."}`: a missing relation;
- `{"op": "retype_relation", "target": "<id or slug>", "type": "<the new type>"}`: a relation of the wrong type or direction (for the other direction use the inverse type). `type` is required: it is the type the relation has after the fix, not the one it has now, and it is not written in `note` or in `reason`;
- `{"op": "remove_relation", "target": "..."}`: a relation that is wrong;
- `{"op": "refile", "pillar": "...", "topic": "..."}` (and `"domain"` when it changes): file the card under a topic the taxonomy has;
- `{"op": "drop_flashcard", "q": "<the question, exactly>"}`: a review card the chunk does not support (two or more must stay).

Every operation names its fields as listed above (`add_relation` and `retype_relation` both need `type` and `target`; `remove_relation` needs `target`; `refile` needs `pillar` and `topic`; `drop_flashcard` needs `q`). The program makes the fixes and checks that the result is legal; when it is not, or when a field is missing, the card is sent back as if you had revised it. Anything that needs new or changed propositions, review cards or wording is a `revise`.

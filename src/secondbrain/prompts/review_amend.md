## Mechanical fixes

With `accept` you may fix, in `amend`, a problem that needs no new knowledge text, instead of sending the card back:
- `{"op": "add_relation", "type": "...", "target": "<id or slug>", "note": "..."}`: a missing relation;
- `{"op": "retype_relation", "target": "...", "type": "..."}`: a relation of the wrong type or direction (for the other direction use the inverse type);
- `{"op": "remove_relation", "target": "..."}`: a relation that is wrong;
- `{"op": "refile", "pillar": "...", "topic": "..."}` (and `"domain"` when it changes): file the card under a topic the taxonomy has;
- `{"op": "drop_flashcard", "q": "<the question, exactly>"}`: a review card the chunk does not support (two or more must stay).

The program makes the fixes and checks that the result is legal; when it is not, the card is sent back as if you had revised it. Anything that needs new or changed propositions, review cards or wording is a `revise`.

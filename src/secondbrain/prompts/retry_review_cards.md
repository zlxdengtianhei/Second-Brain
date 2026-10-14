
## This chunk was ingested before

Earlier jobs of this source settled most of this chunk, and their reviews stand:
- `retry.written_cards`: cards from this chunk that are in the vault, each with the passage it was made from;
- `retry.duplicates`: passages whose knowledge already exists in the cards named in `duplicate_of`;
- `retry.out_of_scope`: passages judged not worth keeping.

They count toward coverage; do not judge them again. `retry.findings` lists what was still open, with the earlier reviewers' reasons; this candidate answers those.

`coverage`: `accept` when this candidate, together with what is settled, handles every open finding. `revise` only for an open finding, or a passage that nothing above covers; name it. A passage listed in `retry.duplicates` or `retry.out_of_scope` is covered; do not ask for a card for it unless an open finding explains why that row is wrong (a finding that only says the passage has no card or row is answered by the settled row). A candidate card that holds the knowledge of a settled duplicate or of a written card is a `duplicate` of that card.

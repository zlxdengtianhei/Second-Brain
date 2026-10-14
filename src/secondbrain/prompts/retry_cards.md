
## This chunk was ingested before

Cards from this chunk that are already in the vault are listed in `retry.written_cards`. Do not write them again; refer to them by `id` in relations. `retry.duplicates` lists passages whose knowledge already exists in the cards named in `duplicate_of`, and `retry.out_of_scope` lists passages judged not worth keeping; earlier reviews settled both, so do not write cards for them either. `retry.findings` lists what is still open, with the reviewer's reasons: rejected cards, coverage gaps, chunks that failed, and material that needed a pillar or topic that did not exist.

Produce cards only for these open items, using the current `candidate_taxonomies` (the taxonomy may have changed since). If there is still no fitting place, report the part as `needs_taxonomy` again. Your coverage rows cover only the parts you handle now. When a finding calls a passage of `retry.duplicates` missing, answer it with a `duplicate` coverage row that names the same cards, not with a new card, unless the finding explains why the earlier row is wrong.

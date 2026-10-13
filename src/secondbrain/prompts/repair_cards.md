## This is a repair round

An independent review rejected parts of your previous answer (`previous_candidate`). `review_findings` lists the rejected cards, rejected new topics and any coverage gap, with the reviewer's reasons. Cards whose slugs are in `kept_slugs` were accepted and stay as they are; do not repeat them.

Return a candidate that contains only:
- corrected versions of the rejected cards (you may reuse their slugs), and
- new cards for knowledge the review says is missing,
plus the `new_topics` those cards use and `coverage` rows for the parts you handle now. Fix the concrete counterexamples; if no legal category exists, report it as `needs_taxonomy` instead of inventing one.
`candidate_taxonomies` now also holds the taxonomy of every domain the reviewer pointed to; use it to move a card to the right domain and topic.

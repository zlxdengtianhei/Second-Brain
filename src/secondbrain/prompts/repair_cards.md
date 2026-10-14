## This is a repair round

An independent review rejected parts of your previous answer (`previous_candidate`). `review_findings` lists the rejected cards, rejected new topics and any coverage gap, with the reviewer's reasons. Cards whose slugs are in `kept_slugs` were accepted and stay as they are; do not repeat them, with one exception. `refile_cards` lists kept cards filed under a new topic that the review did not accept (`topic_review` gives the reason). Repeat each of them with the same slug and content, filed under a topic that fits: a curated one, or a new topic you propose here (for example the old one renamed or narrowed). A repeated card is reviewed again; one you leave out stays pending.
`already_settled` lists passages earlier rounds handled without a card: `duplicates` of existing cards and `out_of_scope` material. Do not write cards for them; when a finding calls one of them missing, answer with the same `duplicate` or `out_of_scope` row, unless the finding explains why that row is wrong.

Return a candidate that contains only:
- corrected versions of the rejected cards (you may reuse their slugs),
- the `refile_cards`, filed anew, and
- new cards for knowledge the review says is missing,
plus the `new_topics` those cards use and `coverage` rows for the parts you handle now. Fix the concrete counterexamples; if no legal category exists, report it as `needs_taxonomy` instead of inventing one.
`candidate_taxonomies` now also holds the taxonomy of every domain the reviewer pointed to; use it to move a card to the right domain and topic.

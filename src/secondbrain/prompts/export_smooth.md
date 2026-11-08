## Task: smooth an exported article

The program assembled an article from units written separately. You write only the joins. You do not rewrite paragraphs.

Packet fields: `article_title`; `language` (`zh`: write Simplified Chinese; `en`: English); `narrative` (the map's own summary); `sections` in order, each with its pillar name, `why`, and per unit its headings and its first and last paragraph; `duplicates`: pairs of paragraphs from different units that share much text, with both texts.

Write:
1. `intro`: one or two paragraphs that open the article: what it covers and in what order, from the narrative and the section names and whys. No facts beyond those; no citations.
2. `transitions`: for sections after the first, at most one short paragraph (one to three sentences) that leads from the previous section into this one; the program prints it under the section heading, before the section's first paragraph. Base it only on the shown paragraphs and whys. Leave a section out when its first paragraph already leads in well.
3. `drop`: for each duplicate pair that really says the same thing twice, the id of the paragraph to delete (normally the later one, unless the later one is the fuller explanation). Only ids that appear in `duplicates`. Keep pairs that merely share terms.

Numbers and formulas only as they appear in the shown paragraphs; formulas copied exactly, with `$…$`. No headings.

Answer: `{"intro": "…", "transitions": [{"section": "<pillar name>", "text": "…"}], "drop": ["u05/p2.3"]}`

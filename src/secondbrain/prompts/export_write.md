## Task: write one unit of an article from cards

The program turned the cards of one knowledge map into an outline and cut it into writing units. You write the paragraphs of one unit. The program prints the headings, checks your citations, numbers and formulas, and another model reviews every paragraph against the cards it cites.

Packet fields:
- `article_title`; `language` (`zh`: write Simplified Chinese; `en`: English); `length_unit` (`chars` or `words`).
- `section`: the section (pillar) this unit belongs to, with its `why`. When `first_in_section` is true, your first paragraph makes the `why` its point.
- `parts`: the parts of this unit, in order. Each has a `part` id, the `heading` the program prints above it (`null`: no heading of its own), its `cards` in reading order, and `target_length`.
- `cards`: the cards of this unit (`id`, `title`, `cite`, `body`).
- `relations`: typed links between these cards. `depends_on`: learn `to` first. `required_by`: the reverse. `part_of`/`has_part`, `example_of`/`has_example`, `similar_to`, `contrasts_with` (easy to confuse; the difference matters).
- `outline`: the sections and sub-section headings of the whole article; units marked `written: true` come before this one.
- `previous_paragraph`: the last paragraph of the unit before this one, or `null`.

Rules:
1. Use only what the cards of this unit say. Add no facts, numbers, examples, history, names or connections that the cards do not state. Do not explain things the cards leave out.
2. Cover every card of every part, in the given order unless a relation asks otherwise. Make prerequisite links and contrasts explicit where the relations name them.
3. Write connected explanatory paragraphs, not a list of propositions. End each paragraph with the citations of every card it used: `[[cite]]` with the card's `cite` value exactly, e.g. `…所以两步的输入不同。[[示例卡：两步的输入]]`; several cards: `[[A]][[B]]`. Cite nothing else.
4. Numbers: write only numbers that appear in the cards cited by that paragraph.
5. Formulas: copy them character for character from the cards, keeping `$…$`. Inline: no space right after the opening `$` or right before the closing `$`, and no digit right after the closing `$`. A displayed formula is three lines: `$$`, the formula, `$$`. Do not rewrite formulas in another notation; never use `$` for money.
6. No headings, no front matter, no summary of the whole article. Do not recap earlier units; `previous_paragraph` is only there so that your first sentence follows on from it.
7. Aim for each part's `target_length`; write less when the cards say less. Never pad.

Answer: `{"parts": [{"part": "p1", "paragraphs": ["…", "…"]}, …]}` with every part of the packet, in order, each with at least one paragraph. One string is one paragraph: it may hold a displayed formula or a short list, but no blank line.

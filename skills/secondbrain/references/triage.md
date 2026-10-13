# Which inbox items become cards

Make cards when the file teaches something that is worth recalling later:

- notes, articles, blog posts, book or paper excerpts;
- lecture notes, slides exported as text, course handouts;
- transcripts of talks, videos or conversations that explain ideas;
- explanations of code, systems or methods (not the raw code dump alone).

Skip with a reason when the file is:

- administrative: invoices, receipts, forms, schedules, travel or account details;
- empty, a stub, or only a link without content;
- an image or PDF without extractable text (unless a converter is configured: `[ingest] converter = "markitdown"`);
- an exact copy of something already ingested (`sb inbox list` shows it as `done`);
- private data about people that the user did not ask to keep as knowledge.

When you are not sure:

1. `sb inbox show <name> --head 60` and read the beginning.
2. If it is still unclear, add it to a list of questions for the user. Do not block the other items.

Mixed files are fine: the producer marks greetings, logistics and other non-knowledge passages as `out_of_scope`, and the reviewer checks that coverage.

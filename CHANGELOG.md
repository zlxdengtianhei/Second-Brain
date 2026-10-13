# Changelog

## 0.1.0 (unreleased)

First public version.

- Ingest chain: inbox status, frozen sources and chunks, producer and independent reviewer, deterministic checks, one repair round, review gate, one recoverable vault transaction, optional git commit.
- Executors: host hand-off for sub-agents of a coding-agent harness; API clients for OpenAI-compatible and Anthropic endpoints, with a model-family check.
- Entry points: the `sb` CLI, an Agent Skill (`skills/secondbrain`), and an MCP server with separate read-only and writer launches.
- Taxonomy and maps of content: domain, pillar and topic for every card; MOC pages regenerated in prerequisite order; topics proposed by the producer and accepted by the reviewer.
- Generated pages (maps, `HOME.md`, the daily review page) in English or Chinese, like the card headings. The vault records its language in the catalog; `sb language` shows it and `sb language --set` switches it.
- `sb ingest --retry <job>` for the pending cards of a partial job, as a child job that is rolled back before its parent; `sb recover <job> --rollback` and `--resume`.
- Card diagrams: the model returns a diagram IR that cites the card; code validates it and generates Mermaid, or the card gets no diagram. Batch tools for diagrams already in a vault: audit, prune, re-judge, old-diagram audit, layout gate with the Mermaid build Obsidian ships, write-back.
- Review registry: append-only review log per device, FSRS replay in Python and TypeScript with shared test vectors, `sb review status|queue|log|import-sr`; the daily review page (`sb today`) shows the same queue.
- Obsidian plugin SecondBrain Review: today view and dashboard, flip review, Q&A review with LLM grading through AI Providers, card explanations with map context, reminders, one-time import of Spaced Repetition schedules.
- Configuration in TOML, configurable folder layout, keys only from environment variables or commands.
- `sb init --catalog <file>` puts a hand-written catalog into a vault, adds the empty card registry and folders, and lists the taxonomy files still to write.
- English example vault (CC BY 4.0): 42 cards built with the Agent Skill and the tool's own ingest chain.

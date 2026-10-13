# Running sub-agents in different hosts

The flow needs one thing from the host: start a fresh agent with a short text and let it read a file, write a file and run one shell command. Status of each way below: **not yet tested in a real host** unless marked otherwise. The command sequence itself is covered by the tool's tests (`tests/test_cli_ingest.py`, `tests/test_mcp.py`).

## Hosts with sub-agents

- **Claude Code** (tested: the English example vault, 7 sources and 34 hand-offs, was built this way): start a sub-agent (the Task/Agent tool) per hand-off with the filled-in text. Choose a different model for reviewers when the host offers model choice. Its sub-agents all run Claude models, so reviews there are recorded as `same_family: true`.
- **Codex** and **Pi**: use their sub-agent or task feature the same way when available; otherwise use a headless session (below).

## Headless sessions instead of sub-agents

Each call is a new process, so it has a fresh context:

```sh
claude -p "$(cat filled-producer-text.txt)"
codex exec "$(cat filled-producer-text.txt)"
pi -p "$(cat filled-reviewer-text.txt)"
```

Give the reviewer a different model when the command allows it (for example `--model`).

## No sub-agents at all: API executor

Configure `[executor.producer]` and `[executor.reviewer]` in the config file (different model families), check them with `sb doctor --live`, then let the tool call the endpoints itself:

```sh
sb ingest <path>                  # or: sb job advance <job_id> --executor api
```

Keys come only from `api_key_env` or `api_key_cmd`, never from the config file.

## MCP instead of the CLI

`sb serve` (read-only) offers `job_packet`, which returns the task text directly; `sb serve --writer` adds `ingest_prepare`, `job_submit` and `job_advance`. The same flow applies: the coordinator never answers a packet itself.

# Running Second Brain with agents and models

The model work of an ingest (producing cards, reviewing them, proposing diagrams) can be done by sub-agents of a coding-agent harness, or by the tool calling API endpoints. Everything else is done by `sb`.

## Agent Skill

[`skills/secondbrain/`](../skills/secondbrain/SKILL.md) follows the Agent Skills format: a `SKILL.md` with a name and a description, plus reference files.

1. Install the package so that `sb` runs in your shell (see the README quick start), and set the vault with `SECONDBRAIN_VAULT` or the config file.
2. Copy `skills/secondbrain/` into the skills folder of your harness. For Claude Code that is `~/.claude/skills/secondbrain/` (all projects) or `.claude/skills/secondbrain/` (one project). For other harnesses, see their documentation on Agent Skills.
3. Start the harness from that shell. Its sub-agents run the `submit_cmd` of their hand-off themselves. That command already names the vault (`--vault`), the config file (`--config`, when one is used) and the state directory (when `SECONDBRAIN_STATE_DIR` is set), but it calls `sb` by name. So `sb` must be on the `PATH` of the sub-agents' shells: activate the virtual environment before you start the harness, or link `sb` into a folder that is on your `PATH`.
4. Ask the agent to process the inbox, or to make cards from a file.

The agent coordinates. For each hand-off it starts a new sub-agent with a short fixed text that points to the task file, the answer file and the submit command; it does not add its own view of the material. `sb` checks each answer when it is submitted and returns errors to the sub-agent. The agent then runs `sb job advance <job_id> --executor host` until the job is `applied` or `partial`, and reports new cards, pending cards with the reviewer's reasons, duplicates and material that needs a new pillar or domain.

When the harness lets you choose models, give reviewers a model from a different family than the producer. A same-family review is accepted in host mode and recorded as `same_family: true` in the job. In Claude Code all sub-agents are Claude models, so every review there is same-family.

The [example vault](../examples/vault/) was built this way in Claude Code: 7 sources, 34 sub-agents (producers on one Claude model, reviewers on another), 42 cards. In that run the sub-agent shells did not have the virtual environment, so the coordinator wrote the full path of `sb` into each submit command. Other harnesses (Codex, Pi and others) should work the same way, but they have not been tested.

### Without sub-agents

A harness that cannot start sub-agents can start a fresh headless session for each hand-off, for example `claude -p "<filled-in text>"`. Each call is a new process with a fresh context. See [`skills/secondbrain/references/harness-notes.md`](../skills/secondbrain/references/harness-notes.md).

### Doing it by hand

The hand-off protocol is plain files and commands, so you can also follow it yourself:

```sh
sb ingest <file> --executor host          # returns handoffs: packet_path, output_path, submit_cmd
# give packet_path to a model, save its JSON answer at output_path, then run submit_cmd
sb job advance <job_id> --executor host   # next hand-offs, or the final state
sb job show <job_id>                      # state, pending items with reasons, run records
sb job packet <job_id> --role producer    # the open hand-off again
sb job abandon <job_id> --run-id <run_id> --reason "sub-agent crashed"
```

A run id accepts one answer. A second, different answer for the same run is refused.

## API executor

The tool calls the endpoints itself. Producer and reviewer must be models of different families. The family is found from the model name (the configured name, and the name the provider reports in its answer) by a table of name parts such as `claude`, `gpt`, `gemini`, `qwen`; add your own in `[model_families]`.

1. `pip install -e ".[api]"`.
2. Add `[executor.producer]` and `[executor.reviewer]` to the config file ([configuration.md](configuration.md)). `kind = "openai"` works with any OpenAI-compatible `/chat/completions` endpoint; `kind = "anthropic"` uses the Anthropic Messages API.
3. Put each key in an environment variable named by `api_key_env`, or give a command that prints it in `api_key_cmd`.
4. Check: `sb doctor` shows the configuration and whether each key is available; `sb doctor --live` sends one small request to each endpoint.
5. Run `sb ingest <file> --executor api`, or `sb ingest --all` with `[executor] default = "api"`.

Rate limits and server errors are retried with back-off. A chunk that is too large for the model's context is split in two and retried. An answer that is cut off or not JSON counts as invalid and uses the chunk's repair round.

## MCP server

`pip install -e ".[mcp]"`, then register the server in your MCP host. Most hosts accept a JSON entry like this one (for Claude Code, a project's `.mcp.json`):

```json
{
  "mcpServers": {
    "secondbrain": {
      "command": "sb",
      "args": ["serve"],
      "env": {"SECONDBRAIN_VAULT": "/path/to/vault"}
    }
  }
}
```

Use the full path of `sb` if the host does not start it from your virtual environment.

| Server | Tools |
|---|---|
| `sb serve` (read-only) | `status`, `inbox_list`, `search`, `read_cards`, `read_taxonomy`, `read_task`, `job_show`, `job_packet` |
| `sb serve --writer` | the above, plus `ingest_prepare`, `job_submit`, `job_advance` |

`job_packet` returns the full task text for a sub-agent; it records a run in the processing state and does not touch the vault. `job_submit` stores and validates an answer and never writes knowledge. Only `job_advance` on the writer server writes the vault, and only after the review gate. Register the two servers separately if you want some hosts to have read access only.

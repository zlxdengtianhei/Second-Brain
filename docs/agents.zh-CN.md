# 用 agent 和模型运行 Second Brain

[English](agents.md)

入库时需要模型做的工作（生产卡片、复核卡片、提出卡内图）有两种做法：由编程 agent 宿主的子代理完成，或由工具调用 API 端点完成。其余工作都由 `sb` 完成。

## Agent Skill

[`skills/secondbrain/`](../skills/secondbrain/SKILL.md) 采用 Agent Skills 格式：一个带名称和描述的 `SKILL.md`，加上若干参考文件。

1. 安装这个包，让 `sb` 能在你的 shell 里运行（见 README 的快速上手部分）。用 `SECONDBRAIN_VAULT` 或配置文件指定知识库。
2. 把 `skills/secondbrain/` 复制到宿主的 skills 目录。Claude Code 的目录是 `~/.claude/skills/secondbrain/`（对所有项目生效）或 `.claude/skills/secondbrain/`（只对一个项目生效）。其他宿主请看它们关于 Agent Skills 的文档。
3. 从这个 shell 启动宿主，让 agent 能对同一个知识库运行 `sb`。agent 的子代理自己运行各自交接里的 `submit_cmd`。这条命令写明 `sb` 的完整路径、知识库（`--vault`）、配置文件（`--config`，用了配置文件时）和状态目录（设了 `SECONDBRAIN_STATE_DIR` 时）。所以即使子代理的 shell 里没有你的虚拟环境，它也能找到同一个任务（job）。
4. 让 agent 处理收件箱，或用某个文件做卡片。

agent 负责协调。对每个交接，它都开一个新的子代理，只给一段简短的固定文字，指向任务文件、答案文件和提交命令；它不加入自己对材料的看法。每份答案提交时，`sb` 都会检查，并把错误返回给子代理。然后 agent 反复运行 `sb job advance <job_id> --executor host`，直到任务变成 `applied` 或 `partial`。最后它报告新卡片、待定卡片及复核者的理由、重复卡片，以及需要新支柱或新领域的材料。

如果宿主允许选择模型，给复核者用与生产者不同家族的模型。宿主模式下也接受同一家族的复核，并在任务里记为 `same_family: true`。Claude Code 里的子代理都是 Claude 模型，所以那里的每次复核都属于同一家族。

[中文示例库](../examples/vault-zh/)就是这样在 Claude Code 里用中文笔记建成的：7 份来源，31 个子代理（生产者用一个 Claude 模型，复核者用另一个），52 张卡片。[英文示例库](../examples/vault/)也是这样建成的：7 份来源，34 个子代理，42 张卡片。其他宿主（Codex、Pi 等）应该也能这样用，但还没有测试过。

### 不用子代理

宿主不能开子代理时，可以为每个交接开一个新的无头会话，例如 `claude -p "<filled-in text>"`。每次调用都是一个新进程，上下文是全新的。见 [`skills/secondbrain/references/harness-notes.md`](../skills/secondbrain/references/harness-notes.md)。

### 手动操作

交接协议只是普通文件和命令，所以你也可以自己按它操作：

```sh
sb ingest <file> --executor host          # 返回 handoffs：packet_path、output_path、submit_cmd
# 把 packet_path 交给一个模型，把它的 JSON 答案存到 output_path，再运行 submit_cmd
sb job advance <job_id> --executor host   # 下一批交接，或最终状态
sb job show <job_id>                      # 状态、附理由的待定项、运行记录
sb job packet <job_id> --role producer    # 再次取出未完成的交接
sb job abandon <job_id> --run-id <run_id> --reason "sub-agent crashed"
```

一个运行 id（run id）只接受一份答案。同一个运行再提交一份不同的答案，会被拒绝。

## API 执行器

工具自己调用端点。生产者和复核者必须是不同家族的模型。工具根据模型名判断家族（包括配置里的名称，以及提供方在回复里报告的名称），依据是一张名称片段表，例如 `claude`、`gpt`、`gemini`、`qwen`。你可以在 `[model_families]` 里添加自己的条目。

1. 运行 `pip install -e ".[api]"`。
2. 在配置文件里加上 `[executor.producer]` 和 `[executor.reviewer]`（[configuration.zh-CN.md](configuration.zh-CN.md)）。`kind = "openai"` 适用于任何 OpenAI 兼容的 `/chat/completions` 端点；`kind = "anthropic"` 使用 Anthropic Messages API。
3. 把每个 key 放进 `api_key_env` 指定的环境变量；或者在 `api_key_cmd` 里给出一条能输出这个 key 的命令。
4. 检查：`sb doctor` 显示配置，以及每个 key 是否可用；`sb doctor --live` 向每个端点发一次小请求。
5. 运行 `sb ingest <file> --executor api`；或者在设了 `[executor] default = "api"` 时运行 `sb ingest --all`。

遇到限流和服务器错误时，工具会退避后重试。块太大、放不进模型上下文时，工具把它一分为二再试。答案被截断或不是 JSON 时，算作无效答案，并用掉这个块的那一轮返修。

## MCP 服务

运行 `pip install -e ".[mcp]"`，然后在你的 MCP 宿主里注册这个服务。大多数宿主接受下面这样的 JSON 条目（Claude Code 写在项目的 `.mcp.json` 里）：

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

如果宿主不是从你的虚拟环境启动 `sb`，就写 `sb` 的完整路径。

| 服务 | 工具 |
|---|---|
| `sb serve`（只读） | `status`、`inbox_list`、`search`、`read_cards`、`read_taxonomy`、`read_task`、`job_show`、`job_packet` |
| `sb serve --writer` | 以上全部，另加 `ingest_prepare`（可选的放置参数 `domain`、`pillar`、`topic`，同 `sb ingest --domain`）、`job_submit`、`job_advance` |

`job_packet` 返回给子代理的完整任务文本；它在处理状态里记一次运行，不碰知识库。`job_submit` 保存并校验答案，从不写入知识。只有写入服务上的 `job_advance` 会写知识库，而且只在过了复核闸门之后。如果想让某些宿主只有读权限，就把两个服务分开注册。

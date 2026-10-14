# 配置

[English](configuration.md)

每个配置项都是可选的。没有配置文件时，`sb` 使用下文的默认值。

## 设置从哪里来

| 设置 | 优先顺序 |
|---|---|
| 知识库目录 | 先看 `--vault`，再看 `$SECONDBRAIN_VAULT`，最后看配置文件里的 `vault` |
| 配置文件 | 先看 `--config`，再看 `$SECONDBRAIN_CONFIG`，最后是 `$XDG_CONFIG_HOME/secondbrain/config.toml`（默认为 `~/.config/secondbrain/config.toml`） |
| 处理状态 | 先看 `$SECONDBRAIN_STATE_DIR`，再看配置文件里的 `state_dir`，最后是 `$XDG_STATE_HOME/secondbrain`（默认为 `~/.local/state/secondbrain`） |

配置文件用 TOML 格式。未知的节或配置项会报错，这样拼写错误不会被漏掉。`sb doctor` 输出它读取的文件、知识库、目录布局和各个端点。

处理状态（冻结的输入、任务文件、答案、运行记录、事务日志和锁）必须放在知识库之外。在状态目录里，每个知识库有自己的一个目录，目录名是知识库路径的哈希值。

## 完整示例

```toml
vault = "~/Obsidian/MyVault"
# state_dir = "~/.local/state/secondbrain"

[layout]            # 知识库内的目录名；所示为默认值
knowledge = "10_Knowledge"
mocs = "20_MOCs"
taxonomy = "20_MOCs/_pillars"
sources = "40_Sources"
inbox = "00_Inbox"
reviews = "50_Reviews"
data = "_data"
entry_docs = []     # 顶层笔记，`sb docs` 刷新其中生成的区块，例如 ["Guide.md"]

[executor]
default = "api"     # api | host

[executor.producer]
kind = "openai"     # openai（任何 OpenAI 兼容端点）| anthropic
base_url = "https://api.example.com/v1"
model = "producer-model-name"
api_key_env = "SB_PRODUCER_API_KEY"
max_output_tokens = 32000
timeout_s = 600

[executor.reviewer]  # 用与生产者不同模型家族的模型
kind = "anthropic"
base_url = "https://api.anthropic.com"
model = "reviewer-model-name"
api_key_env = "SB_REVIEWER_API_KEY"

[review]
require_different_family = true
allow_same_family_in_host_mode = true
withhold_producer_reasons = true
max_repair_rounds = 1

[model_families]    # 子串 -> 家族，合并到内置表之上
# my-local-model = "local"

[ingest]
chunk_chars = 12000
after_ingest = "move"        # move | keep
new_topics_per_source = 2
converter = "none"           # none | markitdown
id_prefix = "kc_"

[cards]
language = "en"              # en | zh：卡片小节名，以及地图页、HOME、复习页的文字
diagrams = true              # 每 10 张新卡多一次模型调用；Mermaid 由程序生成

[taxonomy]
extra_reviewers = 0

[git]
commit = false
```

## 配置项

### 顶层

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `vault` | 无 | 知识库目录。`~` 会被展开。 |
| `state_dir` | 见上文 | 处理状态存放的位置。不能在知识库里面。 |

### `[layout]`

目录名相对于知识库。各目录不能互相重叠。

| 配置项 | 默认值 | 存放内容 |
|---|---|---|
| `knowledge` | `10_Knowledge` | 卡片，每个领域一个目录：`<knowledge>/<domain>/<title>.md` |
| `mocs` | `20_MOCs` | 生成的地图页，每个 MOC 一个根页面和一个支柱页面目录 |
| `taxonomy` | `20_MOCs/_pillars` | 每个 MOC 一个 YAML 文件，列出它的支柱和主题 |
| `sources` | `40_Sources` | 已入库来源的副本：`<sources>/<first 16 hex of sha256>/<file name>` |
| `inbox` | `00_Inbox` | 新材料 |
| `reviews` | `50_Reviews` | 生成的每日复习页（`sb today`） |
| `data` | `_data` | 登记表、领域目录（catalog）、复习日志 |
| `home` | `HOME.md` | 生成的首页 |
| `entry_docs` | 无 | 手写的顶层笔记，`sb docs` 刷新其中生成的区块，例如 `["Guide.md"]`。区块最后一行说明 `sb` 只写这个区块，笔记其他地方提到的命令不是 `sb` 的命令 |
| `extra_dirs` | 无 | 另外计入首页目录表的顶层目录（也计入可选的 `kb-dirs` 区块） |

### `[executor]`、`[executor.producer]`、`[executor.reviewer]`

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `default` | `api` | 命令没有 `--executor` 时用的执行器。`host` 把每一步交给一个子代理；`api` 调用端点。 |
| `kind` | `openai` | `openai` 用于任何 OpenAI 兼容的 `/chat/completions` 端点；`anthropic` 用于 `/v1/messages`。 |
| `base_url` | 无 | 端点的基础 URL。 |
| `model` | 无 | 发给端点的模型名。 |
| `api_key_env` | 无 | 存放 key 的环境变量。 |
| `api_key_cmd` | 无 | 输出 key 的命令（按 shell 命令的规则拆分，但不经过 shell 运行；超时 30 秒）。 |
| `max_output_tokens` | 32000 | 每次调用的输出上限。 |
| `timeout_s` | 600 | 每次调用的超时。 |
| `max_retries` | 3 | 遇到限流和服务器错误时的重试次数（带退避，遵守 `retry-after`）。 |
| `structured_output` | `auto` | `auto` 先试 JSON Schema 输出，再试 JSON 模式，最后用纯文本；也可以强制用 `json_schema` 或 `json_object`。 |

写进文件的 key（`api_key = ...`）会被拒绝。key 在每次请求时读取，从不写进日志，也从不保存。

### `[review]`

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `require_different_family` | true | 模型名来自提供方的响应时（API 执行器），生产者和复核者必须属于不同模型家族。 |
| `allow_same_family_in_host_mode` | true | 宿主模式下，模型名是自报的；生产者和复核者同属一个家族也允许，任务记录 `same_family: true`。 |
| `withhold_producer_reasons` | true | 复核者看不到生产者的理由。 |
| `max_repair_rounds` | 1 | 每个块里被退回或无效的卡最多返修几轮。`sb ingest --max-rounds N` 可以在单次运行里覆盖它。 |

### `[model_families]`

把模型名里的一个子串映射到一个模型家族，供独立复核检查使用。内置表认识 `claude`、`gpt`、`o1`、`o3`、`o4`、`gemini`、`gemma`、`glm`、`deepseek`、`qwen`、`kimi`、`moonshot`、`grok`、`llama`、`mistral` 和 `mixtral`。你的条目合并到内置表之上。

### `[ingest]`

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `chunk_chars` | 12000 | 目标块大小，按字符计（1,000 到 400,000）。块先在标题和空行处切分，再在句末切分。 |
| `after_ingest` | `move` | `move` 在同一个事务里删除收件箱文件（它的字节保存在 `sources` 里）；`keep` 把它留在收件箱。 |
| `new_topics_per_source` | 2 | 生产者为一份来源可以提出的新主题数。 |
| `converter` | `none` | `markitdown` 转换非文本文件（需要 `markitdown` 包）。 |
| `id_prefix` | `kc_` | 卡片 id 是这个前缀加上卡片的 slug。 |
| `max_source_bytes` | 8 MiB | 更大的来源会被拒绝，不会被截断。 |
| `neighbors_per_card` | 3 | 每张新卡给复核者看的已有卡片数。 |
| `candidate_domains` | 3 | 处理一个块时，生产者能看到其分类体系的领域数。 |

### `[cards]`

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `language` | 知识库的语言 | `en` 或 `zh`。它决定新卡片的小节名（`en`：Essence, Propositions, Diagram, Intuition and contrasts, Related, Review cards），以及生成页面的固定文字：地图页、`HOME.md` 和每日复习页。卡片正文跟随来源的语言。没有这个配置项时，`sb` 用知识库的语言（记录在领域目录里；没有记录就从它的地图页判断；仍判断不出就用 `en`）。如果这个配置项指定的语言与知识库的语言不同，写入会被拒绝，直到 `sb language --set` 切换知识库的语言。 |
| `diagrams` | true | 复核之后运行画图步骤（[diagrams.md](diagrams.zh-CN.md)）。 |

### `[taxonomy]` 和 `[git]`

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `extra_reviewers` | 0 | 保留项。暂不支持大于 0 的值。 |
| `commit` | false | 写入之后，把这次写过的路径（不多不少）提交到知识库的 git 仓库。提交失败会被记录，不会回滚这次写入。 |

## 环境变量

| 变量 | 含义 |
|---|---|
| `SECONDBRAIN_VAULT` | 知识库目录 |
| `SECONDBRAIN_CONFIG` | 配置文件 |
| `SECONDBRAIN_STATE_DIR` | 处理状态目录 |
| `SECONDBRAIN_LOCK_WAIT` | 等待另一个写者的锁的秒数（默认 30） |
| `SECONDBRAIN_NO_CACHE=1` | 每张卡都从磁盘读取，不用解析缓存 |
| `SECONDBRAIN_TEST_TIMEOUT` | 单个测试超过这么多秒就判为失败（默认 300；`0` 关闭限制；只在 POSIX 系统上有效） |

## 安装可选依赖

| 可选依赖（extra） | 增加 | 用于 |
|---|---|---|
| `api` | `httpx` | API 执行器 |
| `mcp` | `mcp` | `sb serve` |
| `review` | `fsrs`（固定版本） | `sb review`、`sb today` 和 `sb daily` 的复习页（没有它时，`sb daily` 只写 `HOME.md`）；`sb status` 里的复习统计 |
| `test` | 以上全部，加上 `pytest` | 运行测试 |

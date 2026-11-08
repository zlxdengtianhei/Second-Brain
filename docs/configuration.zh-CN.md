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
articles = "60_Articles"  # `sb export` 写文章的目录
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
max_retries = 2

[executor.reviewer]  # 用与生产者不同模型家族的模型
kind = "anthropic"
base_url = "https://api.anthropic.com"
model = "reviewer-model-name"
api_key_env = "SB_REVIEWER_API_KEY"

[review]
require_different_family = true
allow_same_family_in_host_mode = true
withhold_producer_reasons = true
max_repair_rounds = 2        # 内容返修轮数：每块、每张拆出的卡、每个文章写作单位
max_invalid_retries = 2      # 答案没过程序校验时重新要答案的次数（每个槽位）
focused_review = true        # 返修轮的复核者逐条核上一轮的问题
amend = true                 # 复核者接受时可以直接给机械修正

[model_families]    # 子串 -> 家族，合并到内置表之上
# my-local-model = "local"

[ingest]
chunk_chars = 12000
after_ingest = "move"        # move | keep
new_topics_per_source = 2
converter = "none"           # none | markitdown
id_prefix = "kc_"
candidates_k = 15            # 每次检索取的已有卡数，各角色相同
candidates_chars = 40000     # 一个包里检索到的卡的视图最多多少字
candidates_hop = 0           # 第二路：前 N 张命中卡链接的卡（0：不开）
candidates_shelf = true      # 书架：一个主题下的卡，只给标题
review_named = true          # 复核者看到生产者点名的每张已有卡的全文
review_words_view = "brief"  # brief | full：复核包里检索命中的卡用哪种视图
merge_into_existing = true   # 补充已有卡的新卡补进那张卡
context_note = "program"     # off | program | summary | concepts
context_note_chars = 2000

[cards]
language = "en"              # en | zh：卡片小节名，以及地图页、HOME、复习页的文字
diagrams = true              # 每 10 张新卡多一次模型调用；Mermaid 由程序生成

[taxonomy]
extra_reviewers = 0

[git]
commit = false

[export]
mode = "sequential"          # sequential | parallel
# [export.writer] 和 [export.reviewer]：写文章和复核文章的模型（docs/export.zh-CN.md）
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
| `articles` | `60_Articles` | `sb export` 写出的文章：生成的视图，不是知识卡（[export.zh-CN.md](export.zh-CN.md)） |
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
| `max_retries` | 2 | 限流、服务器错误、401/403、超时和空响应体在执行器内重试的次数（带退避，遵守 `retry-after`）；不占用 `max_invalid_retries`。 |
| `structured_output` | `auto` | `auto` 先试 JSON Schema 输出，再试 JSON 模式，最后用纯文本；也可以强制用 `json_schema` 或 `json_object`。 |

写进文件的 key（`api_key = ...`）会被拒绝。key 在每次请求时读取，从不写进日志，也从不保存。

### `[review]`

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `require_different_family` | true | 模型名来自提供方的响应时（API 执行器），生产者和复核者必须属于不同模型家族。 |
| `allow_same_family_in_host_mode` | true | 宿主模式下，模型名是自报的；生产者和复核者同属一个家族也允许，任务记录 `same_family: true`。 |
| `withhold_producer_reasons` | true | 复核者看不到生产者的理由。 |
| `max_repair_rounds` | 2 | 内容返修轮数：复核者附理由退回的卡、拆出的卡或文章写作单位，最多重写几次。合并核对因异议重跑的次数也受它限制。`sb ingest --max-rounds N`（以及 `sb export --max-rounds N`）可以在单次运行里覆盖它。取值 0 到 10。 |
| `max_invalid_retries` | 2 | 答案没过程序校验时，最多重新要几次答案，按槽位计：块、复核、合并核对、拆出的卡、模型写的说明。不占返修轮。失败次数达到这个数加一之后，才能用 `sb job skip-source-check`。取值 0 到 10。 |
| `focused_review` | true | 返修轮的复核者逐条说上一轮的问题改好没有。只有问题没改好，或有阻塞类问题（`fact`、`unreadable`、`quote`、`not_a_unit`）时，才退回卡片；别的问题记进备注。 |
| `amend` | true | 复核者接受一张卡时，可以直接补、改或删一条关系，把卡改归到已有主题下，或删掉一组问答。程序核对结果合法后照改，不占返修轮（[ingest.zh-CN.md](ingest.zh-CN.md#复核规则)）。 |

默认值来自一次实验：返修 1、2、3 轮，两个开关分别开和关。进了复核的块都在 1 轮返修之内了结；写入的 225 张卡里有 6 张（2.7%）有真问题。

任务保留冻结时的 `[review]` 和 `[ingest]` 设置，所以这里的改动只对之后开始的任务生效（[任务保留的设置](ingest.zh-CN.md#任务保留的设置)）。

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
| `candidate_domains` | 3 | 处理一个块时，生产者能看到其分类体系的领域数。 |
| `candidates_k` | 15 | 每次检索取的已有卡数，各角色相同：生产者的每一段、复核者的每张新卡、合并核对的每张卡，以及重试时这份来源已写入的卡。取值 1 到 100，或 `"auto"`（各角色沿用以前的张数）。在 197 到 8,738 张卡的库上，取多于 15 张也找不到更多重复。 |
| `candidates_chars` | 40000 | 一个包里检索到的卡的视图最多占多少字；生产者点名的卡不计入。超出时，排名靠后的卡改用较短的视图，再不够就不放（[候选卡](ingest.zh-CN.md#候选卡)）。取值 1,000 到 1,000,000。 |
| `candidates_hop` | 0 | 第二路：前 N 张检索命中卡所链接的卡。0 表示不开：实验里链接来的卡并没有比多检索几张找到更多重复。取值 0 到 10。 |
| `candidates_shelf` | true | 书架：一个主题下的卡，只给标题，最多 25 张。生产者拿到检索命中卡所在各主题的书架，复核者拿到每张新卡所在主题的书架。书架最后装包，所以超出预算时最先去掉。 |
| `review_named` | true | 复核者看到生产者点名的每张已有卡的全文：关系目标、`same` 覆盖行、冲突对象、重复提示。 |
| `review_words_view` | `brief` | 复核包里检索命中的卡用哪种视图：`brief` 或 `full`。用 Flash 一档的模型做复核时设成 `full`：实验里 DeepSeek 复核者在 `brief` 下检出的注入错误少约 6 个百分点，Opus 复核者只少约 1 个百分点。 |
| `merge_into_existing` | true | 补充已有卡的新卡补进那张卡；那张卡补完会超过 12 条命题或 8 组问答时，新卡改写成一张回答更窄问题的卡。设为 `false` 时，新卡写在已有卡旁边，标为待复核。 |
| `context_note` | `program` | 每块在来源里所处位置的说明：`off`、`program`（程序从来源结构取，不调模型）、`summary` 或 `concepts`（各多一次便宜的模型调用）（[前文说明](ingest.zh-CN.md#前文说明)）。 |
| `context_note_chars` | 2000 | 前文说明的长度，按字符计。取值 200 到 20,000。 |

以前版本的 `neighbors_per_card` 这一版仍然接受，但不再读取。

### `[cards]`

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `language` | 知识库的语言 | `en` 或 `zh`。它决定新卡片的小节名（`en`：Essence, Propositions, Diagram, Intuition and contrasts, Related, Review cards），以及生成页面的固定文字：地图页、`HOME.md` 和每日复习页。卡片正文跟随来源的语言。没有这个配置项时，`sb` 用知识库的语言（记录在领域目录里；没有记录就从它的地图页判断；仍判断不出就用 `en`）。如果这个配置项指定的语言与知识库的语言不同，写入会被拒绝，直到 `sb language --set` 切换知识库的语言。 |
| `diagrams` | true | 复核之后运行画图步骤（[diagrams.md](diagrams.zh-CN.md)）。 |

### `[export]`

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `mode` | `sequential` | `sequential` 按写作单位逐个写文章，每个写作者能看到上一单位的末尾；`parallel` 所有单位同时写。`sb export --mode` 可以在单次运行里覆盖它。 |
| `[export.writer]` | 无 | 写文章的模型，配置项与 `[executor.producer]` 相同。没设时用 `[executor.producer]`。 |
| `[export.reviewer]` | 无 | 复核文章的模型，配置项相同。没设时用 `[executor.reviewer]`。API 模式下它必须和写作者属于不同模型家族。 |

细节见 [export.zh-CN.md](export.zh-CN.md)。

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
| `SB_MATHJAX_NODE_PATH` | 装有 `mathjax-full` 的 `node_modules` 目录，供 `sb export` 的公式检查使用 |
| `SB_MATHJAX_CHECK` | 换用另一个公式检查脚本（默认 `tools/mathjax_check.js`） |

## 安装可选依赖

| 可选依赖（extra） | 增加 | 用于 |
|---|---|---|
| `api` | `httpx` | API 执行器 |
| `mcp` | `mcp` | `sb serve` |
| `review` | `fsrs`（固定版本） | `sb review`、`sb today` 和 `sb daily` 的复习页（没有它时，`sb daily` 只写 `HOME.md`）；`sb status` 里的复习统计 |
| `curate` | `networkx` | `sb curate` 为超过 25 张卡的主题给出拆分提议 |
| `export` | `markdown`、`pymdown-extensions` | `sb export --html` |
| `test` | 以上全部，加上 `pytest` | 运行测试 |

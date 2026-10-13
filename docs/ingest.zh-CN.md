# 入库：从收件箱文件到卡片

[English](ingest.md)

本页说明 `sb ingest` 做什么、每项检查退回什么、写入什么，以及怎样恢复。[README](../README.zh-CN.md) 里有简短版本。

## 收件箱

```sh
sb inbox list                                   # 收件箱里的每个文件，附状态和任务 id
sb inbox show "<name>" --head 60                # 一个文件的前几行
sb inbox skip "<name>" --reason "a receipt"     # 把一个文件标为不是知识
```

文件的状态是 `new`、`in_progress`、`done`、`partial`（还有卡片待定；见 `sb ingest --retry`）、`skipped` 或 `failed`。状态按内容哈希确定，所以已入库文件改名后的副本仍是 `done`。`partial` 的来源在它的文件离开收件箱后仍留在列表里，这时 `in_inbox: false`，`path` 指向 `40_Sources/` 里的副本。[Agent Skill](../skills/secondbrain/references/triage.md) 列出哪些内容应该做成卡片，哪些不应该。

## 开始处理一份来源

```sh
sb ingest <file>...                 # 一个或多个文件，位置不限
sb ingest --all                     # 收件箱里每个新的或未完成的文件
sb ingest <file> --prepare-only     # 只冻结和切块，不调用模型
sb ingest <file> --proposal-only    # 生产和复核，但不写入
sb ingest --retry <job>             # 再试一次部分完成的任务里的待定卡
```

`--executor host|api` 覆盖 `[executor] default`。`--max-rounds N` 覆盖本次运行的返修轮数。

## 各个步骤

| 步骤 | 做什么 |
|---|---|
| 冻结 | 来源字节、提取出的文本、各个块、领域目录（catalog）、完整的分类体系和最接近的卡片都存为任务（job）的输入，并附一个哈希。没有配置转换器时，只读取 UTF-8 文本。超过 `max_source_bytes` 的来源会被拒绝。 |
| 切块 | 文本被切成精确、连续的片段，每段约 `chunk_chars` 个字符。优先在标题和空行处切，其次在句末切。 |
| 路由 | 块里的每个段落都在已有卡片中检索。最佳匹配所在的领域成为这个块的候选领域，最佳匹配本身成为它的近邻（neighbours）。 |
| 生产 | 生产者拿到：这个块；所有领域及各自的引导问题；候选领域的完整分类体系（附每个主题的卡片数）；近邻；同一来源其他块已经提出的卡片。它返回卡片、提议的新主题和一张覆盖表。 |
| 检查 | 答案提交时，程序校验它（清单见下）。错误退回给生产者。一份无效答案会用掉这个块的返修轮。 |
| 复核 | 复核者拿到：这个块；去掉生产者理由的卡片；每张新卡最接近的三张已有卡片（全文）；分类体系。它对每张卡判定 `accept`、`revise` 或 `duplicate`，对每个新主题判定 `accept` 或 `revise`，并判定覆盖表是否正确。每项判定都附理由。 |
| 返修 | 标为 `revise` 的卡片连同复核者的意见退回生产者，默认一次。已接受的卡片保留。 |
| 闸门 | 只有另一次运行中的复核者接受了的卡片才会写入。仍被退回的卡片成为待定，附上理由。归在被退回的新主题下的卡片也成为待定。 |
| 画图 | 已接受的卡片进入画图步骤（[diagrams.zh-CN.md](diagrams.zh-CN.md)）。 |
| 写入 | 一个事务（见下文）。 |
| git 提交 | 设了 `[git] commit = true` 时，工具提交写过的路径。提交失败会记录下来，但不撤销写入。 |

### 检查会退回什么

- `source_quotes` 里的引文不是块中的逐字原文（错误信息会给出最接近的段落）；
- 分类体系里没有的领域、支柱或主题，除非它是已有支柱下一个合法的新主题提议；
- 与已有主题重复或只是改名的新主题、超过每份来源允许数量的新主题，或没有卡片使用的提议主题；
- 领域目录里没有的关系类型，或既不是已有卡片、也不是本来源卡片的关系目标；
- 重复的 slug、不能用作文件名的标题，或已有卡片已经在用的标题；
- 少于两组或多于四组问答，或问答里含有 `::`；
- 写进正文的卡片 id（卡片之间用 `[[Title]]` 互相引用）；
- 覆盖表里缺少的卡片，或引文不在块中的覆盖表行。

## 写入哪些内容

在一个事务里写入：

- 每张已接受的卡片，存为 `10_Knowledge/<domain>/<title>.md`，由程序根据复核过的字段生成；
- 已有卡片上的反向关系（只改 front matter）；
- 已接受的新主题，写进分类体系文件；
- 来源副本，存为 `40_Sources/<first 16 hex of sha256>/<file name>`；
- `_data/manifest.jsonl`、收件箱登记表和 `_data/notes_registry.json`；
- 受影响领域的地图页（MOC）和首页 `HOME.md`；
- 设了 `after_ingest = "move"` 时，从收件箱删除这个文件（它的字节已在来源副本里）。

指向待定卡片的关系不写入，在结果里列为 `skipped_relations`。

## 任务与恢复

所有进度都存在任务里，位于知识库之外。中断之后，先运行 `sb job list` 和 `sb job show <job_id>`，再用 `sb job advance <job_id>` 继续。

```sh
sb job list
sb job show <job_id>
sb job advance <job_id> --executor host
sb recover <job_id> --rollback      # 逐字节还原这个任务写过的每个文件
sb recover <job_id> --resume        # 完成被打断的写入
sb job reset <job_id> --reason "<why>"   # 搁置一个卡住且从未写入的任务
```

写入之前，事务把每个文件与计划时所依据的字节比对。如果期间有别的程序改了某个文件，任务停下，状态为 `needs_attention`；下一次 `sb job advance` 用同一批复核过的卡片重新规划写入。多份来源可以同时处理；对同一个知识库的写入在锁的保护下逐个进行。

回滚过的来源可以再次入库。

`sb ingest --retry <job>` 把部分完成的任务里的待定卡作为子任务运行。子任务名为 `<job>-retry1`，依此类推，各有自己的事务。先回滚重试子任务，再回滚父任务：子任务写入的内容还在知识库里时，`sb recover <parent> --rollback` 会拒绝执行，并指出是哪个子任务。已回滚或已重置的子任务不妨碍新的 `sb ingest --retry <parent>`；新的子任务沿用它的 id。

## 用于已有知识库的其他命令

这些命令对知识库里已有的卡片使用同样的生产、复核、闸门模式。

| 命令 | 作用 |
|---|---|
| `sb refresh` | 预览会变动的地图页；加 `--apply` 写入 |
| `sb classify --domain <slug>` | 为没有支柱和主题的卡片提议支柱和主题（`--repair-invalid` 用于分类已不再合法的卡片） |
| `sb backlog` | 分批对整个领域或整个知识库做分类 |
| `sb curate --domain <slug>` | 提议修改已有领域的支柱和主题 |
| `sb relocate --source-job <job>` | 分类复核建议换领域时，把卡片移到另一个领域 |
| `sb domcreate --domain <slug> ...` | 根据已有卡片提议一个新领域 |
| `sb resume --job <job>` | 继续上述命令的某个任务 |
| `sb docs --apply` | 重新生成首页 `HOME.md`（以及 `[layout] entry_docs` 的生成块） |
| `sb today --apply` | 在 `50_Reviews/` 下写今天的复习页：复习登记表里的到期卡和新卡，与 `sb review queue` 是同一个队列。需要 `review` 可选依赖（extra） |
| `sb daily --apply` | 两件事都做；没装 `review` 可选依赖时只写 `HOME.md`，并报告 `review_extra_missing` |

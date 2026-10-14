# 入库：从收件箱文件到卡片

[English](ingest.md)

本页说明 `sb ingest` 做什么、每项检查退回什么、写入什么，以及怎样恢复。[README](../README.zh-CN.md) 里有简短版本。

## 收件箱

```sh
sb inbox list                                   # 收件箱里的每个文件，附状态和任务 id
sb inbox show "<name>" --head 60                # 一个文件的前几行
sb inbox skip "<name>" --reason "a receipt"     # 把一个文件标为不是知识
```

文件的状态是 `new`、`in_progress`、`done`、`partial`（还有卡片待定；见 `sb ingest --retry`）、`skipped` 或 `failed`。状态按内容哈希确定，所以已入库文件改名后的副本仍是 `done`。来源的文件离开收件箱后，下面两种仍留在列表里：`partial` 的来源，以及从收件箱入库、状态为 `done` 的来源。这时 `in_inbox: false`，`path` 指向 `40_Sources/` 里的副本。[Agent Skill](../skills/secondbrain/references/triage.md) 列出哪些内容应该做成卡片，哪些不应该。

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
| 路由 | 块里的每个段落都在已有卡片中检索。最佳匹配所在的领域成为这个块的候选领域。整块的最佳匹配，加上每个段落的前三个匹配（最多 40 张），成为它的近邻（neighbours）。 |
| 生产 | 生产者拿到：这个块；所有领域及各自的引导问题；候选领域的完整分类体系（附每个主题的卡片数）；近邻；同一来源其他块已经提出的卡片。它返回卡片、提议的新主题和一张覆盖表。 |
| 检查 | 答案提交时，程序校验它（清单见下）。错误退回给生产者。一份无效答案会用掉这个块的返修轮。 |
| 复核 | 复核者拿到：这个块；去掉生产者理由的卡片；每张新卡最接近的五张已有卡片的 id，以及这些卡片各一份摘要（标题、领域、本质，在大小预算内再加命题）；分类体系。它对每张卡判定 `accept`、`revise` 或 `duplicate`，对每个新主题判定 `accept` 或 `revise`，并判定覆盖表是否正确。每项判定都附理由。接受一张链接了已有卡片的卡时，它还要给出 `overlap`：卡片有自己可回忆的内容就是 `distinct`；主要是在重复所链接的某张卡，就是 `needs_review`，并用 `overlap_with` 指出那张卡。没有自己可回忆内容的卡判为 `duplicate`。生产者和复核者都可以标出 `conflicts`，即新卡与已有卡说法不一致的地方。 |
| 返修 | 标为 `revise` 的卡片连同复核者的意见退回生产者，默认一次。已接受的卡片保留。这一轮还会看到前几轮没有做成卡片就已了结的内容（与已有卡片重复的、超出范围的段落，即 `already_settled`），不会再写一遍。如果复核者退回了一个新主题，归在它下面、已被接受的卡片会作为 `refile_cards` 退回；生产者可以用同样的 slug，把它们归到改名后的主题或已有主题下，再交复核。返修时被拆分或去掉的被退回卡片不再留作待定，由后一轮的覆盖判定决定。 |
| 闸门 | 只有另一次运行中的复核者接受了的卡片才会写入。仍被退回的卡片成为待定，附上理由。返修后仍归在被退回的新主题下的卡片也成为待定。 |
| 画图 | 已接受的卡片进入画图步骤（[diagrams.zh-CN.md](diagrams.zh-CN.md)）。 |
| 写入 | 一个事务（见下文）。 |
| git 提交 | 设了 `[git] commit = true` 时，工具提交写过的路径。提交失败会记录下来，但不撤销写入。 |

### 检查会退回什么

- `source_quotes` 里的引文不是块中的逐字原文（错误信息会给出最接近的段落）；
- 分类体系里没有的领域、支柱或主题，除非它是已有支柱下一个合法的新主题提议；
- 与已有主题重复或只是改名的新主题、超过每份来源允许数量的新主题，或没有卡片使用的提议主题；
- 卡片所在领域不能用的关系类型（领域目录的 `relation_types` 对所有领域开放；只列在 `domain_relations.<domain>` 下的类型只对那个领域开放）、目标领域不能用其反向类型的链接，或既不是已有卡片、也不是本来源卡片的关系目标；
- 同一对卡片之间的两条关系（程序补写的反向关系也算在内）；
- 方向相反的两条关系：一张卡片对同一目标既有某个类型又有它的反向类型，或两张卡片互相声称同一类型；
- 有 `contrasts_with` 关系、却没在 `confusions` 里写出区别的卡片；
- 重复的 slug、生成的卡片 id 已被已有卡片占用的 slug（已有 `kc_rag-vs-finetune` 时的 `rag-vs-finetune`）、不能用作文件名的标题，或已有卡片已经在用的标题；
- 少于两组或多于四组问答，或问答里含有 `::`；
- 写进正文的卡片 id（卡片之间用 `[[Title]]` 互相引用）；
- 覆盖表里缺少的卡片，或引文不在块中的覆盖表行。

## 写入哪些内容

在一个事务里写入：

- 每张已接受的卡片，存为 `10_Knowledge/<domain>/<title>.md`，由程序根据复核过的字段生成。复核者标为 `needs_review` 的卡片写成 `status: needs-review`，并在结果的 `needs_review` 里列出；这个状态改掉之前，复习队列不会把它当新卡排入。`sb status` 统计这类卡片（`needs_review`），`sb status --needs-review` 逐张列出，`HOME.md` 在「现在做什么」下给出数量；
- 已有卡片上的反向关系，写进 front matter 和相关小节（卡片其余部分不变）；
- 已接受的新主题，写进分类体系文件；
- 来源副本，存为 `40_Sources/<first 16 hex of sha256>/<file name>`；
- `_data/manifest.jsonl`、收件箱登记表和 `_data/notes_registry.json`；
- 发现的冲突，追加到 `_data/secondbrain/conflicts.jsonl`；
- 受影响领域的地图页（MOC）和首页 `HOME.md`；
- 设了 `after_ingest = "move"` 时，从收件箱删除这个文件（它的字节已在来源副本里）。

指向被复核者判为重复的卡片的关系，改为指向它所重复的那张已有卡片。指向待定卡片的关系不写入，在结果里列为 `skipped_relations`。这些关系会带到重试子任务：子任务写入目标卡片时补上这条关系，并在先前那张卡上加反向链接；子任务的结果在 `restored_relations` 里列出。

程序不会自动合并或改正任何内容。任务结果在 `conflicts` 里列出冲突，`sb status` 在 `conflicts` 下列出所有未解决的冲突。已有卡片的正文改动之前，冲突一直列着；只给那张卡补上反向关系不算改动。

## 任务与恢复

所有进度都存在任务里，位于知识库之外。中断之后，先运行 `sb job list` 和 `sb job show <job_id>`，再用 `sb job advance <job_id>` 继续。

```sh
sb job list
sb job show <job_id>
sb job advance <job_id> --executor host
sb recover <job_id> --rollback      # 逐字节还原这个任务写过的每个文件
sb recover <job_id> --resume        # 完成被打断的写入
sb job reset <job_id> --reason "<why>"   # 搁置一个卡住且从未写入的任务
sb job prune --days 30              # 预览旧的回滚日志；加 --apply 才删除
```

写入之前，事务把每个文件与计划时所依据的字节比对。如果期间有别的程序改了某个文件，任务停下，状态为 `needs_attention`；下一次 `sb job advance` 用同一批复核过的卡片重新规划写入。多份来源可以同时处理；对同一个知识库的写入在锁的保护下逐个进行。

回滚过的来源可以再次入库。

`sb recover <job> --rollback` 要用任务的回滚日志（任务状态目录里的 `transaction.json`），工具从不自行删除它。`sb job prune` 预览可以删除的日志并给出大小：已完成、早于 `--days` 天（默认 30）、且不是其来源或操作最新一次写入的日志。同一来源的父任务和重试子任务算一个来源；其他任务按操作分组，例如 refresh 或 daily。`sb job prune --apply` 删除这些日志和它们的写入计划；这些写入从此不能撤销，写入的卡片保留。`sb status` 在 `state_dir` 下给出状态目录和它的大小。

`sb ingest --retry <job>` 把部分完成的任务里的待定卡作为子任务运行。子任务名为 `<job>-retry1`，依此类推，各有自己的事务。先回滚重试子任务，再回滚父任务：子任务写入的内容还在知识库里时，`sb recover <parent> --rollback` 会拒绝执行，并指出是哪个子任务。已回滚或已重置的子任务不妨碍新的 `sb ingest --retry <parent>`；新的子任务沿用它的 id。

重试子任务能看到这份来源以前的任务里没有做成卡片就已了结的内容（与已有卡片重复的、超出范围的段落），不会再写一遍。它的结果里有 `retry_progress`：`open_before`、`open_after`、`cards_written` 和 `progress`。如果子任务一张卡也没写，留下的待定项与收到的一样多（`progress: false`），结果里给的是 `message` 而不是重试 `hint`：再重试多半还是这样。先读 `pending` 里的理由（`sb job show <job>`），改掉卡住它们的原因（例如新主题所需的分类体系）后再重试；或者让它们留着，用 `sb inbox skip <file> --reason "..."` 结束这份来源。`sb inbox list` 和 `sb ingest <source>` 也会这样提示。对已经重试过的任务再运行 `sb ingest --retry`，不会再开子任务：它返回一份简短摘要，`message` 指出这条链上最新的任务；那个任务是 `partial` 且有进展时，还附上重试它的 `hint`。

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
| `sb today --apply` | 在 `50_Reviews/` 下写当前复习日的复习页：复习登记表里的到期卡和新卡，与这个复习日里 `sb review queue` 给出的队列相同。复习日从 04:00 开始，所以 01:00 生成的页仍属于前一天。它不改 `HOME.md`。需要 `review` 可选依赖（extra） |
| `sb daily --apply` | 两件事都做，`HOME.md` 会链接新生成的复习页；复习页缺失时，HOME 提示的就是这条命令。没装 `review` 可选依赖时只写 `HOME.md`，并报告 `review_extra_missing` |

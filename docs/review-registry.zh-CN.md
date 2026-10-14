# 复习登记表

[English](review-registry.md)

复习登记表把每张卡片的每次复习都记进一份只追加的事件日志。调度状态（到期日、稳定性、难度）从不存进卡片文件。每个读取方（reader）都用 FSRS 重放日志，重新算出调度状态。

四类写入方（writer）共用一种格式，每类写一种模式（`mode`）：

| 写入方 | 模式 | 位置 |
|---|---|---|
| Obsidian 插件，翻卡模式 | `flip` | `obsidian-plugin/` |
| Obsidian 插件，问答模式（由 LLM 评分） | `qa` | `obsidian-plugin/` |
| 终端 agent，经 `sb review log` | `agent` | `src/secondbrain/review_registry.py` |
| 一次性导入 Spaced Repetition 插件的数据（`sb review import-sr` 或插件命令） | `import` | 两处都有 |

CLI 命令 `sb review status | queue | log | import-sr` 调用 `secondbrain.review_registry` 里的 `status_report`、`queue_report`、`log_review` 和 `import_sr`。这些函数返回可直接转成 JSON 的数据。

有两个实现重放日志：Python（`review_registry.py`，py-fsrs）和 TypeScript（`obsidian-plugin/src/registry/`，ts-fsrs）。对同样的事件，两者必须算出同样的状态。`tests/fixtures/review/vectors.json` 里的共用测试向量就是两者之间的契约。

## 1. 文件

- 目录：知识库内的 `<data>/review/`（默认目录布局下是 `_data/review/`）。
- 每台写入设备一个文件：`events-<device>.jsonl`。写入方只往自己的文件追加，所以两台设备从不编辑同一个文件。
- `<device>` 匹配 `[A-Za-z0-9_-]{1,64}`。插件在每台设备上生成一次 `obs-<random>`，并把它存在设备本地存储里（不放在会同步的插件设置里）。Python 调用方传入一个设备 id；`default_device_id()` 给出 `cli-<hash of hostname>`。
- 读取方读取所有匹配 `events-*.jsonl` 或 `events-*.md` 的文件。同步工具产生的冲突副本（例如 `events-obs-1a2b (conflict).jsonl`）也匹配这个模式；重复的事件会被去除（第 4 节）。

### 1.1 `.jsonl` 存储（默认）

每行一个 JSON 对象，UTF-8 编码，行尾为 `\n`。空行被忽略。其他任何不是有效事件的行，都作为问题（issue）报告，并被跳过。

### 1.2 `.md` 存储（用于只同步笔记的同步服务）

除非打开了 "Sync all other types"，Obsidian Sync 不同步 `.jsonl` 文件。写入方可以改为把同样的行存进 `events-<device>.md`：

```text
---
format: secondbrain-review-log
---
{"v":1,"ts":"2026-10-13T08:12:30.000+02:00", ...}
{"v":1,"ts":"2026-10-13T08:14:02.000+02:00", ...}
```

在 `.md` 文件里，读取方只解析第一个非空格字符是 `{` 的行。其他所有行（front matter、标题、备注）都被忽略。写入方把事件行追加到文件末尾。

### 1.3 追加

写入方以追加模式打开文件，每个事件用一次 `write` 调用追加完整的一行，然后刷新（flush）。如果文件不以换行符结尾（之前某次写入被打断了），写入方先写一个 `\n`。这样新事件从单独的一行开始，残缺的片段作为问题被跳过。写入方在写入一个事件之前，先用读取方的规则校验它；无效的事件从不写入。

## 2. 事件格式，版本 1

```json
{"v":1,"ts":"2026-10-13T08:12:30.000+02:00","device":"obs-3f9a2c","card":"ex_binary_search",
 "mode":"qa","rating":2,"grader":"llm:example-model",
 "items":[{"q":"h:1a2b3c4d","rating":3,"verdict":"hit"},
          {"q":"h:5e6f7a8b","rating":2,"verdict":"partial","missing":["loop invariant"]}],
 "ms":41000}
```

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `v` | 整数 | 是 | 格式版本。读取方跳过版本未知的行，并报告一个问题。 |
| `ts` | 字符串 | 是 | 复习时间，RFC 3339 格式，带 UTC 偏移（`Z` 或 `+02:00`；偏移的小时为 00–23，分钟为 00–59）。写入方用带毫秒的本地时间。偏移是数据的一部分：它决定学习日（第 3.1 节）。 |
| `device` | 字符串 | 是 | 写入设备的 id。 |
| `card` | 字符串 | 是 | 卡片 frontmatter 里的 `id`。重命名或移动卡片文件不会打断它的历史记录。 |
| `mode` | 字符串 | 是 | `flip`、`qa`、`agent` 或 `import`。读取方把 `import` 以外的任何模式都当作一次复习，所以新增的模式仍然可读。 |
| `rating` | 整数 | 见说明 | 卡片评级：1 Again，2 Hard，3 Good，4 Easy。复习事件必须有此字段，除非带有 `items`。 |
| `items` | 数组 | 否 | 一次卡片复习中每道题的明细（第 2.1 节）。 |
| `grader` | 字符串 | 否 | `self`（默认）或 `llm:<model>`。 |
| `answer`、`feedback` | 字符串 | 否 | 单题复习时学习者的回答和评分方的反馈。 |
| `ms` | 整数 | 否 | 所用时间，单位为毫秒。 |
| `import` | 对象 | `import` 事件必填 | 来自另一个调度器的初始状态（第 2.2 节）。 |

读取方忽略未知字段。写入方不得把已有的字段名换成另一种含义再用。匹配模式（`ts`、日期、`device`）必须匹配整个值：末尾带换行符的值无效。时间戳和日期里的数字只能是 ASCII `0`–`9`（全角数字或其他文字系统的数字会使这一行无效）。

### 2.1 题目与卡片评级

调度的单位是卡片。问答复习逐道问卡片里的题目，每道题收集一个评级，然后为这张卡写**一个**事件。

- 每个条目：`q`（题目 id，必填）、`rating`（1–4，必填），以及可选的 `verdict`（`hit`、`partial`、`miss`）、`grader`、`answer`、`feedback`、`missing`（字符串列表）。
- 卡片评级：有 `rating` 字段时取它；否则取各条目评级的最小值（偏保守：一道答得差的题就会让这张卡更早回来）。带条目的写入方把 `rating` 写成这个最小值。
- 题目 id：`h:` 后接规范化后题目文本的 FNV-1a 32 位哈希（8 位小写十六进制数字）。规范化：Unicode NFKC，转小写，把每一串连续空白压成一个空格，去掉首尾空白。题目被编辑后，它的条目历史就断开了；卡片的调度不变。

卡片把题目放在 `## 复习卡` 小节里，每行写成 `question::answer`（小节名可以配置）。行尾的 Spaced Repetition 注释 `<!--SR:...-->` 会被忽略。

### 2.2 导入事件

导入事件用取自另一个调度器的初始状态（seed）替换卡片的调度状态。它不是一次复习：它不计入"今日已复习"、`reps` 或连续天数。

```json
{"v":1,"ts":"2026-10-13T09:00:00.000+02:00","device":"obs-3f9a2c","card":"ex_tcp_handshake",
 "mode":"import","import":{"from":"sr-sm2","due":"2026-10-20","interval":5,"ease":250}}
```

| `import.from` | 字段 | 初始状态 |
|---|---|---|
| `sr-sm2` | `due`（日期）、`interval`（天数 ≥ 1）、`ease`（SM-2 ease × 100） | 稳定性 = `interval`；难度 = clamp(10 − 9 × (ease − 130) / 220, 1, 10)，舍入到 4 位小数；上次复习 = `due` − `interval` 天 |
| `sr-fsrs` | `due`、`interval`、`stability`、`difficulty`，可选 `last_review` | 按给定值；上次复习 = `last_review`，或 `due` − `interval` 天 |

导入后，卡片处于 review 状态，`reps` 为 0，`lapses` 为 0。不带时间的日期，指导入事件所带偏移下那个学习日的开始（04:00）。期望保持率为 0.9 时，FSRS 把下次复习安排在与稳定性相等的间隔之后，所以 `stability = interval` 让旧的到期日依然有意义。

转换卡片：一张卡有多份 Spaced Repetition 调度时（每个题目行一份，或笔记级的 `sr-due`/`sr-interval`/`sr-ease` 字段），导入程序取最早的到期日、最小的间隔和最小的 ease，为这张卡写一个事件。

读取 Spaced Repetition 数据（两个实现都适用；共用用例在 `vectors.json` 的 `sr_import` 和 `sr_plan` 下）：

- 一条注释 `<!--SR:...-->` 含一份或多份调度，每份以 `!` 开头：SM-2 为 `!YYYY-MM-DD,interval,ease`，FSRS 为 `!fsrs,due,interval,stability,difficulty,...`（共 10 个字段；interval、stability 和 difficulty 是纯 ASCII 十进制数）。
- front matter：`sr-due`、`sr-interval`、`sr-ease` 中只要设了任何一个，三个都必须设。`sr-due` 是 `YYYY-MM-DD` 日期（也接受 YAML 日期值）；`sr-interval` 和 `sr-ease` 是整数，可以写成数字，也可以写成由 ASCII 数字组成的字符串。
- 数字只能是 ASCII 数字。其他任何情况（全角数字、间隔写成 `5.7`、未知的段、不含调度的注释、被事件规则拒绝的初始状态，例如 ease 为 0 或时间戳错误）都会使这张卡的 SR 数据无法读取。

执行导入：

1. 没有 SR 数据的卡片被忽略。
2. 已有事件的卡片被跳过，所以再次执行导入不会添加任何东西。
3. SR 数据无法读取的卡片会被报告（卡片 id、路径、原因），不导入；其他卡片照常导入。修好被报告的卡片后再执行一次导入，就只导入这些卡片。
4. 一次运行的全部导入事件用一次追加写入；卡片文件从不改动。`sb review import-sr --dry-run` 只显示计划，不写入。

如果知识库已有 Spaced Repetition 的复习进度，先用 CLI：运行 `sb review import-sr --dry-run`，检查 `invalid` 列表，再运行 `sb review import-sr`。CLI 在安装插件之前就能用，并输出完整报告。插件命令为已经在用插件的用户做同样的事；它显示计数，并把无法读取的卡片记录到开发者控制台。

## 3. 调度器

### 3.1 学习日

调度按**学习日**计天。一个学习日从本地时间 04:00 开始。事件的学习日由它自己的 `ts` 算出（取事件所带偏移下的本地挂钟时间，减去 4 小时，再取日历日期）。日序号是从 1970-01-01 到这个日期的天数。

两次复习之间的经过天数 = 两者学习日序号之差。23:30 的一次复习和次日早上 08:00 的另一次复习相隔一天；同一夜里 01:00 和 03:00 的两次复习相隔零天。

一张卡的调度日从不倒退。来自不同偏移设备的事件，可能时刻更晚、学习日却更早；这样的事件按这张卡最后一个调度日来调度（经过天数 = 0）。

### 3.2 FSRS

记忆模型：FSRS-6，参数如下（两个库公开的默认值）：

```text
w = 0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722,
    0.1666, 0.796, 1.4835, 0.0614, 0.2629, 1.6483, 0.6014, 1.8729, 0.5425,
    0.0912, 0.0658, 0.1542
desired retention = 0.9
learning steps    = 1 min, 10 min
relearning steps  = 10 min
maximum interval  = 36500 days
fuzz              = off
short-term (same-day) stability = on
```

调度策略：ts-fsrs 5.4.2 的基础调度器（用上面选项调用 `fsrs().next()`），在**按日对齐的时间**上运行：

1. 每个复习时间 `t` 映射为 `t' = (scheduler day of t) × 86400 s`（该序号对应的 UTC 午夜；第 3.1 节）。于是两个库都把经过天数算成学习日之差。
2. 调度器在 `t'` 上运行。它给出的到期时间 `due'` 按 `due = t + (due' − t')` 映射回来。以分钟计的间隔步长保持原有长度；以天计的间隔落在一天中相同的本地时刻。

不依赖任何库来表述，这个策略是：

| 当前状态 | 评级 | 下一状态 | 下次到期 |
|---|---|---|---|
| new | 1 | learning，第 0 步 | +1 分钟 |
| new | 2 | learning，第 0 步 | +6 分钟（前两个步长的均值，取整） |
| new | 3 | learning，第 1 步 | +10 分钟 |
| new | 4 | review | +interval(S) 天 |
| learning 或 relearning，第 k 步 | 1 | 同一状态，第 0 步 | +第一个步长 |
| learning 或 relearning，第 k 步 | 2 | 同一状态，第 k 步 | +前两个步长的均值（只有一个步长时：1.5 × 第一个步长），取整到分钟 |
| learning 或 relearning，第 k 步 | 3 | 第 k+1 步（若存在），否则 review | +该步长，或 +interval(S) 天 |
| learning 或 relearning，第 k 步 | 4 | review | +interval(S) 天 |
| review | 1 | relearning，第 0 步；lapses + 1 | +第一个重学步长 |
| review | 2, 3, 4 | review | hard = min(I2, I3)；good = max(I3, hard + 1)；easy = max(I4, good + 1)（单位：天） |

每次复习时，`S` 和 `D` 按 FSRS-6 的记忆公式更新（首次复习取初始值；经过天数 = 0 时用同日公式；评级 1 用遗忘公式；其他情况用回忆公式）。`interval(S)` = min(max(1, round(S × modifier)), 36500)；`Ir` 是评级 `r` 会产生的稳定性所对应的间隔。`reps` 计复习次数。

TypeScript 实现直接调用 ts-fsrs。Python 实现自己实现这张策略表，并从 py-fsrs 6.3.2 取得 `S`、`D` 和 `interval(S)`（对处于 review 状态的卡调用 `Scheduler.review_card`）。两个库对可提取性（retrievability）的浮点舍入不同（ts-fsrs 舍入到 8 位小数），所以稳定性和难度在约 1e-8 的相对误差内一致，而不是逐位相同。测试向量用 1e-6 的相对容差比较它们，到期时间则精确比较。

## 4. 重放

1. 读取所有日志文件；逐行解析；跳过无效行并报告。
2. 去重：`device`、`card` 相同且 `ts` 表示的时刻相同的事件算同一个事件；保留最先读到的那个。文件按路径顺序读取，行按文件内顺序读取。
3. 按（`ts` 时刻、`device`、`card`、文件路径、行号）排序。
4. 按这个顺序把事件应用到各张卡上（第 3 节）。导入事件替换卡片状态（第 2.2 节）。

对至少有一个事件的每张卡片，结果如下：

| 字段 | 含义 |
|---|---|
| `state` | `learning`、`review` 或 `relearning` |
| `stability`、`difficulty` | FSRS 记忆状态 |
| `due` | 下次到期时间（UTC 时刻） |
| `last_review` | 上次复习的时间（导入后则为导入设定的上次复习时间） |
| `reps`、`lapses` | 复习次数；在 review 状态下评级为 1 的次数 |
| `first_review` | 第一次非导入复习的时间，或无 |
| `last_day` | 最后一次非导入复习的学习日序号，或无 |

### 4.1 状态缓存

读取方可以保存一个检查点（checkpoint），这样不必每次启动都重放全部历史。检查点只存在本设备上，只属于一个实现：插件把它存在 IndexedDB 里，Python 调用方传入一个 `cache_dir`（例如放在 `~/.cache/secondbrain/` 下）。检查点从不存进知识库，也从不被别的设备读取。删掉它不会丢失任何东西。

检查点保存：重放结果（每张卡的状态、有复习的日子、每张卡首次复习的日子、发现的问题），最新一个已应用的事件，以及每个日志文件读到了多少：长度、行数和已读前缀的哈希。

带检查点加载：

1. 对每个日志文件，已读的前缀必须没变（哈希相同），并且文件必须没有变短；检查点里的每个文件都必须仍然存在。
2. 只解析这个前缀之后的行。如果某个文件不以换行符结尾（有一行还在写），这次加载就做完整重放，并且不保存检查点。
3. 如果新事件全都晚于最新一个已应用的事件，就按重放顺序应用它们。出现一个更早的事件（某台设备同步晚了）就意味着完整重放。
4. 其他任何意外情况（格式版本不同、规则版本不同、FSRS 参数或库版本不同、检查点损坏）也意味着完整重放。规则版本（两个实现里的 `RULES_VERSION`）标明解析和重放规则的修订版本，所以改动了这些规则的升级，从不会接着旧规则下写的检查点继续。

结果必须与完整重放相同；两套测试都检查这一点。

## 5. 状态

对在时刻 `now` 查看的人，"今天"是 `now` 在查看者本地偏移下的学习日。知识库索引里的每张卡片，恰好处于下列学习状态中的一种：

| 学习状态 | 定义 |
|---|---|
| unlearned（未学） | 这张卡没有事件 |
| learning（学习中） | 状态为 `learning` 或 `relearning` |
| review（复习期） | 状态为 `review`，且稳定性 < 21 天 |
| mastered（已掌握） | 状态为 `review`，且稳定性 ≥ 21 天 |

另有以下标记，与学习状态相互独立：

| 标记 | 定义 |
|---|---|
| due（到期） | 有事件，并且 `due` 的学习日（按查看者的偏移）≤ 今天 |
| reviewed today（今日已复习） | 今天的学习日里至少有一个非导入事件 |
| due, not reviewed today（今日未复习） | 已到期，且今天未复习 |

"Learned"（已学）= learning + review + mastered。汇总给出全库和每个 `domain` 的这些计数，另外还有：今天开始学的新卡数（今天有首次非导入复习），今天的复习事件数，学习连续天数（至少有一次复习的连续学习日，截止到今天；如果今天还没有复习，就截止到昨天），以及有事件但不在索引里的卡片数（已删除的卡片，或改了 id 的卡片）。

## 6. 队列

每日队列先列到期卡，再列新卡。

- **到期**：已到期的卡片（第 5 节），先按 `due` 排序，再按 `id` 排序。学习领域设置不筛选到期卡。
- **新卡**：符合条件的未学卡片，按先修顺序排列，最多 `new_per_day` −（今天已开始学的新卡数）张。默认 `new_per_day` = 10。
  - 符合条件：`kind` 不是 `record`；`status` 不是 `needs-review`；`domain` 在选定的学习领域内（没有选定时为全部领域）。
  - 先修顺序：对符合条件的未学卡片做的拓扑排序。边（“A 在 B 之前”）：

    | 卡片 C 上的关系 | 边 |
    |---|---|
    | `depends_on: T`、`follows: T` | T 在 C 之前 |
    | `required_by: T`、`precedes: T` | C 在 T 之前 |

    其他关系类型（包括 `part_of` 和 `has_part`）不决定卡片顺序。在一个有 8,615 张卡片的真实知识库上，无论按哪个方向加入 `part_of`/`has_part` 边，断环次数都从 323 增加到 624–720，所以这些边与先修边冲突。指向候选集合之外卡片的边，以及指向自身的边，都被忽略。
  - 并列：在已就绪的卡片中，(`rank`, `id`) 最小的排在最前。`rank` = 领域位置 × 1,000,000 + 支柱序号 × 1,000 + 主题位置。领域位置是该领域在学习领域中的下标；没有选定学习领域时，是在按码位排序的全部领域中的下标。支柱序号是分类体系 YAML 里该支柱的 `order`（缺失时为它在列表中的位置 + 1）；主题位置是卡片的 `topic` 在该支柱 `topics` 中的下标。未知的支柱和主题取 999。Python：`build_taxonomy` 和 `rank_cards`（向 `queue_report` 传 `taxonomy=`）；插件：`rankCards`。`tests/fixtures/review/rank.json` 存放共用的期望结果。
  - 环：还有剩余卡片、但没有一张就绪时，取剩余卡片中 (`rank`, `id`) 最小的一张作为下一张，并报告为一次断环。不丢弃任何卡片。`sb review queue` 用 `cycle_breaks` 报告这类卡片的张数。
  - 学习顺序的环，是两张或更多卡片组成的一组，它们之间的前置关系（`depends_on`、`follows`、`required_by`、`precedes`）绕回自身，即上表这些边构成的强连通组；按整个知识库统计，不只统计队列的候选卡。`sb refresh` 和 `sb status` 每组报告一次 `learning_order_cycle`，带 `card_ids` 和 `mocs`。`sb review queue` 用 `prerequisite_cycles: {groups, cards}` 报告同样的组。队列里这两个数统计的东西不同：`prerequisite_cycles` 统计环里的每张卡，不论学过与否；`cycle_breaks` 统计队列不按顺序取出的未学卡，其中也可能有只是依赖某个环、本身不在环里的卡。

## 7. 配置与路径

| 设置 | 默认值 | 说明 |
|---|---|---|
| 日志目录 | `<data>/review` | `<data>` 是目录布局里的数据目录（`_data`） |
| 写入用的日志格式 | `jsonl` | 同步服务跳过其他文件类型时用 `md` |
| 每天新卡数 | 10 | |
| 学习领域 | 无（全部领域） | 只筛选新卡 |
| 已掌握阈值 | 21 天 | 稳定性 |
| 一天的开始 | 04:00 | 属于格式的一部分；不是用户设置，因为它会改变经过天数 |

## 8. 兼容规则

- 会让旧日志重放结果改变的改动（参数、策略、一天的开始时刻、哈希方式），就是一个新的格式版本。读取方继续支持版本 1。
- 新增一个可选字段不算版本变更。
- 任何改变行的解析或重放方式的改动，即使只是修复对无效输入的处理，都要同时递增两个实现里的 `RULES_VERSION` 和向量文件里的 `rules_version`（测试会检查三者一致）。这让状态缓存保持可信；它不改变日志格式。
- 向量文件是 Python 实现和 TypeScript 实现之间的契约。改动任何一个实现，都必须让两套测试在同一个文件上全部通过。

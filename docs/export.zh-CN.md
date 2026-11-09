# 导出：把一张地图的卡片写成文章

[English](export.md)

`sb export` 把一张地图（MOC）的卡片写成一篇文章。程序按分类体系定提纲。写作模型按写作单位，一个单位一个单位地根据卡片写。另一次运行里的复核者逐段对照所引的卡片核对，程序核对引用、数字和公式。返修轮用完后仍不过的段落，会在文章里标出，并列进待你处理的事。

文章是卡片的一种视图：卡片变了就重新导出，不要手改。它从不写进知识卡目录。

## 命令

```sh
sb export --domain <slug>                     # 把这个领域的地图写成文章
sb export --moc "<map title>"                 # 用这个标题的地图
sb export --domain <slug> --ids <id> <id>     # 只用地图里的这几张卡
sb export --domain <slug> --html              # 另出一份公式已排版的 HTML 页面
sb export --domain <slug> --mode parallel     # 所有写作单位同时写
sb export --domain <slug> --out <folder>      # 文件写到这个目录
sb export --domain <slug> --prepare-only      # 只冻结提纲，不调用模型
sb export --domain <slug> --executor host     # 每一步交给一个子代理
sb export --job <job_id> --executor host      # 子代理提交之后接着做
sb export --domain <slug> --no-formula-check  # 不做 MathJax 检查，照样导出
```

`--max-rounds N` 覆盖本次运行的 `[review] max_repair_rounds`。`--parallel N`（1 到 8，默认 4）设定 API 模式下同时进行的模型调用数。`--tag "<text>"` 记进任务输入；换一个标签就是一个新任务。同样的卡片加同样的选项得到同一个任务，所以再运行一次同样的命令，就是接着做这个任务。

## 各个步骤

| 步骤 | 谁做 | 做什么 |
|---|---|---|
| 提纲 | 程序 | 分类体系给出各节、各小节和卡片的顺序（[见下](#提纲)）。提纲冻结为任务的输入。 |
| 写 | 写作者 | 每个写作单位（8 到 15 张卡）写成若干段落。每段用 `[[卡片标题]]` 标出用到的卡。 |
| 检查 | 程序 | 程序逐段检查（[见下](#程序检查)）。 |
| 复核 | 复核者 | 另一次运行里的复核者逐段对照所引的卡片判断：`accept` 或 `revise`，附理由。 |
| 返修 | 写作者 | 有 `revise` 或程序检查发现问题的单位，连同这些问题退回写作者，最多 `[review] max_repair_rounds` 轮。 |
| 理顺 | 写作者 | 一次调用写引言和节与节之间的过渡，并决定删掉哪些重复的段落。 |
| 复核衔接 | 复核者 | 同一个复核者判断引言和每段过渡；`revise` 按同样的规则退回。 |
| 最后检查 | 程序 | 程序再检查一遍整篇文章，然后写出文件（[输出](#输出)）。 |

### 提纲

- 地图的叙事用作引言的材料。
- 支柱按各自的 `order` 成节。支柱的 `why` 是这一节第一段的要点。
- 主题按分类体系里的顺序成小节。卡片少于 3 张的主题不单列小标题：它的卡放在这个支柱一节的开头。
- 同一主题里，卡片按地图页的先修顺序排。
- 每个支柱切成写作单位，每个单位 8 到 15 张卡，尽量均匀；3 张卡以内有主题分界时，就在分界处切。每部分都有目标长度：中文每张卡约 250 字，英文每张卡约 120 词。
- 地图里没有合法支柱和主题的卡，放进最后一节“未归类的卡”，所以范围里的卡一张都不会丢。

写作者看到每张卡的整张正文，去掉链接、问答和卡内图；本单位各卡之间的关系作为数据给出。

### 逐个写还是同时写

`[export] mode` 设定默认写法，`--mode` 可以在单次运行里覆盖它：

- `sequential`（默认）：一个单位接一个单位地写。每个写作者还能看到上一单位的最后一段和已写各单位的标题。
- `parallel`：所有单位同时写，看不到上一单位的末段。

为什么默认逐个写：一次 6 节的实验里，Flash 写作者（GLM-5.3-Flash）逐个写时达到了 Opus 写作者的水平。成对盲比里，6 节平 5 节、胜 1 节。它每 10,000 字有 0.54 处卡片不支持或与卡片矛盾的说法，Opus 是 0.29 处。所有单位同时写快约 3 倍，读起来也不差，但同一个写作者这时每 10,000 字有 1.63 处这样的说法。DeepSeek Flash 写作者两种写法都比不过 Opus。判官都是 Opus 模型，会偏向 Opus 的文字。

用哪个模型写、哪个模型复核，你可以自己选（[配置](#配置)）。

### 程序检查

每一段都对照它所引的卡片检查：

- 至少用 `[[...]]` 引一张卡，每个链接都指向存在的卡；
- 段里的每个数字都出现在所引的卡里；
- 每个公式都出现在所引的卡里（忽略空白），并能被 MathJax 3 解析（[公式检查](#公式检查)）；
- 公式符合 Pandoc 对 `$` 的规则：开头的 `$` 后面、结尾的 `$` 前面不能有空格，结尾的 `$` 后面不能紧跟数字；`$$` 块的首尾各占一行；表格行里的公式不能含 `|`；没有不配对的 `$`；
- 段里没有标题（标题由程序生成）；
- 公式之外没有字面的 `\n`（[怎样读答案](#怎样读答案)）。

每发现一处问题，就像 `revise` 一样，把这个单位退回返修。引言和过渡不引卡，所以对照文章的全部卡片检查。理顺之后，整篇文章再检查一遍。每张卡至少应被引用一次；没被任何段落引用的卡列在文章末尾。

### 复核与返修

复核者拿到各段文字、它们所引的卡和本单位的卡。对每一段，它给出 `accept` 或 `revise` 和具体理由，也可以写备注。备注的五种类别与入库相同（[复核规则](ingest.zh-CN.md#复核规则)），只有复核卡片才用的 `repeats` 不在其中：复核者接受一段、却写了阻塞类备注（`fact`、`unreadable`、`quote`、`not_a_unit`）时，程序把这次接受改成退回，退回理由就是那条备注。报告在每个单位的历史里记下每一轮被改成退回的段落。

### 理顺

一次调用看提纲、地图的叙事，以及每个单位的第一段和最后一段。它根据地图的叙事写引言，并为后面的每一节写一段过渡。它不改写正文段落。程序先找出彼此重复的成对段落（按共有的字符片段），再由这次调用决定删哪一段。会让某张卡不再被引用的删除，程序拒绝。

### 一直没通过的段落

返修轮用完后，或某一步的答案一再没过程序校验、超过 `[review] max_invalid_retries`（计数存在任务里，导出再跑时接着计），这个单位保留下来，标为 `escalated`。复核一直没给出合格答案时，它要判的每一段都标为未复核；写作者一直没给出合格答案时，这个单位本来的位置（它的每个小标题下）放一个标记：这一段没写成、原因（答案几次没过检查、最后一次的校验消息）、这一段本来要写哪几张卡（卡的链接）；这个单位也列在你的清单上。它不会被删掉，也不会当作已通过照常印出。复核或程序检查仍没通过的每一段，前面加一行，写明理由的第一句：

```text
[This paragraph did not pass review: <reason>]
〔此段复核未通过：<reason>〕
[A part is missing here; it was not written: <reason>. It was to cover these cards: [[Card A]], [[Card B]]]
〔此处缺一段，没有写成：<reason>。这一段本来要写这些卡：[[卡 A]]、[[卡 B]]〕
```

英文文章用第一、三种写法，中文文章用第二、四种。引言和过渡也一样。这样的每一段也列进待你处理的事：`sb status --owner-items` 显示它，`owner_reason: pending`、`kind: export_paragraph`，附文章、文件、任务和理由，取每篇文章最新一次完成的导出。报告在 `flagged` 下列出这些段落，front matter 里记有未通过的单位数和标出的段落数。

为什么：不忠实的段落如果照常印出，会误导读者；整段删掉，文章会断开，读者也不知道缺了什么。

## 输出

文件写到知识库里的 `[layout] articles`（默认 `60_Articles`），或 `--out` 指定的目录：

- `<地图标题>_<YYYYMMDD>.md`：front matter（标题、生成时间、写作和复核的模型、写法、卡片数、未用的卡数、未通过的单位数、标出的段落数、任务，以及“这是生成的视图”的说明），一行告诉 Typora 用户怎样打开行内公式，正文，末尾是“未写进正文的卡”；
- `<地图标题>_<YYYYMMDD>.report.json`：程序的各项指标（被引的卡、未用的卡、不在所引卡里的数字、公式数和能解析的数目、格式违规、不存在的链接、每一处发现），每个单位的状态、返修轮数和历史，标出的段落，理顺这一步，以及各次模型运行和用掉的 token；
- 加 `--html` 时，`<地图标题>_<YYYYMMDD>.html`：用 Python-Markdown 和 `pymdownx.arithmatex` 生成的单个页面，公式由 MathJax 3 排版，所以不依赖编辑器的设置。页面打开时从 CDN 加载 MathJax。需要 `export` 可选依赖：`pip install -e ".[export]"`。

公式照卡里的写法输出：行内用 `$...$`，`$$` 块首尾各占一行。程序从不改写公式。只有这两种写法在 Typora、Obsidian 和 GitHub 上都能显示；Typora 要打开“内联公式”（Inline Math）才显示行内公式，所以文件第一行写明怎样打开。

## 公式检查

文章的卡片里有公式时，程序先确认 MathJax 检查能用：它必须能解析一个正确的公式，并拒绝一个错的公式。这一步在调用任何模型之前做。检查用不了时，`sb export` 以退出码 2 停下，说明原因和安装办法，不花一次模型调用。检查需要 `PATH` 上有 Node.js，以及 `mathjax-full` 包：

```sh
npm install --prefix ~/.cache/sb-mathjax mathjax-full@3
export SB_MATHJAX_NODE_PATH=~/.cache/sb-mathjax/node_modules
```

检查脚本 `mathjax_check.js` 随包安装（在 `secondbrain/export/checks.py` 旁边），所以非可编辑的安装（从 git 或从 wheel 装）也有它；`mathjax-full` 仍要自己装，见上。`SB_MATHJAX_CHECK` 可以指定另一个检查脚本，这时用它。运行中途检查失败时，导出以同样的错误停下。

不做这项检查也要导出时，运行 `sb export --no-formula-check`。这时文章在标题下写明公式没有经过解析检查，报告里有 `formula_check_skipped`。卡片里没有公式时，不需要这项检查。

## 怎样读答案

写作者的段落里有 TeX。模型把 `\mid`、`\ge` 抄进 JSON 字符串却不转义时，写出的就不是合法的 JSON。所以 `sb export`（只有它）在严格解析失败时再读一次答案，把每个 TeX 命令的反斜杠加倍。严格解析成功的答案从不重读。这种读法有代价：后面跟着字母的真换行 `\n` 会被读成 TeX。所以程序把公式之外字面的 `\n` 算作一处问题，这个单位退回返修。入库的答案总是严格解析（[ingest.zh-CN.md](ingest.zh-CN.md#形状不对的答案)）。

## 配置

```toml
[export]
mode = "sequential"          # sequential | parallel

[export.writer]              # 没设时用 [executor.producer]
kind = "openai"
base_url = "https://api.example.com/v1"
model = "writer-model"
api_key_env = "SB_WRITER_API_KEY"

[export.reviewer]            # 没设时用 [executor.reviewer]；与写作者不同的模型家族
kind = "anthropic"
base_url = "https://api.anthropic.com"
model = "reviewer-model"
api_key_env = "SB_REVIEWER_API_KEY"
```

`[export.writer]` 和 `[export.reviewer]` 的配置项与 `[executor.producer]` 相同（[configuration.zh-CN.md](configuration.zh-CN.md#executorexecutorproducerexecutorreviewer)）。没设的那一个，分别用 `[executor.producer]` 或 `[executor.reviewer]`。API 模式下，写作者和复核者必须来自不同模型家族，与入库相同。`[layout] articles` 设定默认的输出目录。复核规则取自 `[review]`：`max_repair_rounds`、`max_invalid_retries` 和备注类别与入库的用法相同。

## 宿主模式

`sb export --domain <slug> --executor host` 冻结提纲，像 `sb ingest` 一样返回 `handoffs`。把每个交接交给一个新的子代理：写、返修和理顺用 `role: producer`，各次复核用 `role: reviewer`（[agents.zh-CN.md](agents.zh-CN.md)）。所有能做的步骤一次发出；逐个写时，一个单位要等上一个单位有了初稿才发出。子代理都提交之后，运行 `sb export --job <job_id> --executor host` 取下一批交接，直到结果里出现 `status: complete` 和 `files`。

接着做时用 `sb export --job`，不要用 `sb job advance`：`sb job advance` 能把各步做完，但不写文章文件。对已完成的任务运行 `sb export --job <job_id>`，就会写出文件。

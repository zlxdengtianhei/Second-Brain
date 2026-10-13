# SecondBrain Review（Obsidian 插件）

[English](README.md)

每天复习 Second Brain 知识库的入口。它显示今天要复习什么，做翻卡复习和问答复习，并用大模型讲解卡片。
每次复习都追加到库里的事件日志；调度（FSRS）由这份日志重放得出，所以卡片文件永远不被修改。
Python 包的 `sb review` 命令（`status`、`queue`、`log`、`import-sr`）读写同一份日志，所以终端里的
agent 用 `sb review log` 记下的复习也算数。

界面有英文和中文两种。默认跟随库的语言：英文库显示英文界面，中文库显示中文界面。插件读取的卡片小节标题也跟随库的语言（见[语言](#语言)）。

## 功能

| 功能 | 在哪里 |
|---|---|
| 今日面板：到期卡、新卡（已开始 / 每日上限）、今日已复习、连续天数、各领域进度 | 侧边视图（功能区图标或命令），或任一笔记里的代码块 |
| 翻卡复习：看问题，显示答案，评重来 / 困难 / 记得 / 简单（按键 1–4，空格显示答案），每个按钮上写着下一次间隔 | 命令或面板按钮 |
| 问答复习：用自己的话回答每个问题；模型对照参考答案和本卡命题评分；你确认或改评级；整张卡取各题最低的评级 | 命令或面板按钮 |
| 讲解：模型讲解当前打开的卡，带上相关卡的本质、它在知识地图里的支柱和主题、你的复习记录；可以追问 | 命令、面板按钮，或复习中的按钮 |
| 提醒：状态栏计数，每天第一次打开时一条通知，以及一个每日日历文件（`.ics`），其中的链接打开面板 | 设置 → 提醒 |
| 一次性导入 Spaced Repetition 插件的进度（`<!--SR:...-->`、`sr-due`） | 命令或设置 |

把面板嵌进一篇笔记（例如 `HOME.md`）：

````markdown
```secondbrain-review
```
````

从任何地方打开（日历应用、其他笔记）：`obsidian://secondbrain-review`。
直接开始一轮复习：`obsidian://secondbrain-review?mode=flip` 或
`obsidian://secondbrain-review?mode=qa`。有多个库时加上库名：
`obsidian://secondbrain-review?vault=My%20Vault&mode=qa`。

## 安装

1. 构建：在本目录运行 `npm ci && npm run build`。
2. 把 `main.js`、`manifest.json` 和 `styles.css` 复制到
   `<vault>/.obsidian/plugins/secondbrain-review/`。
3. 在 设置 → 第三方插件 里启用 **SecondBrain Review**。
4. 要用评分和讲解，安装并启用
   [AI Providers](https://github.com/pfrankov/obsidian-ai-providers) 插件，在其中添加一个
   provider（类型、密钥、模型、测试），再到本插件的设置里选中它。
   没有它时，翻卡复习和面板照常可用；问答复习由你自己评级。

## 语言

库的语言是 `sb init` 或 `sb language --set` 记在 `<data>/secondbrain/catalog.json` 里的语言。
在有这条记录之前建的库，从生成的地图页检测（taxonomy 目录所在目录里的 `.md` 文件，例如 `20_MOCs`），
做法与 `sb` 命令相同。插件在 Obsidian 启动时、catalog 变化时、以及你点面板上的"刷新"时读取库的语言。

| 内容 | 跟随 | 英文库 | 中文库 |
|---|---|---|---|
| 界面（视图、按钮、通知、设置、状态栏、日历事件文字） | **界面语言** 设置。`自动`（默认）用库的语言；库没有语言时用 Obsidian 的语言 | 英文 | 中文 |
| 内置的评分与讲解模板，以及插件加进模板的上下文行 | 界面语言 | `examples/prompts/*.en.md` | `examples/prompts/*.md` |
| 设置留空时插件读取的小节标题 | 库的语言；库没有语言时用界面语言 | `Review cards`、`Essence`、`Propositions` | `复习卡`、`本质`、`命题` |
| 设置留空时的日历文件 | 与小节标题相同 | `90_Meta/review-reminder.ics` | `90_Meta/复习提醒.ics` |

你在设置里填写的标题或日历路径始终优先。小节标题就是 `sb` 写进卡片的标题（`src/secondbrain/cards.py`）；
有测试核对两张表一致。命令名在 Obsidian 启动时取界面语言，所以改了设置之后，要重启 Obsidian 才会改命令名。

界面文字在 `src/i18n.ts`。要加一种语言，加一张键相同的表。

## 设置

| 设置 | 默认值 |
|---|---|
| 界面语言 | `自动`（库的语言，否则 Obsidian 的语言） |
| 知识卡片目录、体系文件目录（支柱 YAML）、数据目录 | `10_Knowledge`、`20_MOCs/_pillars`、`_data` |
| 小节标题：`question::answer` 行、本质、命题 | 留空：库语言的标题（见[语言](#语言)） |
| 每日新卡数；在学域（新卡只从这些域按先修顺序取） | 10；全部域 |
| 模型（来自 AI Providers）；评分与讲解模板 | 无；`90_Meta/prompts/grade.md`、`90_Meta/prompts/explain.md`（文件不存在时用内置模板；见 `examples/prompts/`） |
| 讲解上限：相关卡、本质长度、同主题卡、卡片长度、回答长度 | 8、120、10、6000、600 |
| 每日通知；日历时间与文件 | 开；`09:00`，留空（库语言的文件名） |
| 日志格式 | `jsonl`（同步服务只同步笔记时选 `md`） |

## 数据

- 复习日志：`<data>/review/events-<device>.jsonl`（或 `.md`），每台设备一个文件，只追加。
  格式、调度器和重放规则见 [`docs/review-registry.md`](../docs/review-registry.md)。
- 设备 id（`obs-xxxxxx`）存在本设备的本地存储里，不在同步的设置里。
- 重放检查点存在本设备的 IndexedDB 里，所以启动时只读上次启动以来新增的行。
  清掉它是安全的；下次启动会重放全部日志。
- Obsidian Sync 不同步 `.jsonl` 文件，除非打开了"同步其他类型文件"。
- 问答复习里你输入的回答存进日志（每题最多 2,000 个字符）。
  提示词只发给你在 AI Providers 里选的 provider。

## 手工检查清单

单元测试不能在 Obsidian 里加载插件。在库的副本上按下面的步骤做，桌面端一次、手机端一次。
打开开发者控制台（桌面：Ctrl/Cmd+Shift+I），记下任何错误。

步骤里写的是中文界面的标签；英文界面的标签见 `src/i18n.ts`（例如 `开始复习` 是 `Start review`，`记得` 是 `Good`）。

1. **安装。** 复制三个文件，启用插件。预期：控制台没有错误；库加载完后状态栏出现 `复习 N`。
   记下用了几秒。中文库里界面和小节标题是中文；英文库（例如 `examples/vault`）里是英文。
2. **面板。** 点功能区图标。预期：侧边视图显示到期、今日未复习、新卡（已开始 / 上限）、
   今日已复习、连续天数，每个领域一行。新库里所有卡都未学，到期为 0。
3. **笔记代码块。** 在 `HOME.md` 里加 `secondbrain-review` 代码块。预期：阅读视图和实时预览里
   数字相同，领域表可以滚动。
4. **翻卡复习。** 点"开始复习"。预期：问题能渲染（公式、链接）；空格显示答案；每个评级按钮显示间隔；
   按键 1–4 评级。每评一次，`_data/review/events-obs-xxxxxx.jsonl` 多一行，带 `"mode":"flip"` 和该评级。
   评"重来"的卡在本轮末尾再出现。
5. **学习计划。** 在设置里打开一两个在学域，改每日新卡数。预期：新卡只来自这些域，按它们的顺序。
6. **问答复习。** 安装 AI Providers，添加一个 provider，在本插件设置里选中它，点"问答复习"。
   一题答好，一题答差。预期：建议的评级高亮，你可以另选；最后一题之后写入一行 `"mode":"qa"`，
   每题一项，整卡评级为最低的那个。再选一个不存在的模型（或断网）：预期显示"未评分"和原因，
   你仍然可以评级。
7. **讲解。** 打开一张卡，运行"讲解当前卡"。预期：回答流式出现在侧边视图；上方一行显示上下文大小和
   相关卡数；追问得到的回答用到了前面的回答；"停止"能停下。
8. **提醒。** 第二天打开 Obsidian。预期：一条带计数的通知，当天不再出现第二条。在设置里生成日历文件，
   导入手机和电脑的日历。预期：在设定时间提醒；点链接打开 Obsidian 的面板。
9. **URI。** 从浏览器或另一篇笔记打开 `obsidian://secondbrain-review?mode=qa`。预期：Obsidian 打开面板
   并开始一轮问答复习。
10. **两台设备。** 在每台设备上各复习几张卡，等同步完成，重新打开。预期：两边的到期数和卡片状态相同。
    用 Obsidian Sync 时，先打开"同步其他类型文件"，或把日志格式改成 `md`。
11. **语言。** 在设置里把界面语言改成另一种语言。预期：设置页、面板和状态栏立即改变；插件读取的小节标题不变；
    重启后命令名也改变。再改回 `自动`。
12. **Spaced Repetition 导入**（只在卡片里有 `<!--SR:...-->` 注释或 `sr-*` front matter 时做）。
    已有的库优先在装插件之前用命令行：先 `sb review import-sr --dry-run`，再 `sb review import-sr`。
    在插件里运行导入命令。预期：确认框里有卡数；SR 数据读不了的卡在通知里点名、在控制台列出，不导入；
    导入后，导入的卡进入复习期并保留原来的到期日；卡片文件不变；再运行一次什么也不导入。

## 开发

```sh
npm ci
npm test        # vitest：与 Python 共享的向量、数据层与缓存、LLM 解析、两种语言、复习界面流程（happy-dom）
npm run build   # 类型检查 + 生产构建
```

`src/registry/` 是 `src/secondbrain/review_registry.py` 的 TypeScript 孪生实现；两边都必须通过
`tests/fixtures/review/vectors.json`。`src/registry/`、`src/llm/` 和 `src/session.ts` 下的模块不导入 Obsidian，
有单元测试；`src/ui/`、`src/vault/` 和 `src/main.ts` 把它们接到 Obsidian 上。

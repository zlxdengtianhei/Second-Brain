# 示例

[English](README.md)

这里有两个小而完整的 Second Brain 知识库，主题相同（学习、数据推理、计算基础），一个中文、一个英文；还有复习插件的提示词模板。在示例库上试命令之前，先复制一份：`sb` 会写入它指向的知识库。

## `vault-zh/`：中文示例库

[中文说明](../README.zh-CN.md)的快速上手用它。这个库记录的语言是 `zh`，所以卡片小节名是 `本质`、`命题` 和 `复习卡`，地图页和 `HOME.md` 也是中文。

| 部分 | 内容 |
|---|---|
| `00_Inbox/` | 两份还没入库的短笔记 |
| `10_Knowledge/` | 52 张卡片，分属三个领域：`learning`（13）、`statistics`（17）、`computing`（22）；其中 8 张带图 |
| `20_MOCs/` | 每个领域一张地图页、它的支柱页，以及 `_pillars/` 里的分类体系文件 |
| `40_Sources/` | 做卡片用的 7 份来源笔记 |
| `_data/` | 领域目录、卡片登记表和收件箱登记表，以及记了 5 次复习的复习日志 |
| `HOME.md` | 生成的首页 |

怎么做出来的：

1. 来源笔记是英文示例库笔记的中文版，专为这个示例而写。领域目录和三个分类体系文件用中文手写；`sb init --catalog` 放入领域目录，并补上登记表和各个目录。
2. 在 Claude Code 里用 Agent Skill 入库 7 份笔记，`[cards] language = "zh"`：每个交接一个新的子代理，生产者用一个 Claude 模型，复核者用另一个。一共 31 次子代理运行；每个任务都完整写入，没有待定卡。
3. 有一张卡跨领域关联：「布隆过滤器作为慢速查找的前置检查」（computing）与「基础率忽视」（statistics）是 `similar_to` 关系，另一张卡上有反向关系。
4. 用 `sb review log --device example` 记了 5 次复习。

## `vault/`：英文示例库

[英文说明](../README.md#quick-start-with-the-example-vault)的快速上手用它。

| 部分 | 内容 |
|---|---|
| `00_Inbox/` | 两份还没入库的短笔记 |
| `10_Knowledge/` | 42 张卡片，分属三个领域：`learning`（13）、`statistics`（12）、`computing`（17）；其中 8 张带图 |
| `20_MOCs/` | 每个领域一张地图页、它的支柱页，以及 `_pillars/` 里的分类体系文件 |
| `40_Sources/` | 做卡片用的 7 份来源笔记 |
| `_data/` | 领域目录、卡片登记表和收件箱登记表，以及记了 5 次复习的复习日志 |
| `HOME.md` | 生成的首页 |

怎么做出来的：

1. 领域目录和三个分类体系文件是手写的，领域目录用 `sb init --catalog` 放入。当时的 `sb init` 还不会补空的登记表和目录，所以这两样也是手建的；现行步骤见 [docs/new-vault.zh-CN.md](../docs/new-vault.zh-CN.md)。来源笔记是专为这个示例写的原创文字。
2. 在 Claude Code 里用 Agent Skill 入库 7 份笔记：每个交接一个新的子代理，生产者用一个 Claude 模型，复核者用另一个（所以每次复核都记为同一家族）。一共 34 次子代理运行；每个任务都完整写入，没有待定卡。
3. 有一张卡特意跨领域关联：「Bloom filter as a cheap first check and the rare-item false positive problem」（computing）与「Screening test with a rare disease」是 `similar_to` 关系，与「Positive predictive value」（statistics）是 `example_of` 关系。
4. 用 `sb review log --device example` 记了 5 次复习，所以复习统计里除了新卡，还有几张已学的卡。
5. 工具更新后，用 `sb refresh --apply` 和 `sb docs --apply` 重新生成了地图页和 `HOME.md`。

两个库都不带插件设置：SecondBrain Review 插件读取知识库的语言，自动用对应的小节名、界面和内置提示词。

`tests/test_example_vault.py` 和 `tests/test_example_vault_zh.py` 检查：每个库的新副本没有报告问题；地图页和 `HOME.md` 与卡片一致；关系和链接都能解析；文字是该库的语言；这里、两份 README 和 docs/agents 里写的数字与库一致。

## `prompts/`：复习插件的提示词模板

SecondBrain Review 的评分（`grade.md`）和讲解（`explain.md`）提示词是中文的，英文版是 `grade.en.md` 和 `explain.en.md`。插件里内置了同样的文本。想改的话，把文件复制到插件设置里的路径（默认 `90_Meta/prompts/grade.md` 和 `90_Meta/prompts/explain.md`），再在那里改。

## 许可证

这里的全部内容都采用本仓库的 MIT 许可证（[LICENSE](../LICENSE)）。

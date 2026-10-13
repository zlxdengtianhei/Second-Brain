# 卡内图

[English](diagrams.md)

只有当图能表达文字表达不了的东西时，卡片才带图：分支、循环、两条路径汇合、部件之间互相传递数据、一串指针。模型决定画不画，并用数据描述这张图（IR）。程序检查 IR，并写出 Mermaid。模型从不写 Mermaid，所以图里不会出现卡片没说的主张，而且语法总是正确的。

## 入库链中的这一步

这一步在复核之后、写入知识库之前运行，处理一份来源里已接受的卡片。

1. **预筛（pregate）。** 类型为 `contrast`、`fact` 和 `derivation` 的卡片按规则跳过，不调用模型：表格、一句话或对齐的公式更适合表达它们。没有命题的卡片也跳过。
2. **请求。** 已接受的卡片先每十张分成一组，再按类型规则跳过，所以一次请求可能少于十张卡。剩下的卡片连同 [`src/secondbrain/prompts/diagram.md`](../src/secondbrain/prompts/diagram.md) 里的指令，交给生产者模型。每张卡发送的内容是：标题、领域、本质 `E`、编号的命题 `P1`、`P2`……、卡片类型，以及相关卡片的标题。
3. **IR。** 模型为每张卡返回 `{"decision": "skip", "reason": ...}`，或者返回一份画图描述：图的形式、这张图回答的问题，以及节点和连线（或参与者和消息）。每个节点、每条带标签的连线和每条消息都在 `p` 里引用它所依据的卡片部分，例如 `["E", "P2"]`。
4. **检查。** 程序校验 IR（见[检查](#检查)）。
5. **绘制或重写。** 有效的 IR 变成一个 Mermaid 代码块。无效的 IR 带着错误清单退回模型一次。如果重写后仍然无效，这张卡写入时不带图，原因记在任务（job）里。

在宿主模式下，请求是一次步骤为 `diagram` 的交接（第一批十张卡的槽位是 `dg01`，这一批的重写是 `dg01.r1`）。结果是已保存的卡片和已保存的答案的纯函数，所以重复这一步会得到相同的 Mermaid。`[cards] diagrams = false` 关闭这一步。

在入库链里，宽度检查用估算值（V14）。需要浏览器的检查（V9、V15 和实测宽度）只在批处理工具里运行，即 `sb diagrams apply --checker`。

### IR

| 形式 | 适用于 | 节点角色 |
|---|---|---|
| `flow` | 带分支、循环或汇合的过程 | `step`、`decision`、`store`、`input`、`output`、`start`、`end` |
| `structure` | 部件以及部件之间怎样连接：框图、层级、指针链、汇聚到一起的多个原因 | `step`、`store`、`input`、`output`、`actor` |
| `state` | 一个对象，有三个或更多命名状态 | `state`、`start`、`end` |
| `sequence` | 两到三方互相交换消息 | `actor` |

`arrows`（只用于 flow 和 structure）说明箭头的含义：`control`（flow 的默认值）、`data` 或 `relation`（structure 的默认值）。数据图必须从数据进入的地方（角色 `input`）开始，到数据离开的地方（角色 `output`）或最终存储结束。`sb diagrams schema` 输出完整的 JSON Schema。

## 检查

| 编号 | 检查内容 | 在哪里运行 |
|---|---|---|
| V1 | 形状：字段类型正确，形式是已知的，角色是该形式允许的，节点键唯一，连线只连接已知节点，每个节点最多一个自环，一个节点最多属于一个分组，分组只用于 flow 和 structure，消息只用于 sequence，连线只用于其他形式 | 每个 IR |
| V2 | 规模：flow 和 structure 4 到 9 个节点（有两条路径汇合时下限为 3 个），state 3 到 8 个状态，sequence 2 到 3 个参与者（第四个参与者会超出笔记宽度） | 每个 IR |
| V3 | 每个节点、每条带标签的连线和每条消息都引用卡片确有的部分（`E`、`P1`..`Pn`） | 每个 IR |
| V4 | 每个标签至少一半的词能在被引文字里找到（公式名和符号也算；`yes`、`no`、`start`、`end` 等少数词不受此限） | 每个 IR |
| V5 | 图要增加结构：flow 里有分支、汇合或循环；structure 里有汇合、循环，或三条及以上连接（单纯的链必须引用三个或更多部分）；state 里有三条或更多状态转换；sequence 里有三条或更多消息；被引文字说明工作会重复时，flow 要画出回边（如 repeat、loop、until、iterate、重复、循环、直到；否定说法如「不会重复」、指重复数据的如「重复项」、名称如「循环队列」都不算） | 每个 IR |
| V6 | 标签宽度：节点标签最多 20 个单位，判断节点标签 10，连线标签 14，消息 20（码位在 U+2E80 及以上的字符算 1，例如中日韩字符或全角标点；其他字符算 0.5） | 每个 IR |
| V7 | structure、state 和 sequence 的连接都有标签；从判断节点出发的连线写明条件；一个判断节点有两个或更多出口 | 每个 IR |
| V8 | 卡片有两条或更多命题时，它的图要引用两个或更多部分 | 每个 IR |
| V9 | 生成的 Mermaid 能在 Obsidian 内置的 Mermaid 版本里渲染 | 测试里的回归语料；`apply --checker` |
| V10 | 标签是纯文本，不是 TeX | 每个 IR |
| V11 | 数据图从 `input` 节点开始，到 `output` 节点或最终存储结束；在任何 flow 或 structure 里，`input` 只有出边，并指向被引文字提到这个输入的节点，`output` 只有入边 | 每个 IR |
| V12 | 图引用 `E`：它表达本质所陈述的主要关系。卡片的旧图能渲染时（批处理工具），新图还必须引用两条命题；卡片的命题少于两条时，引用全部命题 | 每个 IR |
| V13 | 任何节点扇出的叶节点都不超过三个；也没有三个或更多出口全是叶节点的判断节点（那其实是一个列表） | 每个 IR |
| V14 | flow、structure 和 sequence 的渲染宽度最多 700 px（Obsidian 会把 state 图缩放到适合的宽度）：根据布局估算，或改用 `apply --checker` 实测 | 这几种形式的每个 IR（估算）；`apply --checker`（实测） |
| V15 | 布局之后，连线标签不互相重叠 | `apply --checker` |

警告不会阻止出图。W0 报告被忽略的未知键，或已停用的角色 `data`；W1 报告点名某张相关卡片的节点（卡片之间的链接属于关系，不属于图）；W2 报告以关系类型（例如 `depends_on`）作标签的连线。

这些阈值是默认值，依据作者知识库里图的分布和一次 50 张卡的试点选定。它们没有用更大规模的研究校准过。

## 生成的代码块是什么样子

下面这个代码块来自[示例库](../examples/vault/)里的卡片 "Bloom filter as a cheap first check and the rare-item false positive problem"：

```text
%% diagram v1 props=E,P1,P2,P3,P5 sha=98410946
flowchart TB
  n1(["check item with Bloom filter"])
  n2{"definite no?"}
  n3(["skip the slow lookup"])
  n4(["slow lookup settles the answer"])
  n1 --> n2
  n2 -->|"definite no"| n3
  n2 -->|"probably yes"| n4
```

第一行记录图引用了卡片的哪些部分，以及对本质和全部命题计算的 SHA-256 的前 8 位十六进制数字。如果之后卡片文字改了，哈希就不再匹配，这张图算作过期。生成器还会自己给节点编号（`n1`、`n2`……），转义 Mermaid 会当作语法读取的每个字符，并且流程图一律从上到下排布。生成器不写颜色或样式，因为 Obsidian 在深色主题下会反转图的颜色。

## 知识库里已有的图

`sb diagrams <command>`（等同于 `python -m secondbrain.diagrams <command>`）在已有知识库上运行批处理工具。这些工具面向已经有很多图的知识库，这些图是手画的，或由早先的工具生成。命令不带 `--write` 时，知识库里什么都不会改。每次写入都用审计时记录的文件哈希把关；此后被编辑过的卡片会报告为冲突，不做改动。

| 命令 | 作用 |
|---|---|
| `schema` | 输出 IR 的 JSON Schema |
| `audit-extract VAULT OUT` | 读取每张卡片（只读），写入 `cards.jsonl`；读取每个 Mermaid 代码块，写入 `blocks.jsonl`（`--language` 选择用哪套卡片小节名） |
| `audit-tier OUT` | 读取 `cards.jsonl` 和 `checks.jsonl` 里的 `tools/mermaid_check` 结果，把每张图分进一个档：`keep`、`delete`、`review` 或 `redraw`（`--delete-domain` 把整个领域分进 `delete`）。它写出 `tiers.json`，其中还记下每张旧流程图的标签和边，供 `writeback` 的重画规则使用；旧版本做的审计目录要重新运行一次 `audit-tier` |
| `prune VAULT OUT` | 删除 `delete` 档卡片的图小节（有三条或更多命题的机制卡改为进入复核池），并把保留下来的、从左到右排布且过宽的图改成从上到下 |
| `pool OUT` | 列出要重新判断的卡片：`review` 和 `redraw` 两档，加上 prune 留待复核的 `delete` 档卡片（`--refs FILE` 只取该文件里的卡片引用） |
| `packets CARDS OUT` | 按每批十张（`--batch`）生成给模型的请求，并把预筛掉的卡片写入 `pregated.jsonl` |
| `apply CARDS ANSWERS... --out OUT` | 读取模型答案，为每张卡做决定：`draw`（附 Mermaid）、`rewrite`（写出一个重写任务文件 `rewrite-NNN.json`）或 `skip`，结果写入 `outcomes.jsonl` 和 `mermaid.jsonl`。每份答案命名为 `X.answer.json`，放在它所回答的任务文件 `X.json` 旁边。`--checker tools/mermaid_check/check.mjs` 渲染每张新图，并按 V9、实测宽度（V14）和 V15 把关 |
| `old-audit-packets POOL OUT` | 生成请求：对每张会保留旧图的卡片，询问这张旧图是在复述文字、有误导，还是有用 |
| `old-audit-apply ANSWERS... --out OUT` | 读取这些答案（`old-audit-NNN.answer.json`，放在 `old-audit-NNN.json` 旁边），写入 `old_audit_outcomes.jsonl`；只有带 `restates` 或 `misleading` 的 `remove` 才删除旧图，其他答案都保留旧图 |
| `writeback VAULT OUT OUTCOMES...` | 为每张池中卡片规划结果（带 `--write` 时则应用结果）。传入 `outcomes.jsonl` 和 `pregated.jsonl`，第一轮的文件放在重写的文件之前：后面的结果替换前面的结果。用 `--old-audit old_audit_outcomes.jsonl` 传入审计结果 |

池中卡片的写回规则：

- **draw**：新图替换图小节。如果旧图能渲染，新图只有在引用 `E` 和两条命题时（卡片的命题少于两条时，引用全部命题），才替换旧图。否则这张卡按下面的 skip 规则处理，规则名以 `draw:weak/` 开头。只是把旧图重画一遍的新流程图也不写入：它与旧图形状相同（节点数、边数相同，各节点的入度和出度相同），并且两张图的标签词至少有 30% 相同。这张卡按 skip 规则处理，规则名以 `draw:same_as_old/` 开头。
- **skip**：在被移除的领域里（由 `--remove-domain` 指定，或在审计中被分档为 `domain`），旧图被删除。因复核而进入池中的 `delete` 档卡片，旧图也被删除（`--delete-review-on-skip keep` 会保留它）。其他情况下，能渲染的旧图保留，不能渲染的删除。
- **旧图审计**：保留下来的旧图，如果审计结果是 `restates` 或 `misleading`，就被删除。
- **过宽的旧图**：保留下来、渲染宽度超过 700 px 的旧图，方向是 `LR` 或 `RL` 时改为从上到下（动作 `direction_tb`，规则名以 `+tb` 结尾）。不能改方向的保持原样，规则名以 `+wide` 结尾。
- **重写仍待定，或没有结果**：不做任何改动。只有用 `--old-audit` 传入旧图审计文件时，旧图审计才起作用。

一次典型的运行：先运行 `audit-extract`，再运行 `node tools/mermaid_check/check.mjs OUT/blocks.jsonl OUT/checks.jsonl`，然后依次是 `audit-tier`、`prune`、`pool`、对池运行 `packets`、模型作答、`apply --checker`、重写、`old-audit-packets` 和 `old-audit-apply`；最后先不带 `--write` 运行 `writeback`，查看计划，再带 `--write` 运行。运行 `sb diagrams <command> --help` 查看全部选项。

读写知识库卡片的命令（`audit-extract`、`prune`、`writeback`）按语言查找卡片小节。给了 `--language en|zh` 就用它；否则用知识库记录的语言（`sb language`）；再否则用配置文件的 `[cards] language`；最后看卡片实际用的小节标题。这些命令把选择结果输出到 stderr。如果卡片用的是另一种语言的小节标题，命令会停止，并指出应传入的 `--language`。`pool` 读取 `cards.jsonl`，所以沿用 `audit-extract` 用过的语言。`audit-extract` 读取 `--knowledge-dir` 下的卡片；没有给出时，用配置文件的 `[layout] knowledge`；再没有就用 `10_Knowledge`。

## 对照 Obsidian 的 Mermaid 检查

[`tools/mermaid_check`](../tools/mermaid_check/README.md) 在无头 Chrome 里加载 Obsidian 内置的 Mermaid 版本，并使用相同的补丁和选项。它需要 Node.js 18 或更高版本，以及一个 Chrome 或 Chromium 可执行文件（在该目录里运行 `npm ci`）。装好它时，测试套件会用它检查每种形式的生成图；没装时跳过这项测试。

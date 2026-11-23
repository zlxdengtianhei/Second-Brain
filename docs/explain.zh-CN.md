# 三层存储、支柱之间的图与下钻讲解

[English](explain.md)

## 三层存储

库可以只存三层：领域、支柱、卡。分类文件里没写 `topics` 的支柱，卡直接放在支柱下。这样的支柱有一个隐含的主题，名字就是支柱名：读分类的程序都看得到它（放卡、书架、复习顺序、录入的检查照旧），存下来的东西都不写它：卡没有 `topic` 一行，登记表里没有，分类文件里也没有 `topics` 列表。自己带主题的支柱仍是四层；两种支柱可以在一个库里并存。

三层的支柱下，录入不收新主题；地图页直接列卡（没有主题小标题，页面预算够时只有一页）；导出时这个支柱不再分小节。

把一张地图改成三层：

```sh
sb flatten --domain <slug>            # plan: what changes, and the topics each card had
sb flatten --domain <slug> --apply    # one recoverable transaction
sb recover <job> --rollback           # every byte back
```

它只改：`topics` 列表、每张已分类卡的 `topic` 一行（卡的其余字节不动）、它们在登记表里的 `topic`、地图页。新地图不再有的支柱页会删掉，原字节留在回滚日志里。结果里的 `topics_before` 记着每张卡原来的主题。

## 支柱变大时

三层的支柱到 50 张卡，`sb status` 就列出它（`topics.oversize`，`level: pillar`），并按卡之间的关系给一份怎样分组的提议（与主题拆分提议同一方法）。40 张不提。这个数是“再看一眼”的参考线，不是上限。

拆不拆、怎样拆，要看卡讲的是什么。`sb advise` 让模型判：

```bash
sb advise --pillar <pillar> [--domain <domain>] --executor api
```

程序先冻结证据：领域的问题和各支柱，这个支柱的 `why`，它的卡按提议分成的组（标题与本质一句），以及每组的关系有多少留在组内、连到别的组、别的支柱、别的领域。`sb explain` 的写作者回答 `keep`（不拆）、`split_pillars`（把这些组归进同领域的几个新支柱，各有名字和它回答什么）或 `new_domain`（一部分组拆成新领域，附它的问题），并引用证据说明理由；程序核答案（每组只进一个支柱，名字不和别的支柱、领域重），`sb explain` 的复核者对着证据判。不拆任何东西：建议存在这个 job 里，`sb status` 在这个支柱的 `topics.oversize` 一项里显示它（`advice`），直到这个支柱的卡或关系变了；没问过时那一项给出命令（`advise`）。库往宽长（支柱拆成支柱、一部分拆成新领域），始终三层。

## 支柱之间的图

一张卡只在一个支柱下，所以一张地图的支柱是一串列表；卡之间的关系跨支柱、跨地图，把支柱连成图。每张地图的首页有一节“支柱之间的连接”：每对支柱之间的卡片关系，按类型计数（`→` 有方向，照关系读：左 `depends_on` 右；`↔` 没有方向）。别的地图的支柱带上地图名。每个支柱页写出与它相连的支柱。

## 下钻讲解：`sb explain`

```sh
sb explain "<concept>"                    # its best card; or a card id, a pillar, a domain
sb explain --pillar "<pillar>" [--domain <slug>]
sb explain --domain <slug>
sb explain "<concept>" --executor api     # [explain.writer] and [explain.reviewer]
```

程序先为概念建一棵树：领域、支柱、卡，再沿卡的前提与组成（`depends_on`、`has_part`）往下到基础卡（`--depth`，默认卡下 3 层；`--max-children`，默认 5）。第二次碰到的卡只写成“见上”，指向它已在的位置。整个支柱会按关系切成几组相关的卡。树只是视图，不存，存储仍是三层。

写作者给每个节点写一段：`explain` 按读者此刻的层次讲这个节点，并点名每个子节点、说它补上什么，让下一步往下走得通；`recap`（有子节点的）是读完子节点、往回走时读的一句话，把子节点串回这个节点。程序核每一段：每个节点一次、每个子节点都点到、有回到高层的一句、长度。另一次运行的复核者（API 模式下是另一家模型）按卡片内容逐节点判；判“改”的节点在复核规则允许的轮数内退回写作者。轮数用完仍没过的照样写出，带标记，不丢。宿主模式与别的维护任务一样交包（`sb job advance`）。

页面（Markdown 与 HTML，写在 `[layout] articles/explain/` 或 `--out`）有两部分：

- 下钻讲解，从上到下（HTML 里每层可折叠）；
- 复习路径，从下到上：卡上复核过的问答，前提在前；每学完一组子节点，回到它们的上一层读那句串起来的话，最后回到领域。

不往知识目录写任何东西。

## 复习页会回到高层

每日复习页（`sb today`、`sb daily`）末尾有“学完以上的卡，回到高层”：今天的卡按支柱分段，每段写这个支柱回答什么、它最新一次整支柱讲解（`sb explain --pillar`）的串起来那句，以及今天这几张卡之间怎样连。

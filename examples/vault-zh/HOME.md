# HOME — SecondBrain

## 今日复习

```secondbrain-review
```

2026-10-13 的复习页还没有生成：运行 `sb daily --apply`，它生成复习页并在这里加上链接（不依赖插件）。

面板由 SecondBrain Review 插件显示；终端里用 `sb review status` 看进度，`sb review queue` 看今天的卡。

## 现在做什么

1. 复习：用上面的面板，或终端 `sb review queue`。
2. 新资料放进 `00_Inbox/`，运行 `sb inbox list`，再 `sb ingest <文件>`。
3. 未完成的任务：`sb job list`；部分写入的源：`sb ingest --retry <任务>`。

## 领域入口

## 学习科学

- [[20_MOCs/我们如何学习|我们如何学习]] · 13 张

## 定量推理

- [[20_MOCs/用数据推理|用数据推理]] · 17 张
- [[20_MOCs/计算基础|计算基础]] · 22 张

## 库存

当前库存（2026-10-13 现算）：**52 张概念卡**，3 个域；空分类字段 0 张。

| 轨 | 域（卡数） |
|---|---|
| 学习科学 | learning 13 |
| 定量推理 | statistics 17、computing 22 |

### 目录（本轮最终路径集合）

| 目录 | 文件数 |
|---|---|
| `00_Inbox/` | 2 |
| `10_Knowledge/` | 52 |
| `20_MOCs/` | 15 |
| `40_Sources/` | 7 |
| `50_Reviews/` | 0 |
| `_data/` | 5 |

> 现值用 `sb status` 查询；完整卡片与策展 YAML 是真源。

## 录入与维护

`sb ingest <文件>`：完整来源 → 分卡 → 分类与关系 → 独立复核 → 事务写入 → MOC。

`sb classify --domain <域>` 补分类；`sb refresh` 预览 MOC 变化，`sb refresh --apply` 写入；`sb recover <任务> --rollback` 撤销一次写入；`sb docs --apply` 重写本页。

缺主题或领域的内容会留下待办；分类与 MOC 不改复习记录。

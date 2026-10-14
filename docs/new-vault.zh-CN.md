# 建立自己的知识库

[English](new-vault.md)

第一次入库之前，知识库需要三样东西：一份领域目录（catalog）、每张地图页（MOC）各一个分类体系文件，以及一份空的卡片登记表。领域目录和分类体系文件由你来写；`sb init` 把领域目录放进知识库，并补上登记表和各个文件夹。[中文示例库](../examples/vault-zh/)和[英文示例库](../examples/vault/)里都有一整套，可以照着复制。

下面的步骤使用默认文件夹名。要用别的名称，先在配置文件里设置 `[layout]`（[configuration.zh-CN.md](configuration.zh-CN.md)）。

## 1. 让 `sb` 指向这个文件夹

```sh
mkdir -p ~/MyVault
export SECONDBRAIN_VAULT=~/MyVault
```

这个文件夹可以是已有的 Obsidian 知识库。`sb` 只在布局（layout）指定的文件夹里写入，另外还写首页 `HOME.md`。如果你在 `[layout] entry_docs` 里列出了自己的顶层笔记，`sb` 也会写这些笔记里的生成区块。

## 2. 写领域目录

领域目录列出各个领域、每个领域的地图页，以及卡片可以使用的关系类型。它可以存在任何位置，例如存为 `catalog.json`：

```json
{
  "schema": "secondbrain-catalog/1",
  "tracks": [
    {"id": "core", "title": "Core", "domains": ["cooking", "gardening"]}
  ],
  "domains": {
    "cooking": {"moc": "Cooking", "question": "How do heat, time and ingredients change food?"},
    "gardening": {"moc": "Gardening", "question": "How do plants grow, and how do we help them?"}
  },
  "relation_types": ["depends_on", "required_by", "part_of", "has_part", "similar_to", "contrasts_with"],
  "inverse_relations": {
    "depends_on": "required_by", "required_by": "depends_on",
    "part_of": "has_part", "has_part": "part_of",
    "similar_to": "similar_to", "contrasts_with": "contrasts_with"
  },
  "domain_relations": {}
}
```

- 领域的短名（slug）同时是这个领域卡片所在文件夹的名称：`10_Knowledge/<slug>/`。
- `question` 是一句话，告诉模型哪些内容属于这个领域。
- 每个领域必须至少属于一个分组（track）。分组在首页上把领域归在一起。
- `inverse_relations` 把每种关系映射到它的反向关系。这样，新卡片指向另一张卡的链接，可以在那张卡上反向补上。映射必须在两个方向上一致；对称关系映射到它自己。
- `depends_on`、`required_by`、`follows` 和 `precedes` 决定学习顺序：在地图页里，以及在复习队列的新卡里，先修在前。
- 可选的 `relation_gloss` 把一种关系类型映射到一段简短说明。

然后把它写进知识库：

```sh
sb init --catalog catalog.json
```

`sb init` 把领域目录写到 `_data/secondbrain/catalog.json`，写一份空的卡片登记表 `_data/notes_registry.json`，并在缺少时建立文件夹 `10_Knowledge/`、`20_MOCs/_pillars/` 和 `00_Inbox/`。它的结果列出 `created_folders` 和 `taxonomy_files_missing`；后者是你还要写的分类体系文件（第 3 步）。用同一份领域目录再运行一次，什么都不会改变；它只补上缺少的登记表或文件夹。

领域目录只写入一次。`sb init` 还在领域目录里记录知识库的语言：配置文件设置了 `[cards] language` 就用它；否则用文件夹里已有地图页的语言；再否则用英文。`sb language` 显示这个语言；`sb language --set zh` 切换语言，然后由 `sb refresh --apply` 和 `sb docs --apply` 重写生成的页面。以后要改领域目录的其他部分，请小心编辑 `_data/secondbrain/catalog.json`，或使用整理（curation）命令。手动编辑后运行 `sb doctor`：它的 `catalog` 字段给出 `valid`，或 `invalid:` 加原因；这时 `sb status` 也会在 `issues` 里列出 `catalog_invalid`。

## 3. 为每张地图页写一个分类体系文件

每张地图页有一个文件 `20_MOCs/_pillars/<MOC title>.yaml`；`sb init` 结果里的 `taxonomy_files_missing` 列出还要写的文件。支柱是一个领域的主要组成部分，主题是支柱里的一个个书架。每张卡都归入一个领域、一个支柱和一个主题。

```yaml
title: Cooking
narrative: >-
  Food changes with heat and time. Knowing why lets you fix a dish
  instead of following a recipe blindly.
pillars:
  - name: Heat
    order: 1
    why: How heat moves into food and what it does there.
    topics:
      - Browning
      - Boiling and steaming
  - name: Ingredients
    order: 2
    why: What each ingredient contributes.
    topics:
      - Salt
      - Fats
```

领域目录里的每张地图页都要写一个文件；在这个例子里还要写 `Gardening.yaml`。某个领域有自己的文件之前，`sb status` 对它报告 `missing_taxonomy`，入库也不会在这个领域生成卡片。所有领域都还没有主题时，`sb ingest` 在调用任何模型之前就停下，并列出要写的分类文件。

先从小处做起。生产者可以在已有支柱下提议新主题（默认每份来源两个），复核者必须接受这些主题。需要新支柱或新领域的材料不会生成卡片，并在任务（job）结果里报告为 `needs_taxonomy`。再入库这类材料之前，先把这个支柱加进文件。

## 4. 检查并生成地图页

```sh
sb status          # "issues": [] 表示知识库已就绪
sb refresh --apply # 把地图页写到 20_MOCs/ 下
```

`issues` 列出会阻止写入的问题。`map_issues` 统计 `sb refresh` 报告的地图页问题，例如分类已不合法的卡片；`sb refresh` 会逐条列出。

## 5. 入库

把笔记、文章或转录稿放进 `00_Inbox/`，然后按 [README](../README.zh-CN.md) 里的快速上手操作：先运行 `sb inbox list`，再用宿主执行器或 API 执行器运行 `sb ingest <file>`。

要在 Obsidian 里复习，请安装 [SecondBrain Review 插件](../obsidian-plugin/README.md)。它读取 `sb init` 记录的语言，并使用对应的界面和卡片小节名（英文知识库里是 `Review cards`、`Essence` 和 `Propositions`）。只有当你的卡片用了别的小节标题时，才需要在插件设置里填写小节名。

"""Fixed text of generated pages (maps, HOME, the daily review page), per language.

``[cards] language`` selects the table, as it does for card section headings.
The ``zh`` map text reproduces the pages earlier versions wrote byte for byte
(``tests/test_page_language.py`` holds the golden pages), so switching to this
module does not change an existing Chinese vault's maps. Vault content (card
titles, taxonomy names, narratives) is never translated.
"""
from __future__ import annotations

from . import config

MOC = {
    'zh': {
        'residual': '待分类',
        'alias_pipe': '／',
        'card_sep': '　',
        'target_sep': '；',
        'cross_map': '［跨域→%s］',
        'residual_title': '# %s · 待分类与非法分类',
        'navigation': '## 本页导航',
        'back_to_root': '回域入口',
        'covers': '_本 MOC 覆盖：%s ｜ 生成页 %d（含本页）_',
        'domain_sep': '、',
        'narrative': '## 叙事',
        'pillars': '## 支柱',
        'pillar_line': '- %s（%d 卡）',
        'pages': '　分页：',
        'residual_line': '- %s（%d 卡，待分类＋非法分类）',
        'bridges': '## 相关 MOC 桥',
        'unknown': '（未知）',
        'page_index': '## 分页索引（全部可达）',
        'this_page': '- [[%s|本页 · 域入口]]',
        'page_label': '页 p%d',
        'missing_header': '待分类（缺 pillar 或 topic）',
        'invalid_header': '非法分类（不属于本 MOC taxonomy）',
        'missing_both': '缺pillar/topic',
        'missing_pillar': '缺pillar',
        'missing_topic': '缺topic',
        'bad_pillar': 'pillar=%r 不在本 MOC taxonomy',
        'bad_topic': 'topic=%r 不属于 pillar=%r',
        'pillar_page': '_支柱分页 %d/%d（页码是浏览切片，不是新语义层）_',
        'continued': '（承接上一页）',
        'no_previous': '⟵ 上一页（无）',
        'previous': '上一页',
        'no_next': '⟶ 下一页（末页）',
        'next': '下一页',
    },
    'en': {
        'residual': 'Unclassified',
        'alias_pipe': '/',
        'card_sep': ' · ',
        'target_sep': '; ',
        'cross_map': ' [other map: %s]',
        'residual_title': '# %s · Unclassified and invalid',
        'navigation': '## Navigation',
        'back_to_root': 'Back to the map',
        'covers': '_This map covers: %s | %d generated pages, this one included_',
        'domain_sep': ', ',
        'narrative': '## Narrative',
        'pillars': '## Pillars',
        'pillar_line': '- %s (cards: %d)',
        'pages': ' · pages: ',
        'residual_line': '- %s (cards: %d, unclassified or invalid)',
        'bridges': '## Related maps',
        'unknown': '(unknown)',
        'page_index': '## Page index (every page)',
        'this_page': '- [[%s|This page · map root]]',
        'page_label': 'page %d',
        'missing_header': 'Unclassified (pillar or topic missing)',
        'invalid_header': "Invalid (not in this map's taxonomy)",
        'missing_both': 'pillar and topic missing',
        'missing_pillar': 'pillar missing',
        'missing_topic': 'topic missing',
        'bad_pillar': "pillar=%r is not in this map's taxonomy",
        'bad_topic': 'topic=%r is not under pillar=%r',
        'pillar_page': '_Pillar page %d/%d (pages only split a long list)_',
        'continued': ' (continued)',
        'no_previous': '⟵ previous (none)',
        'previous': 'previous',
        'no_next': '⟶ next (last page)',
        'next': 'next',
    },
}

PAGES = {
    'zh': {
        'review_page': '今日复习-{date}',
        'review_title': '# 今日复习 {date}',
        'review_intro': '先合上笔记复述，再打开核对；评级用 HOME 里的 SecondBrain Review 面板。',
        'back_home': '返回首页',
        'due_cards': '到期整卡',
        'due_inline': '到期命题笔记',
        'new_today': '今日新卡（5张）',
        'none': '本项暂无。',
        'new_left': '未开始的新卡共 {n} 张，其余在复习面板的队列里继续；不在这页转储整个库。',
        'unparsed': '解析不了的复习日期：{n}。',
        'inventory': '当前库存（{date} 现算）：**{cards:,} 张概念卡**，{domains} 个域；空分类字段 {missing:,} 张。',
        'track_table': ['| 轨 | 域（卡数） |', '|---|---|'],
        'domain_sep': '、',
        'dirs_title': '### 目录（本轮最终路径集合）',
        'dirs_table': ['| 目录 | 文件数 |', '|---|---|'],
        'dirs_live_table': ['| 目录 | 现有文件（现算） |', '|---|---|'],
        'stats_note': '> 现值用 `sb status` 查询；完整卡片与策展 YAML 是真源。',
        'stats_marker': '<!-- 由secondbrain从完整库存生成 -->',
        'home': [
            '# HOME — SecondBrain', '',
            '## 今日复习', '',
            '```secondbrain-review',
            '```', '',
            '面板由 SecondBrain Review 插件显示；终端里用 `sb review status` 看进度，`sb review queue` 看今天的卡。', '',
            '## 现在做什么', '',
            '1. 复习：用上面的面板，或终端 `sb review queue`。',
            '2. 新资料放进 `{inbox}/`，运行 `sb inbox list`，再 `sb ingest <文件>`。',
            '3. 未完成的任务：`sb job list`；部分写入的源：`sb ingest --retry <任务>`。',
        ],
        'domains_title': '## 领域入口',
        'map_line': '- [[{path}|{title}]] · {n} 张',
        'stats_title': '## 库存',
        'maintenance': [
            '## 录入与维护', '',
            '`sb ingest <文件>`：完整来源 → 分卡 → 分类与关系 → 独立复核 → 事务写入 → MOC。', '',
            '`sb classify --domain <域>` 补分类；`sb refresh` 预览 MOC 变化，`sb refresh --apply` 写入；'
            '`sb recover <任务> --rollback` 撤销一次写入；`sb docs --apply` 重写本页。', '',
            '缺主题或领域的内容会留下待办；分类与 MOC 不改复习记录。', '',
        ],
        'entry_block': [
            '## 当前操作入口', '',
            '使用 `sb`：`sb status` 查库存，`sb inbox list` 看待办，`sb ingest <文件>` 录入，'
            '`sb classify --domain <域>` 分类，',
            '`sb review queue` 看今天要复习的卡，`sb refresh --apply` 刷新 MOC，`sb job list` 查看未完成任务。',
            '这些命令不自动打开 Obsidian；生成后从 HOME 或左侧目录打开。',
        ],
    },
    'en': {
        'review_page': 'Daily review {date}',
        'review_title': '# Daily review {date}',
        'review_intro': 'Recall first with the note closed, then open it to check; rate cards in the '
                        'SecondBrain Review dashboard on HOME.',
        'back_home': 'Back to HOME',
        'due_cards': 'Due cards',
        'due_inline': 'Due proposition notes',
        'new_today': 'New cards today (5)',
        'none': 'Nothing here.',
        'new_left': 'New cards not started yet: {n}; the rest continue in the dashboard queue. '
                    'This page does not list the whole vault.',
        'unparsed': 'Review dates that could not be read: {n}.',
        'inventory': 'Counted on {date}: **cards: {cards:,}** · domains: {domains} · '
                     'without pillar or topic: {missing:,}.',
        'track_table': ['| Track | Domains (cards) |', '|---|---|'],
        'domain_sep': ', ',
        'dirs_title': '### Folders (paths after this run)',
        'dirs_table': ['| Folder | Files |', '|---|---|'],
        'dirs_live_table': ['| Folder | Files now |', '|---|---|'],
        'stats_note': '> Current numbers: `sb status`. The cards and the curated taxonomy YAML are the source of truth.',
        'stats_marker': '<!-- generated by secondbrain from the full inventory -->',
        'home': [
            '# HOME — SecondBrain', '',
            "## Today's review", '',
            '```secondbrain-review',
            '```', '',
            'The dashboard comes from the SecondBrain Review plugin; in a terminal use `sb review status` '
            "for progress and `sb review queue` for today's cards.", '',
            '## What to do now', '',
            '1. Review: use the dashboard above, or `sb review queue` in a terminal.',
            '2. Put new material into `{inbox}/`, run `sb inbox list`, then `sb ingest <file>`.',
            '3. Unfinished jobs: `sb job list`; sources with pending cards: `sb ingest --retry <job>`.',
        ],
        'domains_title': '## Maps',
        'map_line': '- [[{path}|{title}]] · cards: {n}',
        'stats_title': '## Inventory',
        'maintenance': [
            '## Ingest and maintenance', '',
            '`sb ingest <file>`: whole source → cards → classification and relations → independent review '
            '→ one transaction → maps.', '',
            '`sb classify --domain <domain>` fills in missing classification; `sb refresh` previews map '
            'changes and `sb refresh --apply` writes them; `sb recover <job> --rollback` undoes a write; '
            '`sb docs --apply` rewrites this page.', '',
            'Material that needs a new topic or domain stays listed as pending; classification and maps never '
            'change review records.', '',
        ],
        'entry_block': [
            '## Commands', '',
            'Use `sb`: `sb status` for the inventory, `sb inbox list` for new material, `sb ingest <file>` to '
            'make cards, `sb classify --domain <domain>` to classify,',
            "`sb review queue` for today's reviews, `sb refresh --apply` to rebuild the maps, "
            '`sb job list` for unfinished jobs.',
            'These commands do not open Obsidian; open the pages from HOME or the file list.',
        ],
    },
}


def language() -> str:
    """The configured language (``[cards] language``)."""
    return config.current().card_language


def moc(lang: str | None = None) -> dict:
    """Map page text for ``lang`` (default: the configured language)."""
    return MOC[lang or language()]


def pages(lang: str | None = None) -> dict:
    """HOME, entry block and review page text for ``lang`` (default: configured)."""
    return PAGES[lang or language()]

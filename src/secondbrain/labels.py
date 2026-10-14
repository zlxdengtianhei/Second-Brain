"""Fixed text of generated pages (maps, HOME, the daily review page), per language.

``[cards] language`` selects the table, as it does for card section headings.
The ``zh`` map text reproduces the pages earlier versions wrote byte for byte
(``tests/test_page_language.py`` holds the golden pages), so switching to this
module does not change an existing Chinese vault's maps. Vault content (card
titles, taxonomy names, narratives) is never translated.

A vault has one language for its generated text. It is recorded as the
top-level ``language`` of the catalog (``sb init``, ``sb language --set``);
a vault made before the record existed is detected from its generated map
pages. ``[cards] language`` defaults to the vault's language, and a write
whose configured language differs from it is refused (:func:`check_language`).
"""
from __future__ import annotations

import json

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
        # meaning of a relation type as shown to models (catalog.relation_meanings)
        'relation_plain': 'A {type} B',
        'relation_forward': 'A {type} B：{gloss}',
        'relation_inverse': 'A {type} B 等于 B {other} A',
        'relation_inverse_gloss': 'A {type} B 等于 B {other} A；{other}：{gloss}',
        'relation_symmetric': 'A {type} B 与 B {type} A 同义',
        'relation_symmetric_gloss': 'A {type} B 与 B {type} A 同义：{gloss}',
        'review_title': '# 今日复习 {date}',
        'review_intro': '先合上笔记复述，再打开核对；评级用 HOME 里的 SecondBrain Review 面板。',
        'back_home': '返回首页',
        'due_cards': '到期卡',
        'new_today': '今日新卡',
        'none': '本项暂无。',
        'new_left': '未开始的新卡共 {available} 张，排入 {n} 张。这一页是 {date} 这个复习日（{hour:02d}:00 起算）的队列，'
                    '与这个复习日里 `sb review queue` 给出的相同。',
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
            '{review_link}', '',
            '面板由 SecondBrain Review 插件显示；终端里用 `sb review status` 看进度，`sb review queue` 看今天的卡。', '',
            '## 现在做什么', '',
            '1. 复习：用上面的面板，或终端 `sb review queue`。',
            '2. 新资料放进 `{inbox}/`，运行 `sb inbox list`，再 `sb ingest <文件>`。',
            '3. 未完成的任务：`sb job list`；部分写入的源：`sb ingest --retry <任务>`。',
        ],
        'needs_review': '4. 标为 needs-review 的卡：**{n:,}** 张。把 `status` 改为 `confirmed` 之前，它们不进复习队列；'
                        '在 Obsidian 里搜索 `[status:needs-review]`，或运行 `sb status --needs-review` 列出它们，'
                        '再逐张合并、修改或确认。',
        'review_link': '[[{review}|打开 {date} 的复习页]]：不依赖插件；`sb daily --apply` 生成当天的复习页，'
                       '并把这里改成当天的链接。',
        'review_missing': '{date} 的复习页还没有生成：运行 `sb daily --apply`，它生成复习页并在这里加上链接'
                          '（不依赖插件）。',
        'review_no_extra': '复习页需要 review 扩展：{install}，然后运行 `sb daily --apply`。',
        # the project is installed from its folder; a PyPI package with the same name is someone else's
        'install': '在 Second-Brain 目录（安装 `sb` 的那个目录）里运行 `pip install -e ".[{extra}]"`',
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
        'relation_plain': 'A {type} B',
        'relation_forward': 'A {type} B: {gloss}',
        'relation_inverse': 'A {type} B means B {other} A',
        'relation_inverse_gloss': 'A {type} B means B {other} A; {other}: {gloss}',
        'relation_symmetric': 'A {type} B means the same as B {type} A',
        'relation_symmetric_gloss': 'A {type} B means the same as B {type} A: {gloss}',
        'due_cards': 'Due cards',
        'new_today': "Today's new cards",
        'none': 'Nothing here.',
        'new_left': "New cards not started yet: {available}; {n} of them are in this day's queue. "
                    'This page is the queue of review day {date} (days start at {hour:02d}:00), '
                    'the same as `sb review queue` gives during that day.',
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
            '{review_link}', '',
            'The dashboard comes from the SecondBrain Review plugin; in a terminal use `sb review status` '
            "for progress and `sb review queue` for today's cards.", '',
            '## What to do now', '',
            '1. Review: use the dashboard above, or `sb review queue` in a terminal.',
            '2. Put new material into `{inbox}/`, run `sb inbox list`, then `sb ingest <file>`.',
            '3. Unfinished jobs: `sb job list`; sources with pending cards: `sb ingest --retry <job>`.',
        ],
        'needs_review': '4. Cards marked needs-review: **{n:,}**. They stay out of the review queue until their '
                        '`status` is `confirmed`: search `[status:needs-review]` in Obsidian, or run '
                        '`sb status --needs-review`, then merge, fix or confirm each.',
        'review_link': "[[{review}|Open the review page of {date}]]: works without the plugin; "
                       "`sb daily --apply` makes each day's page and points this link to it.",
        'review_missing': 'The review page of {date} has not been made yet: run `sb daily --apply`; it makes '
                          'the page and adds the link here (works without the plugin).',
        'review_no_extra': 'The review page needs the review extra: {install}, then run `sb daily --apply`.',
        'install': 'run `pip install -e ".[{extra}]"` in your Second-Brain folder (the one `sb` was installed from)',
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
    """The active language: ``[cards] language``, or the vault's when the config file does not set it."""
    return config.current().card_language


def recorded_language(catalog) -> str | None:
    """The ``language`` recorded in a catalog mapping, if any."""
    value = catalog.get('language') if isinstance(catalog, dict) else None
    return value if value in MOC else None


def detected_language(root) -> str | None:
    """Language of the generated map root pages (``<mocs>/*.md``); None when there are none."""
    from . import layout as layouts
    from .store import read_bytes
    if root is None:
        return None
    folder = layouts.of(root).mocs
    try:
        names = sorted(p.name for p in (root / folder).iterdir() if p.suffix == '.md')
    except OSError:
        return None
    markers = [(lang, table['page_index'].encode()) for lang, table in MOC.items()]
    for name in names:
        try:
            raw = read_bytes(root, f'{folder}/{name}') or b''
        except (OSError, ValueError):
            continue
        for lang, marker in markers:
            if marker in raw:
                return lang
    return None


def _catalog(root):
    from . import layout as layouts
    from .store import read_bytes
    try:
        raw = read_bytes(root, layouts.of(root).catalog)
        return json.loads(raw) if raw else None
    except (OSError, ValueError):
        return None


def vault_language(root, *, catalog=None) -> str | None:
    """The vault's language: recorded in the catalog, else detected from its maps, else None."""
    if catalog is None and root is not None:
        catalog = _catalog(root)
    return recorded_language(catalog) or detected_language(root)


def describe(root) -> dict:
    """Where the vault's language comes from and which language writes use (`sb language`)."""
    settings = config.current()
    recorded = recorded_language(_catalog(root))
    detected = detected_language(root)
    vault = recorded or detected
    return {'recorded': recorded, 'detected': detected, 'vault': vault,
            'configured': settings.card_language if settings.card_language_set else None,
            'config_file': str(settings.source) if settings.source else None,
            'active': settings.card_language,
            'writes_allowed': vault is None or vault == settings.card_language}


def check_language(snapshot) -> None:
    """Refuse a write whose generated text would not be in the vault's language.

    Called by every writer through ``writable_snapshot``. When the config file
    does not set ``[cards] language``, the vault's language becomes the active
    one instead.
    """
    vault = vault_language(snapshot.root, catalog=snapshot.catalog)
    settings = config.current()
    if vault is None or vault == settings.card_language:
        return
    if not settings.card_language_set:
        config.use_language(vault)
        return
    source = recorded_language(snapshot.catalog) and 'recorded in the catalog' or 'found in the map pages'
    where = f'in {settings.source}' if settings.source else 'in the active settings'
    want = settings.card_language
    raise ValueError(
        f'This vault\'s generated text is {vault} ({source}), but [cards] language = "{want}" {where}. '
        f'Writing now would mix {want} maps, HOME and cards into a {vault} vault, so nothing was written. '
        f'To keep {vault}, remove `language` from [cards] (the vault\'s language is then used). '
        f'To switch the vault to {want}, run `sb language --set {want}`, then `sb refresh --apply` and '
        f'`sb docs --apply` to rewrite the generated pages.')


def install_hint(extra: str, lang: str | None = 'en') -> str:
    """How to add an optional extra: a path install from the checkout, never a PyPI name."""
    return PAGES[lang or language()]['install'].format(extra=extra)


def moc(lang: str | None = None) -> dict:
    """Map page text for ``lang`` (default: the configured language)."""
    return MOC[lang or language()]


def pages(lang: str | None = None) -> dict:
    """HOME, entry block and review page text for ``lang`` (default: configured)."""
    return PAGES[lang or language()]

"""Local user-entry and spaced-repetition projections; no model calls."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from . import jobs
from .classification import projection_changes, writable_snapshot, snapshot_checks
from .moc import Projection, build_mocs, learning_order
from .store import Conflict, digest, read_bytes
from .vault import read_snapshot


def parsed_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _read(root, rel, reads):
    if rel not in reads:
        raw = read_bytes(root, rel)
        reads[rel] = raw
    return reads[rel]


def _record_checks(checks, reads, *, exclude=()):
    excluded = set(exclude)
    for rel, raw in reads.items():
        if rel in excluded:
            continue
        observed = None if raw is None else digest(raw)
        if rel in checks and checks[rel] != observed:
            raise Conflict(f'投影依赖在读取期间变化：{rel}')
        checks[rel] = observed


_CONTROLLED_MARKERS = ('kb-stats', 'secondbrain-entry', 'kb-dirs')

ENTRY_BLOCK = ['<!-- GENERATED:BEGIN secondbrain-entry -->', '## 当前操作入口', '',
               '使用 `sb`：`sb status` 查库存，`sb inbox` 看待办，`sb ingest <文件>` 录入，`sb classify --domain <域>` 分类，',
               '`sb refresh --apply` 刷新 MOC，`sb today --apply` 生成今日复习，`sb job list` 查看未完成任务。',
               '这些命令不自动打开 Obsidian；生成后从 HOME 或左侧目录打开。',
               '<!-- GENERATED:END secondbrain-entry -->']


def _scan_markers(text):
    """Validate all known markers in their document-wide token order."""
    token_re = r'<!-- GENERATED:(BEGIN|END) (' + '|'.join(
        re.escape(name) for name in _CONTROLLED_MARKERS) + r') -->'
    active = None
    for match in re.finditer(token_re, text):
        kind, name = match.group(1), match.group(2)
        if kind == 'BEGIN':
            if active is not None:
                return False, f'{name}受控标记嵌套（当前活动块：{active}）'
            active = name
        elif active != name:
            return False, f'{name}受控标记结构残缺（当前活动块：{active}）'
        else:
            active = None
    if active is not None:
        return False, f'{active}受控标记结构残缺'
    return True, None


def _marker_status(text, name, *, allow_zero=False):
    """Validate one named block after validating all known markers together."""
    valid, detail = _scan_markers(text)
    if not valid:
        return False, detail
    token_re = rf'<!-- GENERATED:(BEGIN|END) {re.escape(name)} -->'
    tokens = list(re.finditer(token_re, text))
    begins = sum(m.group(1) == 'BEGIN' for m in tokens)
    ends = len(tokens) - begins
    expected = {(0, 0), (1, 1)} if allow_zero else {(1, 1)}
    if (begins, ends) not in expected:
        return False, f'{name}受控标记数量异常（BEGIN={begins}, END={ends}）'
    return True, None


def _replace_or_insert_entry(raw: bytes, migration: bytes) -> bytes:
    begin = b'<!-- GENERATED:BEGIN secondbrain-entry -->'
    end = b'<!-- GENERATED:END secondbrain-entry -->'
    start = raw.find(begin)
    if start >= 0:
        stop = raw.find(end, start)
        if stop < 0:
            raise Conflict('secondbrain-entry受控块结构残缺')
        stop += len(end)
        return raw[:start] + migration + raw[stop:]
    stats_end = b'<!-- GENERATED:END kb-stats -->'
    stop = raw.find(stats_end)
    if stop < 0:
        raise Conflict('kb-stats受控块缺失，无法插入secondbrain-entry')
    stop += len(stats_end)
    return raw[:stop] + migration + raw[stop:]


def _validate_generated_document(raw: bytes, name: str) -> None:
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise Conflict(f'{name}生成结果不是UTF-8') from exc
    valid, detail = _scan_markers(text)
    if not valid:
        raise Conflict(f'{name}生成结果受控标记无效：{detail}')
    for marker, allow_zero in [('kb-stats', False), ('secondbrain-entry', False), ('kb-dirs', True)]:
        valid, detail = _marker_status(text, marker, allow_zero=allow_zero)
        if not valid:
            raise Conflict(f'{name}生成结果标记数量无效：{detail}')


def _directory_counts(root, final_paths):
    """Count the original directory members plus absent paths planned this round."""
    from . import layout as layouts
    directories = layouts.of(root).counted_dirs
    counts = {}
    existing = set()
    for directory in directories:
        folder = root / directory
        if folder.is_dir():
            for p in folder.rglob('*'):
                if p.is_file() and not p.is_symlink() and not any(
                        x.startswith('.') for x in p.relative_to(folder).parts):
                    existing.add(p.relative_to(root).as_posix())
    planned = set(final_paths)
    for directory in directories:
        prefix = directory + '/'
        counts[directory] = len({p for p in existing | planned if p.startswith(prefix)})
    return counts


def review_projection(snapshot, today: date, *, extra_targets=(), generated_at=None,
                      reads=None) -> Projection:
    reads = {} if reads is None else reads
    lay = _layout(snapshot)
    whole, inline, new, unparsed = [], [], [], []
    for c in snapshot.cards.values():
        row = {'id': c.id, 'title': c.title, 'path': c.path[:-3], 'domain': c.domain}
        due = parsed_date(c.fm.get('sr-due'))
        if due is not None and due <= today:
            whole.append({**row, 'due': due.isoformat()})
        comments = re.findall(r'<!--SR:([^>]*)-->', c.body)
        dates = []
        for content in comments:
            value = parsed_date(content.lstrip('!').split(',')[0])
            if value is None:
                unparsed.append({'id': c.id, 'value': content})
            elif value <= today:
                dates.append(value)
        if dates:
            inline.append({**row, 'due_count': len(dates), 'earliest': min(dates).isoformat()})
        tags = c.fm.get('tags') or []
        if isinstance(tags, str):
            tags = [tags]
        if not comments and not any(k.startswith('sr-') for k in c.fm) and 'review' in tags and c.fm.get('kind') != 'record':
            new.append(c)
    whole.sort(key=lambda x: (x['due'], x['id']))
    inline.sort(key=lambda x: (x['earliest'], x['id']))
    ordered, cycles = learning_order(new)
    new_rows = [{'id': c.id, 'title': c.title, 'path': c.path[:-3], 'domain': c.domain} for c in ordered]
    counts = {'scanned': len(snapshot.cards), 'due_notes': len(whole), 'due_inline': len(inline),
              'inline_due_total': sum(r['due_count'] for r in inline), 'new_notes': len(new), 'sr_unparsed': len(unparsed)}
    state = {'date': today.isoformat(), 'due_notes': whole, 'due_inline': inline, 'new_notes': new_rows,
             'counts': counts, 'issues': cycles + unparsed, 'generated_by': 'secondbrain'}
    lines = ['---', f'date: {today.isoformat()}', 'generated_by: secondbrain', '---', '', f'# 今日复习 {today.isoformat()}', '',
             '先合上笔记复述，再打开核对；在卡片页用 Spaced Repetition 插件评级。', '', f'[[{lay.home[:-3]}|返回首页]]', '']
    for title, rows, label in [('到期整卡', whole, 'due'), ('到期命题笔记', inline, 'earliest'), ('今日新卡（5张）', new_rows[:5], None)]:
        lines.extend([f'## {title}', ''])
        for r in rows:
            detail = f' · {r[label]}' if label else ''
            lines.append(f'- [[{r["path"]}|{r["title"]}]]{detail}')
        if not rows:
            lines.append('本项暂无。')
        lines.append('')
    lines.extend([f'未开始的新卡共 {len(new_rows)} 张，其余从插件队列继续；不在这页转储整个库。',
                   f'解析不了的复习日期：{len(unparsed)}。', ''])
    trend = []
    for i in range(6, -1, -1):
        rel = f'{lay.projections}/review_state_{(today - timedelta(days=i)).isoformat()}.json'
        old = _read(snapshot.root, rel, reads)
        value = state if i == 0 else json.loads(old) if old else None
        if value:
            trend.append({'date': (today - timedelta(days=i)).isoformat(),
                          'due_count': len(value.get('due_notes', [])) + len(value.get('due_inline', []))})
    known = {c.path.removesuffix('.md') for c in snapshot.cards.values()}
    known.update(str(p).removesuffix('.md') for p in extra_targets)
    stems = {Path(p).name for p in known}
    broken = []
    for target in re.findall(r'\[\[([^\]|#]+)', '\n'.join(lines)):
        target = target.removesuffix('.md')
        rel = target + '.md'
        raw = _read(snapshot.root, rel, reads)
        exists = raw is not None
        if target not in known and Path(target).name not in stems and not exists:
            broken.append(target)
    site = {'date': today.isoformat(),
            'generated_at': generated_at or datetime.now().astimezone().isoformat(timespec='seconds'),
            'broken_links': len(broken), 'broken_links_measured': True, 'broken_links_scope': 'review_page',
            'due_count': len(whole) + len(inline), 'new_count': len(new_rows),
            'due_top': [{'title': x['title'], 'due': x['due']} for x in whole[:10]], 'trend_7d': trend}
    return Projection({f'{lay.reviews}/今日复习-{today}.md': '\n'.join(lines).encode(),
                       f'{lay.projections}/review_state_{today}.json': jobs.encoded(state),
                       f'{lay.projections}/kb_review.json': jobs.encoded(site)}, counts, cycles + unparsed)


def entry_projection(snapshot, today: date, *, final_paths=(), reads=None) -> Projection:
    reads = {} if reads is None else reads
    lay = _layout(snapshot)
    counts = Counter(c.domain for c in snapshot.cards.values())
    missing = sum(not c.fm.get('pillar') or not c.fm.get('topic') for c in snapshot.cards.values())
    catalog = snapshot.catalog
    directory_counts = _directory_counts(snapshot.root, final_paths)
    rows = [f'当前库存（{today} 现算）：**{len(snapshot.cards):,} 张概念卡**，{len(catalog["domains"])} 个域；空分类字段 {missing:,} 张。', '',
            '| 轨 | 域（卡数） |', '|---|---|']
    for track in catalog['tracks']:
        rows.append(f'| {track["title"]} | ' + '、'.join(f'{d} {counts[d]}' for d in track['domains']) + ' |')
    rows += ['', '### 目录（本轮最终路径集合）', '', '| 目录 | 文件数 |', '|---|---|']
    rows += [f'| `{d}/` | {directory_counts[d]:,} |' for d in directory_counts]
    rows += ['', '> 现值用 `secondbrain status` 查询；完整卡片/策展YAML是真源。']
    stats = '\n'.join(rows)
    home = ['# HOME — SecondBrain', '', '## 现在做什么', '',
        f'1. [[{lay.reviews}/今日复习-{today}|打开今日复习]]；清单缺失时运行 `secondbrain today --apply`。',
        '2. 合上笔记复述，再打开卡片核对，用 Spaced Repetition 插件评级。',
        '3. 新资料用 `secondbrain ingest <文件>`；未解决任务用 `secondbrain status`。', '',
        ' · '.join(f'[[{name[:-3]}]]' for name in lay.entry_docs), '', '## 领域入口', '']
    seen = set()
    for track in catalog['tracks']:
        home.extend([f'## {track["title"]}', ''])
        for d in track['domains']:
            title = catalog['domains'][d]['moc']
            if title not in seen:
                seen.add(title)
                total = sum(counts[x] for x, m in catalog['domains'].items() if m['moc'] == title)
                home.append(f'- [[{lay.mocs}/{title}|{title}]] · {total} 张')
        home.append('')
    home += ['## 库存', '', stats, '', '## 录入与维护', '',
        '`secondbrain ingest <文件>`：完整来源 → 分卡与去重 → 分类/关系 → 独立复核 → 事务写入 → MOC。', '',
        '`secondbrain classify --domain <域>` 处理分类；`secondbrain resume --job <任务>` 恢复；`secondbrain refresh` 预览MOC变化。', '',
        '缺主题或领域的内容保留具体待办；计数下降不代替语义核验。分类与MOC不会改复习调度字段。', '']
    files = {lay.home: '\n'.join(home).encode()}
    issues = []
    documents = []
    for name in lay.entry_docs:
        raw = _read(snapshot.root, name, reads)
        if raw is None:
            issues.append({'path': name, 'code': 'missing_document', 'detail': '文档缺失，未宣称docs完成'})
            continue
        try:
            text = raw.decode()
        except UnicodeDecodeError:
            issues.append({'path': name, 'code': 'invalid_document_encoding', 'detail': '文档不是UTF-8，保留原文'})
            continue
        for marker, allow_zero, code in [
                ('kb-stats', False, 'missing_or_duplicate_stats_markers'),
                ('secondbrain-entry', True, 'missing_or_duplicate_entry_markers'),
                ('kb-dirs', True, 'missing_or_duplicate_dirs_markers')]:
            valid, detail = _marker_status(text, marker, allow_zero=allow_zero)
            if not valid:
                issues.append({'path': name, 'code': code, 'detail': detail})
        documents.append((name, text))

    # Validate every document before producing any document bytes.  A malformed
    # peer must not allow a valid peer's controlled block to be persisted.
    marker_codes = {'missing_or_duplicate_stats_markers',
                    'missing_or_duplicate_entry_markers',
                    'missing_or_duplicate_dirs_markers'}
    if not any(issue['code'] in marker_codes for issue in issues):
        migration = '\n'.join(ENTRY_BLOCK)
        for name, text in documents:
            stats_pattern = r'(?s)(<!-- GENERATED:BEGIN kb-stats -->).*?(<!-- GENERATED:END kb-stats -->)'
            text = re.sub(stats_pattern, lambda m: m[1] + '\n<!-- 由secondbrain从完整库存生成 -->\n\n' + stats + '\n' + m[2], text, count=1)
            dirs_pattern = r'(?s)(<!-- GENERATED:BEGIN kb-dirs -->).*?(<!-- GENERATED:END kb-dirs -->)'
            if re.search(dirs_pattern, text):
                dirs = ['| 目录 | 现有文件（现算） |', '|---|---|']
                dirs += [f'| `{d}/` | {directory_counts[d]:,} |' for d in directory_counts]
                text = re.sub(dirs_pattern, lambda m: m[1] + '\n\n' + '\n'.join(dirs) + '\n\n' + m[2], text, count=1)
            migration = '\n'.join(ENTRY_BLOCK).encode()
            candidate = _replace_or_insert_entry(text.encode(), migration)
            _validate_generated_document(candidate, name)
            files[name] = candidate
    return Projection(files, {'cards': len(snapshot.cards), 'domains': len(catalog['domains']), 'directories': directory_counts}, issues)


def _layout(snapshot):
    from . import layout as layouts
    return getattr(snapshot, 'layout', None) or layouts.of(snapshot.root)


def _merge_projections(projections):
    files, issues, metrics = {}, [], {}
    for projection in projections:
        for path, data in projection.files.items():
            if path in files:
                raise ValueError(f'投影路径重复：{path}')
            files[path] = data
        issues.extend(projection.issues)
        metrics.update(projection.metrics)
    return files, issues, metrics


def _projection_files(snapshot, today, *, reviews, entries, refresh_mocs=False, generated_at=None):
    reads = {}
    lay = _layout(snapshot)
    moc = build_mocs(snapshot) if refresh_mocs else None
    review_targets = ({f'{lay.reviews}/今日复习-{today}.md',
                       f'{lay.projections}/review_state_{today}.json',
                       f'{lay.projections}/kb_review.json'} if reviews else set())
    planned = set(moc.files) if moc else set()
    planned.update(review_targets)
    if entries:
        planned.add(lay.home)
    entry = entry_projection(snapshot, today, final_paths=planned, reads=reads) if entries else None
    entry_targets = set(entry.files) if entry else set()
    review = review_projection(snapshot, today,
                               extra_targets=tuple(sorted((set(moc.files) if moc else set()) |
                                                          entry_targets | review_targets)),
                               generated_at=generated_at, reads=reads) if reviews else None
    projections = [p for p in (moc, entry, review) if p is not None]
    files, issues, metrics = _merge_projections(projections)
    return files, issues, reads, metrics


def _projection_changes(root, files, reads):
    changes = []
    for rel, data in sorted(files.items()):
        before = reads.get(rel)
        if rel not in reads:
            before = read_bytes(root, rel)
            reads[rel] = before
        if before != data:
            from .store import Change
            changes.append(Change(rel, None if before is None else digest(before), data))
    return changes


def generate(root: Path, *, job_id: str, apply: bool = False, today: date | None = None,
             reviews: bool = True, entries: bool = True, refresh_mocs: bool | None = None,
             generated_at=None):
    """Preview or apply deterministic review/entry/MOC projections."""
    terminal = {'applied', 'published', 'partial', 'rolled_back'}
    existing = None
    frozen = None
    if apply:
        try:
            existing = jobs.load(root, job_id)
        except ValueError:
            pass
        if existing and existing.get('commit_intent') and existing.get('status') not in terminal:
            return jobs.resume_transaction(root, job_id)
        if existing and existing.get('status') in terminal:
            frozen = jobs.read_input(root, job_id)
            requested_kind = 'daily' if reviews and entries else ('today' if reviews else 'docs')
            requested_refresh = bool(refresh_mocs) if refresh_mocs is not None else False
            frozen_options = frozen.get('options') or {'reviews': True, 'entries': True}
            if (frozen.get('projection') != requested_kind or
                    bool(frozen_options.get('reviews', True)) != bool(reviews) or
                    bool(frozen_options.get('entries', True)) != bool(entries) or
                    (today is not None and frozen.get('date') != today.isoformat()) or
                    bool(frozen_options.get('refresh_mocs', False)) != requested_refresh or
                    (generated_at is not None and frozen.get('generated_at') != generated_at)):
                raise Conflict('任务ID已绑定其他输入')
            return jobs.resume_transaction(root, job_id) or existing
        if existing and existing.get('status') == 'prepared' and not existing.get('commit_intent'):
            # Read the durable packet before making a fresh clock value or calling
            # create; this makes a same-argv retry replay the prepared plan.
            frozen = jobs.read_input(root, job_id)
            requested_kind = 'daily' if reviews and entries else ('today' if reviews else 'docs')
            if frozen.get('projection') != requested_kind:
                raise Conflict('准备任务的投影类型已变化，拒绝重建旧输入')
            frozen_options = frozen.get('options') or {'reviews': True, 'entries': True}
            requested_options = {'reviews': reviews, 'entries': entries}
            if refresh_mocs is not None:
                requested_options['refresh_mocs'] = bool(refresh_mocs)
            for key, value in requested_options.items():
                if bool(frozen_options.get(key, False)) != bool(value):
                    raise Conflict('准备任务的options已变化，拒绝重建旧输入')
            if today is not None and frozen.get('date') != today.isoformat():
                raise Conflict('准备任务的date已变化，拒绝重建旧输入')
            if generated_at is not None and frozen.get('generated_at') != generated_at:
                raise Conflict('准备任务的generated_at已变化，拒绝重建旧输入')
            today = date.fromisoformat(frozen['date'])
            generated_at = frozen.get('generated_at') or datetime.now().astimezone().isoformat(timespec='seconds')
            refresh_mocs = bool(frozen_options.get('refresh_mocs', False))

    s = read_snapshot(root)
    writable_snapshot(s)
    today = today or date.today()
    generated_at = generated_at or datetime.now().astimezone().isoformat(timespec='seconds')
    refresh_mocs = bool(refresh_mocs)
    sub_kind = 'daily' if reviews and entries else ('today' if reviews else 'docs')
    packet = {'kind': jobs.PROJECTION_KIND, 'projection': sub_kind, 'date': today.isoformat(),
              'generated_at': generated_at,
              'options': {'reviews': reviews, 'entries': entries},
              'snapshot_fingerprint': s.fingerprint}
    if refresh_mocs:
        packet['options']['refresh_mocs'] = True
    if not apply:
        files, issues, reads, _ = _projection_files(s, today, reviews=reviews, entries=entries,
                                                     refresh_mocs=refresh_mocs, generated_at=generated_at)
        changes = _projection_changes(root, files, reads)
        return {'status': 'planned', 'date': str(today), 'paths': list(files),
                'changed_files': len(changes), 'issues': issues}

    if frozen is None:
        state = jobs.create(root, job_id, jobs.PROJECTION_KIND, packet)
        if state.get('commit_intent') or state['status'] != 'prepared':
            return jobs.resume_transaction(root, job_id) or state
        frozen = jobs.read_input(root, job_id)
    if frozen.get('snapshot_fingerprint') and frozen['snapshot_fingerprint'] != s.fingerprint:
        raise Conflict('准备任务的snapshot fingerprint已变化，拒绝重建旧输入')
    # Existing prepared input is authoritative; never rebuild it with new options.
    today = date.fromisoformat(frozen['date'])
    generated_at = frozen.get('generated_at') or generated_at
    options = frozen.get('options') or {'reviews': True, 'entries': True, 'refresh_mocs': False}
    files, issues, reads, _ = _projection_files(s, today, reviews=options.get('reviews', True),
                                                 entries=options.get('entries', True),
                                                 refresh_mocs=options.get('refresh_mocs', False),
                                                 generated_at=generated_at)
    changes = _projection_changes(root, files, reads)
    checks = snapshot_checks(s, changes)
    _record_checks(checks, reads, exclude={c.relative_path for c in changes})
    result = {'status': 'applied', 'date': str(today), 'paths': [c.relative_path for c in changes],
              'changed_files': len(changes), 'issues': issues}
    return jobs.freeze_projection(root, job_id, changes, checks=checks, result=result)

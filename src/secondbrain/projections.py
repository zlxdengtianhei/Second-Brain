"""HOME, entry-document blocks and the daily review page; no model calls."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import jobs, labels
from .classification import projection_changes, writable_snapshot, snapshot_checks
from .moc import Projection, build_mocs
from .store import Conflict, digest, read_bytes
from .vault import read_snapshot


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
            raise Conflict(f'A projection input changed while it was read: {rel}')
        checks[rel] = observed


_CONTROLLED_MARKERS = ('kb-stats', 'secondbrain-entry', 'kb-dirs')


def entry_block(lang: str | None = None) -> list[str]:
    """The controlled command block kept up to date inside the configured entry documents."""
    return ['<!-- GENERATED:BEGIN secondbrain-entry -->', *labels.pages(lang)['entry_block'],
            '<!-- GENERATED:END secondbrain-entry -->']


def review_page(lay, today) -> str:
    """Vault path of the daily review page for ``today`` (name follows the language)."""
    return f'{lay.reviews}/{labels.pages()["review_page"].format(date=today)}.md'


def _scan_markers(text):
    """Validate all known markers in their document-wide token order."""
    token_re = r'<!-- GENERATED:(BEGIN|END) (' + '|'.join(
        re.escape(name) for name in _CONTROLLED_MARKERS) + r') -->'
    active = None
    for match in re.finditer(token_re, text):
        kind, name = match.group(1), match.group(2)
        if kind == 'BEGIN':
            if active is not None:
                return False, f'{name}: generated-block markers are nested (open block: {active})'
            active = name
        elif active != name:
            return False, f'{name}: generated-block markers are incomplete (open block: {active})'
        else:
            active = None
    if active is not None:
        return False, f'{active}: generated-block markers are incomplete'
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
        return False, f'{name}: wrong number of generated-block markers (BEGIN={begins}, END={ends})'
    return True, None


def _replace_or_insert_entry(raw: bytes, migration: bytes) -> bytes:
    begin = b'<!-- GENERATED:BEGIN secondbrain-entry -->'
    end = b'<!-- GENERATED:END secondbrain-entry -->'
    start = raw.find(begin)
    if start >= 0:
        stop = raw.find(end, start)
        if stop < 0:
            raise Conflict('The secondbrain-entry block is incomplete')
        stop += len(end)
        return raw[:start] + migration + raw[stop:]
    stats_end = b'<!-- GENERATED:END kb-stats -->'
    stop = raw.find(stats_end)
    if stop < 0:
        raise Conflict('No kb-stats block, so there is no place to insert the secondbrain-entry block')
    stop += len(stats_end)
    return raw[:stop] + migration + raw[stop:]


def _validate_generated_document(raw: bytes, name: str) -> None:
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise Conflict(f'{name}: generated text is not UTF-8') from exc
    valid, detail = _scan_markers(text)
    if not valid:
        raise Conflict(f'{name}: generated text has invalid markers: {detail}')
    for marker, allow_zero in [('kb-stats', False), ('secondbrain-entry', False), ('kb-dirs', True)]:
        valid, detail = _marker_status(text, marker, allow_zero=allow_zero)
        if not valid:
            raise Conflict(f'{name}: generated text has a wrong number of markers: {detail}')


def planned_paths(changes) -> tuple[set, set]:
    """(paths a change set writes, paths it removes), for counting the vault as the write leaves it."""
    return ({c.relative_path for c in changes if c.after is not None},
            {c.relative_path for c in changes if c.after is None})


def _directory_counts(root, final_paths, removed=()):
    """Count the files of each folder as they will be: on disk, plus planned writes, minus removals."""
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
    final = (existing | set(final_paths)) - set(removed)
    for directory in directories:
        prefix = directory + '/'
        counts[directory] = len({p for p in final if p.startswith(prefix)})
    return counts


def current_day() -> date:
    """The day the generated pages are for: the current review day (starts at 04:00), else the calendar date."""
    from .reviews import learning_date
    return learning_date()


def review_time(today: date) -> datetime:
    """The moment the daily review page is computed for: noon of ``today``, local time."""
    return datetime.combine(today, time(12), tzinfo=datetime.now().astimezone().tzinfo)


def review_projection(snapshot, today: date, *, extra_targets=(), generated_at=None,
                      reads=None) -> Projection:
    """Today's review page from the review registry: the same due and new cards as `sb review queue`."""
    from . import reviews
    reads = {} if reads is None else reads
    lay = _layout(snapshot)
    now = review_time(today)
    queue = reviews.queue(snapshot.root, now, snapshot=snapshot)
    due = [{'id': r['id'], 'title': r['title'], 'path': r['path'][:-3], 'domain': r['domain'],
            'due': reviews.local_day(r['due'], now.tzinfo)} for r in queue['cards'] if r['kind'] == 'due']
    new = [{'id': r['id'], 'title': r['title'], 'path': r['path'][:-3], 'domain': r['domain']}
           for r in queue['cards'] if r['kind'] == 'new']
    issues = ([{'code': 'learning_order_cycle', 'count': queue['cycle_breaks'],
                'detail': 'Prerequisite edges among new cards form a cycle; the queue breaks it in card id order'}]
              if queue['cycle_breaks'] else [])
    counts = {'scanned': len(snapshot.cards), 'due': len(due), 'new': len(new),
              'new_available': queue['new_available'], 'new_quota': queue['new_quota']}
    state = {'date': today.isoformat(), 'source': 'review registry', 'due': due, 'new': new,
             'counts': counts, 'issues': issues, 'generated_by': 'secondbrain'}
    T = labels.pages()
    lines = ['---', f'date: {today.isoformat()}', 'generated_by: secondbrain', '---', '',
             T['review_title'].format(date=today.isoformat()), '',
             T['review_intro'], '', f'[[{lay.home[:-3]}|{T["back_home"]}]]', '']
    for title, rows, label in [(T['due_cards'], due, 'due'), (T['new_today'], new, None)]:
        lines.extend([f'## {title}', ''])
        for r in rows:
            detail = f' · {r[label]}' if label else ''
            lines.append(f'- [[{r["path"]}|{r["title"]}]]{detail}')
        if not rows:
            lines.append(T['none'])
        lines.append('')
    lines.extend([T['new_left'].format(available=queue['new_available'], n=len(new), date=today.isoformat(),
                                       hour=reviews.day_start_hour()), ''])
    trend = []
    for i in range(6, -1, -1):
        rel = f'{lay.projections}/review_state_{(today - timedelta(days=i)).isoformat()}.json'
        old = _read(snapshot.root, rel, reads)
        value = state if i == 0 else json.loads(old) if old else None
        if value:
            # older pages stored Spaced Repetition counts as due_notes / due_inline
            count = len(value.get('due', value.get('due_notes', []))) + len(value.get('due_inline', []))
            trend.append({'date': (today - timedelta(days=i)).isoformat(), 'due_count': count})
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
            'due_count': len(due), 'new_count': len(new),
            'due_top': [{'title': x['title'], 'due': x['due']} for x in due[:10]], 'trend_7d': trend}
    return Projection({review_page(lay, today): '\n'.join(lines).encode(),
                       f'{lay.projections}/review_state_{today}.json': jobs.encoded(state),
                       f'{lay.projections}/kb_review.json': jobs.encoded(site)}, counts, issues)


def entry_projection(snapshot, today: date, *, final_paths=(), removed_paths=(), reads=None) -> Projection:
    """HOME and the generated blocks of the entry docs.

    ``snapshot`` is the vault as the current write leaves it; ``final_paths``
    and ``removed_paths`` are the files that write creates and removes, so the
    folder table counts the same state as the card count.
    """
    reads = {} if reads is None else reads
    lay = _layout(snapshot)
    counts = Counter(c.domain for c in snapshot.cards.values())
    missing = sum(not c.fm.get('pillar') or not c.fm.get('topic') for c in snapshot.cards.values())
    catalog = snapshot.catalog
    directory_counts = _directory_counts(snapshot.root, final_paths, removed_paths)
    T = labels.pages()
    rows = [T['inventory'].format(date=today, cards=len(snapshot.cards), domains=len(catalog['domains']),
                                  missing=missing), '', *T['track_table']]
    for track in catalog['tracks']:
        rows.append(f'| {track["title"]} | ' + T['domain_sep'].join(f'{d} {counts[d]}' for d in track['domains']) + ' |')
    rows += ['', T['dirs_title'], '', *T['dirs_table']]
    rows += [f'| `{d}/` | {directory_counts[d]:,} |' for d in directory_counts]
    rows += ['', T['stats_note']]
    stats = '\n'.join(rows)
    from .reviews import available as reviews_available
    page = review_page(lay, today)
    # link the day's review page only when it exists or this write makes it, so the link resolves;
    # otherwise name the command that makes the page and rewrites this line (`sb daily`)
    made = page in set(final_paths) or (snapshot.root is not None and (snapshot.root / page).is_file())
    if made:
        review_line = T['review_link'].format(review=page[:-3], date=today.isoformat())
    elif reviews_available():
        review_line = T['review_missing'].format(date=today.isoformat())
    else:
        review_line = T['review_no_extra'].format(install=labels.install_hint('review', labels.language()))
    home = [line.format(inbox=lay.inbox, review_link=review_line) for line in T['home']]
    waiting = sum(c.fm.get('status') == 'needs-review' for c in snapshot.cards.values())
    if waiting:  # they never reach the review queue unless the owner looks at them
        home.append(T['needs_review'].format(n=waiting))
    home.append('')
    if lay.entry_docs:
        home += [' · '.join(f'[[{name[:-3]}]]' for name in lay.entry_docs), '']
    home += [T['domains_title'], '']
    seen = set()
    for track in catalog['tracks']:
        home.extend([f'## {track["title"]}', ''])
        for d in track['domains']:
            title = catalog['domains'][d]['moc']
            if title not in seen:
                seen.add(title)
                total = sum(counts[x] for x, m in catalog['domains'].items() if m['moc'] == title)
                home.append(T['map_line'].format(path=f'{lay.mocs}/{title}', title=title, n=total))
        home.append('')
    home += [T['stats_title'], '', stats, '', *T['maintenance']]
    files = {lay.home: '\n'.join(home).encode()}
    issues = []
    documents = []
    for name in lay.entry_docs:
        raw = _read(snapshot.root, name, reads)
        if raw is None:
            issues.append({'path': name, 'code': 'missing_document',
                           'detail': 'Document missing; its generated blocks were not refreshed'})
            continue
        try:
            text = raw.decode()
        except UnicodeDecodeError:
            issues.append({'path': name, 'code': 'invalid_document_encoding', 'detail': 'Document is not UTF-8; left unchanged'})
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
        for name, text in documents:
            stats_pattern = r'(?s)(<!-- GENERATED:BEGIN kb-stats -->).*?(<!-- GENERATED:END kb-stats -->)'
            text = re.sub(stats_pattern, lambda m: m[1] + '\n' + T['stats_marker'] + '\n\n' + stats + '\n' + m[2],
                          text, count=1)
            dirs_pattern = r'(?s)(<!-- GENERATED:BEGIN kb-dirs -->).*?(<!-- GENERATED:END kb-dirs -->)'
            if re.search(dirs_pattern, text):
                dirs = list(T['dirs_live_table'])
                dirs += [f'| `{d}/` | {directory_counts[d]:,} |' for d in directory_counts]
                text = re.sub(dirs_pattern, lambda m: m[1] + '\n\n' + '\n'.join(dirs) + '\n\n' + m[2], text, count=1)
            migration = '\n'.join(entry_block()).encode()
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
                raise ValueError(f'Two projections write the same path: {path}')
            files[path] = data
        issues.extend(projection.issues)
        metrics.update(projection.metrics)
    return files, issues, metrics


def _projection_files(snapshot, today, *, reviews, entries, refresh_mocs=False, generated_at=None):
    from .reviews import available as reviews_available
    reads = {}
    lay = _layout(snapshot)
    skipped = []
    if reviews and entries and not reviews_available():
        # `sb daily` without the review extra: write HOME and the docs, say why the review page is missing.
        reviews = False
        skipped.append({'code': 'review_extra_missing',
                        'detail': f'The daily review page needs the review extra: {labels.install_hint("review")}'})
    moc = build_mocs(snapshot) if refresh_mocs else None
    review_targets = ({review_page(lay, today),
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
    return files, skipped + issues, reads, metrics


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
                raise Conflict('This job id is already bound to other input')
            return jobs.resume_transaction(root, job_id) or existing
        if existing and existing.get('status') == 'prepared' and not existing.get('commit_intent'):
            # Read the durable packet before making a fresh clock value or calling
            # create; this makes a same-argv retry replay the prepared plan.
            frozen = jobs.read_input(root, job_id)
            requested_kind = 'daily' if reviews and entries else ('today' if reviews else 'docs')
            if frozen.get('projection') != requested_kind:
                raise Conflict('The prepared job is for another projection kind; not rebuilding its input')
            frozen_options = frozen.get('options') or {'reviews': True, 'entries': True}
            requested_options = {'reviews': reviews, 'entries': entries}
            if refresh_mocs is not None:
                requested_options['refresh_mocs'] = bool(refresh_mocs)
            for key, value in requested_options.items():
                if bool(frozen_options.get(key, False)) != bool(value):
                    raise Conflict('The prepared job has other options; not rebuilding its input')
            if today is not None and frozen.get('date') != today.isoformat():
                raise Conflict('The prepared job has another date; not rebuilding its input')
            if generated_at is not None and frozen.get('generated_at') != generated_at:
                raise Conflict('The prepared job has another generated_at; not rebuilding its input')
            today = date.fromisoformat(frozen['date'])
            generated_at = frozen.get('generated_at') or datetime.now().astimezone().isoformat(timespec='seconds')
            refresh_mocs = bool(frozen_options.get('refresh_mocs', False))

    s = read_snapshot(root)
    writable_snapshot(s)
    # one day for every generated page: the review day, as `sb review queue` counts it
    today = today or current_day()
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
        raise Conflict('The vault changed since the job was prepared (snapshot fingerprint); not rebuilding its input')
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

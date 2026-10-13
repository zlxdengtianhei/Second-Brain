"""Inbox scan (S0) and the inbox registry.

The registry (``<data>/secondbrain/inbox_registry.jsonl``) is an append-only
log of sources the tool has finished with: ``applied``, ``partial`` or
``skipped``. A file counts as done when its sha256 appears in that log, in
the legacy ``ingested.jsonl`` or ``manifest.jsonl`` files, or in an ingest job
that reached a terminal state.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import jobs
from . import layout as layouts
from .store import Change, apply_changes, digest, read_bytes

TERMINAL_DONE = {'applied', 'partial', 'published'}


def job_id_for(sha256: str) -> str:
    return f'ingest-{sha256[:16]}'


def _jsonl(root: Path, rel: str) -> list[dict]:
    raw = read_bytes(root, rel)
    if not raw:
        return []
    rows = []
    for line in raw.decode('utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def registry_rows(root: Path) -> list[dict]:
    return _jsonl(root, layouts.of(root).inbox_registry)


def done_index(root: Path) -> dict:
    """sha256 -> how it was finished (latest registry row wins)."""
    lay = layouts.of(root)
    done = {}
    for row in _jsonl(root, lay.ingested):
        if isinstance(row.get('sha256'), str):
            done[row['sha256']] = {'status': 'done', 'via': 'ingested.jsonl'}
    for row in _jsonl(root, lay.manifest):
        if isinstance(row.get('sha256'), str):
            done.setdefault(row['sha256'], {'status': 'done', 'via': 'manifest.jsonl'})
    for row in registry_rows(root):
        if isinstance(row.get('sha256'), str):
            done[row['sha256']] = {'status': row.get('status'), 'via': 'inbox_registry',
                                   'job_id': row.get('job_id'), 'reason': row.get('reason')}
    return done


def _job_status(root: Path, job_id: str) -> str | None:
    try:
        return jobs.load(root, job_id)['status']
    except (ValueError, OSError):
        return None


def scan(root: Path) -> list[dict]:
    """Every top-level inbox file with its status (read only)."""
    lay = layouts.of(root)
    folder = Path(root) / lay.inbox
    if not folder.is_dir():
        return []
    done = done_index(root)
    items = []
    for name in sorted(os.listdir(folder)):
        path = folder / name
        rel = f'{lay.inbox}/{name}'
        if name.startswith('.') or path.is_symlink() or not path.is_file():
            continue
        item = {'path': rel, 'name': name, 'size': None, 'sha256': None, 'status': 'new', 'job_id': None}
        try:
            raw = read_bytes(root, rel)
        except (OSError, ValueError) as exc:
            items.append({**item, 'status': 'failed', 'reason': str(exc)})
            continue
        if raw is None:
            continue
        sha = digest(raw)
        item.update(size=len(raw), sha256=sha, job_id=job_id_for(sha))
        record = done.get(sha)
        status = _job_status(root, item['job_id'])
        if record and record['status'] == 'skipped':
            item.update(status='skipped', reason=record.get('reason'))
        elif record:
            item.update(status='done', via=record['via'], result=record['status'])
        elif status in TERMINAL_DONE:
            item.update(status='done', via='job', result=status)
        elif status == 'failed':
            item.update(status='failed', reason='ingest job failed; see sb job show')
        elif status is not None:
            item.update(status='in_progress', job_status=status)
        items.append(item)
    return items


def find(root: Path, ref: str) -> dict:
    """An inbox item by file name, inbox-relative path or sha256 prefix."""
    matches = [i for i in scan(root) if ref in {i['name'], i['path']}
               or (i['sha256'] and len(ref) >= 8 and i['sha256'].startswith(ref))]
    if len(matches) != 1:
        raise ValueError(f'No unique inbox item matches {ref!r}')
    return matches[0]


def registry_line(row: dict) -> bytes:
    return (json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8')


def appended(root: Path, rel: str, lines: bytes) -> Change:
    before = read_bytes(root, rel)
    prefix = before or b''
    if prefix and not prefix.endswith(b'\n'):
        prefix += b'\n'
    return Change(rel, None if before is None else digest(before), prefix + lines)


def skip(root: Path, ref: str, reason: str) -> dict:
    """Record that an inbox item should not become cards."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('skip needs a reason')
    item = find(root, ref)
    if item['status'] in {'done', 'skipped'}:
        return {**item, 'changed': False}
    row = {'sha256': item['sha256'], 'path': item['path'], 'status': 'skipped', 'reason': reason.strip(),
           'job_id': None, 'at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    lay = layouts.of(root)
    apply_changes(root, f'inbox-skip-{item["sha256"][:16]}',
                  [appended(root, lay.inbox_registry, registry_line(row))])
    return {**item, 'status': 'skipped', 'reason': row['reason'], 'changed': True}

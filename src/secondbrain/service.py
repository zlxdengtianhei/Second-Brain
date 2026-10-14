"""Small shared service used by terminal commands and the MCP adapter."""
from __future__ import annotations

import json
import uuid
from collections import Counter
from pathlib import Path

from . import classification, jobs
from .catalog import validate_catalog
from .store import Change, apply_changes, digest, read_bytes
from .vault import card_packet, read_snapshot


class Service:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        self._snapshot = None

    def snapshot(self):
        # One full, immutable read view per MCP process. Writes always reread.
        if self._snapshot is None:
            self._snapshot = read_snapshot(self.root)
        return self._snapshot

    def status(self, job_id: str | None = None):
        if job_id:
            return jobs.load(self.root, job_id)
        s = self.snapshot()
        missing = Counter(c.domain for c in s.cards.values()
                          if not c.fm.get('pillar') or not c.fm.get('topic'))
        from . import inbox, reviews
        from . import layout as layouts
        from .moc import build_mocs
        items = inbox.scan(self.root)
        inbox_counts = Counter(item['status'] for item in items)
        try:
            review = reviews.status(self.root, reviews.now_local())['totals']
        except (ValueError, OSError) as exc:
            review = {'unavailable': str(exc)}
        issues, map_issues = list(s.issues), Counter()
        if s.catalog:
            try:
                validate_catalog(s.catalog)
            except ValueError as exc:
                issues.append({'code': 'catalog_invalid', 'path': layouts.of(self.root).catalog, 'detail': str(exc)})
            else:  # what `sb refresh` reports about the maps (counts only; it lists each one)
                map_issues = Counter(i['code'] for i in build_mocs(s).issues)
        from .pipelines.ingest import open_conflicts
        conflicts = open_conflicts(self.root, s)
        waiting = self.needs_review()
        return {'cards': len(s.cards), 'missing_classification': sum(missing.values()),
                'missing_by_domain': dict(sorted(missing.items())), 'issues': issues,
                'map_issues': dict(sorted(map_issues.items())),
                **({'map_issues_hint': 'sb refresh lists each one'} if map_issues else {}),
                'snapshot_fingerprint': s.fingerprint, 'inbox': dict(sorted(inbox_counts.items())),
                'pending_items': sum(i.get('pending') or 0 for i in items if i['status'] == 'partial'),
                'conflicts': conflicts,
                'needs_review': len(waiting),
                **({'needs_review_hint': 'these cards stay out of the review queue until their status is confirmed; '
                                         'sb status --needs-review lists them'} if waiting else {}),
                'state_dir': _state_size(self.root),
                **({'conflicts_hint': 'new and existing cards disagree here; each stays listed until the existing '
                                      f'card\'s text changes ({layouts.of(self.root).conflicts})'} if conflicts else {}),
                'review': review, 'jobs': jobs.list_jobs(self.root)}

    def needs_review(self) -> list[dict]:
        """Every card marked ``status: needs-review``; the review queue leaves them out until they are confirmed."""
        return [{'id': c.id, 'title': c.title, 'path': c.path}
                for c in sorted(self.snapshot().cards.values(), key=lambda c: c.path)
                if c.fm.get('status') == 'needs-review']

    def job_show(self, job_id: str) -> dict:
        state = jobs.load(self.root, job_id)
        if state.get('status') == 'archived':  # run records keep the original job id
            return state
        return {**state, 'run_records': jobs.list_runs(self.root, job_id)}

    def job_packet(self, job_id: str, role: str, chunk: str | None = None) -> dict:
        """Create the next run for ``role`` and return its hand-off (packet file, run id, submit command)."""
        from .executors.host import HostExecutor
        host = HostExecutor.for_vault(self.root)
        if jobs.load(self.root, job_id)['kind'] == 'ingest':
            from .pipelines.ingest import packet_for
            return packet_for(self.root, job_id, role=role, chunk=chunk, executor=host)
        from .runner import run_job
        result = run_job(self.root, job_id, producer=host, reviewer=host, apply=False)
        if result.get('status') != 'awaiting_submission':
            raise ValueError('No model step is due for this job')
        return result['handoff']

    def job_advance(self, job_id: str, *, producer, reviewer, apply: bool = True) -> dict:
        """Run every step that can run now; writes knowledge when the gate passes and ``apply``."""
        kind = jobs.load(self.root, job_id)['kind']
        self._snapshot = None
        if kind == 'ingest':
            from .pipelines.ingest import advance
            return advance(self.root, job_id, producer=producer, reviewer=reviewer, apply=apply)
        if kind == 'classification-batch':
            from .backlog import run as run_batch
            return run_batch(self.root, job_id, producer=producer, reviewer=reviewer, apply=apply)
        from .runner import run_job
        return run_job(self.root, job_id, producer=producer, reviewer=reviewer, apply=apply)

    def read_cards(self, ids: list[str]):
        if len(ids) > 50 or len(ids) != len(set(ids)):
            raise ValueError('read 1-50 distinct card ids at a time')
        s = self.snapshot()
        if any(i not in s.cards for i in ids):
            raise ValueError('unknown card id')
        return [card_packet(s.cards[i]) for i in ids]

    def read_task(self, job_id: str, part: str = 'overview', offset: int = 0, limit: int = 20):
        packet = jobs.read_input(self.root, job_id)
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError('offset must be >= 0 and limit 1-100')
        if part == 'overview':
            return {k: v for k, v in packet.items() if k not in {'cards', 'taxonomy', 'catalog', 'source', 'neighbors', 'repair_feedback'}} | {
                'card_count': len(packet.get('cards', []))}
        if part == 'cards':
            cards = packet.get('cards', [])
            return {'total': len(cards), 'offset': offset, 'cards': cards[offset:offset + limit]}
        if part in {'taxonomy', 'catalog', 'neighbors'}:
            if part not in packet:
                raise ValueError(f'this {packet["kind"]} job has no {part} part')
            return packet[part]
        if part in {'candidate', 'review'}:
            return jobs.artifact(self.root, jobs.load(self.root, job_id), part)
        raise ValueError('part must be overview, cards, taxonomy, catalog, neighbors, candidate or review')

    def search(self, query: str, limit: int = 12):
        from .index import search
        if not query.strip() or not 1 <= limit <= 50:
            raise ValueError('query must not be empty and limit must be 1-50')
        return search(self.root, self.snapshot(), query, limit=limit)

    def submit(self, job_id: str, payload: dict, *, role: str, run_id: str,
               model: str | None = None):
        """Submit a host-mode run output; validated, never written to knowledge."""
        return jobs.submit(self.root, job_id, payload, role=role, run_id=run_id,
                           model_reported=model, attestation='self_reported')


def _state_size(root: Path) -> dict:
    """Where the vault's job records and caches are, and their size."""
    import os
    from .store import state_root
    try:
        base = state_root(root)
    except ValueError as exc:
        return {'error': str(exc)}
    total = 0
    for folder, _, names in os.walk(base):
        for name in names:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
    return {'path': str(base), 'mb': round(total / 1e6, 1),
            'hint': 'rollback journals of old writes take most of it; sb job prune shows which can go'}


def initialize(root: Path, catalog: dict, job_id: str = 'initialize-catalog'):
    validate_catalog(catalog)
    if 'language' not in catalog:
        # Record the vault's generated-text language: configured, else found in existing maps, else English.
        from . import config, labels
        settings = config.current()
        language = settings.card_language if settings.card_language_set else (
            labels.detected_language(root) or settings.card_language)
        catalog = {**catalog, 'language': language}
    raw = jobs.encoded(catalog)
    from . import layout as layouts
    lay = layouts.of(root)
    existing = read_bytes(root, lay.catalog)
    if existing is not None and existing != raw:
        raise ValueError('a catalog exists already; change domains through a reviewed proposal')
    # what an ingest needs besides the catalog: an empty card registry and the card, taxonomy and inbox folders
    changes = [] if existing is not None else [Change(lay.catalog, None, raw)]
    if read_bytes(root, lay.registry) is None:
        changes.append(Change(lay.registry, None, jobs.encoded({'notes': []})))
    folders = [d for d in (lay.knowledge, lay.taxonomy, lay.inbox) if not (Path(root) / d).is_dir()]
    if changes:
        if existing is not None:  # an older `sb init` wrote the catalog only
            job_id = f'{job_id}-{uuid.uuid4().hex[:12]}'
        result = apply_changes(root, job_id, changes)
    else:
        result = {'status': 'unchanged'}
    for folder in folders:
        (Path(root) / folder).mkdir(parents=True, exist_ok=True)
    # taxonomy files are the owner's to write (docs/new-vault.md); a domain without one gets no cards
    missing = sorted({lay.taxonomy_path(d['moc']) for d in catalog['domains'].values()
                      if not (Path(root) / lay.taxonomy_path(d['moc'])).is_file()})
    return {**result, 'created_folders': folders, 'taxonomy_files_missing': missing}


def set_language(root: Path, language: str, job_id: str | None = None):
    """Record ``language`` as the vault's generated-text language (the explicit way to switch it).

    Pages already written keep their text until ``sb refresh --apply`` and
    ``sb docs --apply`` rewrite them; existing cards keep their headings.
    """
    from . import labels
    from . import layout as layouts
    catalog_rel = layouts.of(root).catalog
    raw = read_bytes(root, catalog_rel)
    if raw is None:
        raise ValueError('No catalog yet; `sb init` records the language when it creates one')
    catalog = json.loads(raw)
    before = labels.recorded_language(catalog)
    if before == language:
        return {'status': 'unchanged', 'language': language}
    catalog['language'] = language
    validate_catalog(catalog)
    job_id = job_id or f'language-{language}-{uuid.uuid4().hex[:12]}'
    result = apply_changes(root, job_id, [Change(catalog_rel, digest(raw), jobs.encoded(catalog))])
    return {**result, 'language': language, 'previous': before or labels.detected_language(root),
            'next': ['sb refresh --apply', 'sb docs --apply']}


def describe_moc_changes(root: Path, changes) -> dict:
    """What a MOC refresh would change, by kind and by MOC.

    Kinds: ``new`` (page does not exist yet), ``breadcrumbs_field_only`` (the
    page differs only by the ``BC-list-note-field`` frontmatter that current
    pages carry), ``removed`` and ``content``.
    """
    from . import layout as layouts
    from .moc import BC_FRONTMATTER
    mocs = layouts.of(root).mocs + '/'
    kinds: Counter = Counter()
    by_moc: dict = {}
    for change in changes:
        before = read_bytes(root, change.relative_path)
        if before is None:
            kind = 'new'
        elif change.after is None:
            kind = 'removed'
        elif change.after == BC_FRONTMATTER.encode('utf-8') + before:
            kind = 'breadcrumbs_field_only'
        else:
            kind = 'content'
        kinds[kind] += 1
        rel = change.relative_path
        name = rel[len(mocs):].split('/', 1)[0] if rel.startswith(mocs) else rel
        moc = name[:-3] if name.endswith('.md') else name
        by_moc.setdefault(moc, Counter())[kind] += 1
    return {'kinds': dict(sorted(kinds.items())),
            'by_moc': {moc: dict(sorted(c.items())) for moc, c in sorted(by_moc.items())}}


def refresh(root: Path, *, job_id: str, domains: list[str] | None = None, apply: bool = False):
    from datetime import date
    from .moc import build_mocs
    s = read_snapshot(root)
    classification.writable_snapshot(s)
    if domains and not set(domains) <= s.catalog['domains'].keys():
        raise ValueError('unknown domain')
    if not apply:
        p = build_mocs(s, affected_domains=set(domains) if domains else None)
        changes = classification.projection_changes(root, p)
        return {'status': 'planned', 'changed_files': len(changes), 'drift': describe_moc_changes(root, changes),
                'metrics': p.metrics, 'issues': p.issues, 'paths': [c.relative_path for c in changes]}
    packet = {'kind': jobs.PROJECTION_KIND, 'projection': 'refresh', 'date': date.today().isoformat(),
              'options': {'domains': list(domains or [])}, 'snapshot_fingerprint': s.fingerprint}
    state = jobs.create(root, job_id, jobs.PROJECTION_KIND, packet)
    if state.get('commit_intent') or state['status'] != 'prepared':
        return jobs.resume_transaction(root, job_id) or state
    p = build_mocs(s, affected_domains=set(domains) if domains else None)
    changes = classification.projection_changes(root, p)
    result = {'status': 'applied', 'changed_files': len(changes), 'metrics': p.metrics,
              'issues': p.issues, 'paths': [c.relative_path for c in changes]}
    return jobs.freeze_projection(root, job_id, changes,
                                  checks=classification.snapshot_checks(s, changes), result=result)

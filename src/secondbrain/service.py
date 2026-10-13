"""Small shared service used by terminal commands and the MCP adapter."""
from __future__ import annotations

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
        inbox_counts = Counter(item['status'] for item in inbox.scan(self.root))
        try:
            review = reviews.status(self.root, reviews.now_local())['totals']
        except (ValueError, OSError) as exc:
            review = {'unavailable': str(exc)}
        return {'cards': len(s.cards), 'missing_classification': sum(missing.values()),
                'missing_by_domain': dict(sorted(missing.items())), 'issues': s.issues,
                'snapshot_fingerprint': s.fingerprint, 'inbox': dict(sorted(inbox_counts.items())),
                'review': review, 'jobs': jobs.list_jobs(self.root)}

    def read_cards(self, ids: list[str]):
        if len(ids) > 50 or len(ids) != len(set(ids)):
            raise ValueError('每次读取1–50个唯一卡ID')
        s = self.snapshot()
        if any(i not in s.cards for i in ids):
            raise ValueError('卡ID不存在')
        return [card_packet(s.cards[i]) for i in ids]

    def read_task(self, job_id: str, part: str = 'overview', offset: int = 0, limit: int = 20):
        packet = jobs.read_input(self.root, job_id)
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError('offset>=0，limit须为1–100')
        if part == 'overview':
            return {k: v for k, v in packet.items() if k not in {'cards', 'taxonomy', 'catalog', 'source', 'neighbors', 'repair_feedback'}} | {
                'card_count': len(packet.get('cards', []))}
        if part == 'cards':
            cards = packet.get('cards', [])
            return {'total': len(cards), 'offset': offset, 'cards': cards[offset:offset + limit]}
        if part in {'taxonomy', 'catalog', 'neighbors'}:
            if part not in packet:
                raise ValueError(f'此{packet["kind"]}任务没有{part}部分')
            return packet[part]
        if part in {'candidate', 'review'}:
            return jobs.artifact(self.root, jobs.load(self.root, job_id), part)
        raise ValueError('part须为overview/cards/taxonomy/catalog/neighbors/candidate/review')

    def search(self, query: str, limit: int = 12):
        from .index import search
        if not query.strip() or not 1 <= limit <= 50:
            raise ValueError('query不能为空，limit须为1–50')
        return search(self.root, self.snapshot(), query, limit=limit)

    def submit(self, job_id: str, payload: dict, *, role: str, run_id: str,
               model: str | None = None):
        """Submit a host-mode run output; validated, never written to knowledge."""
        return jobs.submit(self.root, job_id, payload, role=role, run_id=run_id,
                           model_reported=model, attestation='self_reported')


def initialize(root: Path, catalog: dict, job_id: str = 'initialize-catalog'):
    validate_catalog(catalog)
    raw = jobs.encoded(catalog)
    from . import layout as layouts
    catalog_rel = layouts.of(root).catalog
    existing = read_bytes(root, catalog_rel)
    if existing is not None:
        if existing == raw:
            return {'status': 'unchanged'}
        raise ValueError('catalog已存在；修改领域体系须走提案与复核')
    return apply_changes(root, job_id, [Change(catalog_rel, None, raw)])


def refresh(root: Path, *, job_id: str, domains: list[str] | None = None, apply: bool = False):
    from datetime import date
    from .moc import build_mocs
    s = read_snapshot(root)
    classification.writable_snapshot(s)
    if domains and not set(domains) <= s.catalog['domains'].keys():
        raise ValueError('未知领域')
    if not apply:
        p = build_mocs(s, affected_domains=set(domains) if domains else None)
        changes = classification.projection_changes(root, p)
        return {'status': 'planned', 'changed_files': len(changes), 'metrics': p.metrics,
                'issues': p.issues, 'paths': [c.relative_path for c in changes]}
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

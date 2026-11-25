"""`sb flatten`: move a map to three levels (domain, pillar, card).

Each pillar of the map loses its topics: its cards then stand directly under it (:mod:`secondbrain.levels`). The
command changes only what that takes, in one recoverable transaction: the ``topics`` lists of the taxonomy file,
the ``topic`` line of each classified card of the map (every other byte of the card stays), the ``topic`` of
their registry notes, and the generated map pages (rebuilt; a pillar page the new map no longer has is removed).
It makes no judgement of meaning, so it asks no model: the topics a card was filed under are kept in the result
(``topics_before``), and `sb recover --rollback <job>` puts every byte back. Without ``apply`` it only plans.

Cards without a valid pillar keep their frontmatter; they stay on the map's unclassified page, as before.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import replace
from pathlib import Path

from . import layout as layouts
from . import levels
from .classification import projection_changes, snapshot_checks, writable_snapshot
from .curation import _serialise_taxonomy
from .moc import BC_FRONTMATTER, _safe_segment, build_mocs
from .store import Change, apply_changes, digest, read_bytes
from .vault import DELETE, parse_note, patch_frontmatter, read_snapshot, with_topic

#: A generated pillar page's file name (``NN<stem>--NN.md``, :func:`secondbrain.moc._pillar_filename`).
_PAGE_RE = re.compile(r'^\d{2}.*--\d{2}\.md$')


def generated_page(lay, rel: str, raw: bytes | None) -> bool:
    """True for a map page the program wrote: a pillar page by name, under a map folder, with the generated head."""
    if raw is None or not lay.is_moc_page(rel):
        return False
    rest = rel[len(lay.mocs) + 1:].split('/')
    return len(rest) == 2 and bool(_PAGE_RE.match(rest[1])) and raw.startswith(BC_FRONTMATTER.encode('utf-8'))


def stale_pages(root: Path, moc: str, projection) -> list[Change]:
    """Removals of the generated pillar pages in ``moc``'s folder that ``projection`` no longer writes (a pillar the
    rebuilt map no longer has, or has under another number); a page the owner wrote stays."""
    lay = layouts.of(root)
    folder = Path(root, *lay.mocs.split('/'), _safe_segment(moc, 'moc'))
    out = []
    if folder.is_dir():
        for page in sorted(folder.iterdir()):
            prel = page.relative_to(root).as_posix()
            if prel in projection.files or not page.is_file():
                continue
            raw = read_bytes(root, prel)
            if generated_page(lay, prel, raw):
                out.append(Change(prel, digest(raw), None))
    return out


def referrers(root: Path, removed: list[str], written: dict) -> dict:
    """``{removed page: [files that name it]}``: the Markdown files of the vault, as ``written`` (path: bytes) will
    leave them, that still name a page in ``removed`` by its file name; pages without one are left out."""
    from .relocate import _iter_md
    if not removed:
        return {}
    gone = set(removed)
    names = {rel: rel.rsplit('/', 1)[-1][:-3] for rel in removed}
    found = {rel: set() for rel in removed}
    documents = dict(_iter_md(root))
    documents.update({rel: raw for rel, raw in written.items() if rel.endswith('.md')})
    for rel, raw in documents.items():
        if rel in gone:
            continue
        text = raw.decode('utf-8')
        for page, name in names.items():
            if name in text:
                found[page].add(rel)
    return {page: sorted(files) for page, files in found.items() if files}


def _job_id(moc: str, fingerprint: str) -> str:
    return 'flatten-' + digest(json.dumps([moc, fingerprint], ensure_ascii=False).encode('utf-8'))[:16]


def plan(root: Path, domain: str) -> dict:
    """The changes that move ``domain``'s map to three levels, and what they do."""
    root = Path(root)
    lay = layouts.of(root)
    snapshot = read_snapshot(root)
    writable_snapshot(snapshot)
    domains = snapshot.catalog.get('domains') or {}
    if domain not in domains:
        raise ValueError(f'unknown domain: {domain}')
    moc = domains[domain]['moc']
    sharing = sorted(d for d, m in domains.items() if m.get('moc') == moc)
    taxonomy = snapshot.taxonomies.get(moc)
    if not isinstance(taxonomy, dict) or not isinstance(taxonomy.get('pillars'), list):
        raise ValueError(f'map {moc!r} has no taxonomy file to flatten')
    flat = copy.deepcopy(taxonomy)
    topics_by_pillar = {}
    for p in flat['pillars']:
        if isinstance(p, dict) and isinstance(p.get('name'), str):
            if not levels.is_flat(p):
                topics_by_pillar[p['name']] = list(p.get('topics') or [])
            p['topics'] = [p['name']]
    pillars = {p['name']: p for p in flat['pillars'] if isinstance(p, dict) and isinstance(p.get('name'), str)}

    changes, topics_before, unclassified = [], {}, []
    rel = lay.taxonomy_path(moc)
    before = read_bytes(root, rel)
    after = _serialise_taxonomy(flat)
    if before != after:
        changes.append(Change(rel, None if before is None else digest(before), after))
    updated = dict(snapshot.cards)
    for card in sorted((c for c in snapshot.cards.values() if c.domain in sharing), key=lambda c: c.path):
        stored, _ = parse_note(card.raw)
        if stored.get('pillar') not in pillars:
            unclassified.append(card.id)
            continue
        if 'topic' not in stored:
            continue
        raw = patch_frontmatter(card.raw, {'topic': DELETE})
        fm, body = parse_note(raw)
        if body != card.body or {k: v for k, v in stored.items() if k != 'topic'} != fm:
            raise ValueError(f'removing the topic would change more of the card than its topic line: {card.id}')
        topics_before[card.id] = stored.get('topic')
        changes.append(Change(card.path, card.sha256, raw))
        updated[card.id] = replace(card, raw=raw, fm=with_topic(fm, pillars), sha256=digest(raw))
    registry_raw = read_bytes(root, lay.registry)
    if registry_raw is not None and topics_before:
        data = json.loads(registry_raw)
        notes = data.get('notes') if isinstance(data, dict) else None
        if isinstance(notes, list):
            for i, note in enumerate(notes):
                if isinstance(note, dict) and note.get('id') in topics_before and 'topic' in note:
                    notes[i] = {k: v for k, v in note.items() if k != 'topic'}
            registry_after = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
            if registry_after != registry_raw:
                changes.append(Change(lay.registry, digest(registry_raw), registry_after))
    projected = replace(snapshot, cards=updated, taxonomies={**snapshot.taxonomies, moc: flat})
    projection = build_mocs(projected, affected_domains=set(sharing))
    pages = projection_changes(root, projection)
    changes.extend(pages)
    stale = stale_pages(root, moc, projection)
    changes.extend(stale)
    removed = [c.relative_path for c in stale]
    return {'snapshot': snapshot, 'changes': changes, 'moc': moc, 'domains': sharing,
            'job_id': _job_id(moc, snapshot.fingerprint),
            'report': {'moc': moc, 'domains': sharing, 'pillars': len(pillars),
                       'pillars_flattened': len(topics_by_pillar), 'topics_removed': topics_by_pillar,
                       'cards_changed': len(topics_before), 'cards_unclassified_kept': unclassified,
                       'pages_written': len(pages), 'pages_removed': removed,
                       'topics_before': topics_before, 'projection_issues': projection.issues}}


def run(root: Path, *, domain: str, apply: bool = False, job_id: str | None = None) -> dict:
    """Plan, or with ``apply`` write, the move of ``domain``'s map to three levels."""
    p = plan(root, domain)
    report = p['report']
    job = job_id or p['job_id']
    if not apply:
        return {'status': 'planned', 'job_id': job, 'changed_files': len(p['changes']),
                'paths': [c.relative_path for c in p['changes']], **report}
    if not p['changes']:
        return {'status': 'unchanged', 'job_id': job, **report}
    checks = snapshot_checks(p['snapshot'], p['changes'])
    tx = apply_changes(root, job, p['changes'], checks=checks)
    return {'status': 'applied', 'job_id': job, 'changed_files': len(tx.get('paths') or []),
            'rollback': f'sb recover {job} --rollback', **report}

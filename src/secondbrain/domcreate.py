"""Creating a new domain: frozen evidence, an independent review, then deterministic writes.

The chain has the same shape as the other jobs. ``prepare`` freezes an evidence
packet (the new domain's parameters, candidate cards, metrics). A producer
submits a proposal (taxonomy, cards to move, relation words, arguments for the
boundary and the impact), and after an independent review accepts it this
module only writes: the domain and relation words in catalog.json, the pillar
taxonomy, each moved card (only domain, pillar, topic and the flashcards tag
change; body and review data stay), the registry and the affected maps. Models
write and review the proposal; the writes make no judgement of meaning.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from . import jobs
from . import layout as layouts
from .catalog import validate_catalog
from .classification import writable_snapshot
from .curation import _normalise_taxonomy, _projection_changes, _serialise_taxonomy
from .relocate import _relocated_bytes, _registry_change
from .store import Change, Conflict, digest, read_bytes
from .vault import read_snapshot

KIND = 'domcreate'



def _safe_domain(domain: str) -> str:
    import re
    if not isinstance(domain, str) or not re.fullmatch(r'[a-z][a-z0-9-]{1,39}', domain):
        raise ValueError(f'The domain name must be a safe slug: {domain!r}')
    return domain


def prepare(root: Path, *, domain: str, moc: str, question: str, track: str,
            evidence_ids: list[str], job_id: str, reason: str | None = None) -> dict:
    """Freeze the evidence packet of a new domain; ``evidence_ids`` are all cards the proposal may move in (any domain)."""
    domain = _safe_domain(domain)
    if not isinstance(moc, str) or not moc.strip():
        raise ValueError('The map title must not be empty')
    if not isinstance(question, str) or not question.strip():
        raise ValueError('The question must not be empty')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('prepare needs a specific reason why a new domain is warranted')
    snapshot = read_snapshot(root)
    catalog = snapshot.catalog
    domains = catalog.get('domains') or {}
    if domain in domains:
        raise Conflict(f'Domain exists already: {domain}')
    if any(meta.get('moc') == moc for meta in domains.values()):
        raise Conflict(f'Map title is taken already: {moc}')
    tracks = {t.get('id'): t for t in (catalog.get('tracks') or [])}
    if track not in tracks:
        raise ValueError(f'Unknown track: {track} (existing: {sorted(tracks)})')
    if not evidence_ids or len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError('evidence_ids are empty or repeat')
    cards = []
    missing = []
    edge_count = 0
    for cid in evidence_ids:
        card = snapshot.cards.get(cid)
        if card is None:
            missing.append(cid)
            continue
        rels = card.fm.get('relations') or []
        edge_count += len(rels)
        cards.append({'id': card.id, 'title': card.fm.get('title'), 'domain': card.domain,
                      'pillar': card.fm.get('pillar'), 'topic': card.fm.get('topic'),
                      'relation_count': len(rels)})
    if missing:
        raise ValueError(f'Evidence cards do not exist: {missing[:8]}')
    packet = {
        'kind': KIND,
        'method_version': 'domcreate/1',
        'domain': domain, 'moc': moc, 'question': question, 'track': track,
        'reason': reason,
        'catalog': catalog,
        'evidence_cards': sorted(cards, key=lambda c: (c['domain'], c['id'])),
        'metrics': {'evidence_count': len(cards), 'relation_edges': edge_count,
                    'edges_per_card': round(edge_count / len(cards), 2)},
    }
    return jobs.create(root, job_id, KIND, packet)


def validate(packet: dict, proposal: dict) -> dict:
    if not isinstance(proposal, dict):
        raise ValueError('A new-domain proposal must be a JSON object')
    for key in ('domain', 'moc', 'question', 'track'):
        if proposal.get(key) != packet[key]:
            raise ValueError(f'Proposal {key} differs from the value the job set: {proposal.get(key)!r}')
    tx = proposal.get('taxonomy')
    _normalise_taxonomy(tx, packet['domain'], packet['moc'])
    pairs = set()
    for p in tx.get('pillars', []):
        for t in p.get('topics', []):
            pairs.add((p['name'], t))
    evidence = {c['id'] for c in packet['evidence_cards']}
    migrations = proposal.get('migrations')
    if not isinstance(migrations, list) or not migrations:
        raise ValueError('migrations must be a non-empty list')
    seen = set()
    for row in migrations:
        cid = row.get('id')
        if cid not in evidence:
            raise ValueError(f'Card to move is not among the evidence cards: {cid}')
        if cid in seen:
            raise ValueError(f'Card to move repeats: {cid}')
        seen.add(cid)
        if (row.get('pillar'), row.get('topic')) not in pairs:
            raise ValueError(f'Move target is not in the proposed taxonomy: {cid} → {row.get("pillar")}/{row.get("topic")}')
    relation_types = list(packet['catalog'].get('relation_types') or [])
    inverse = dict(packet['catalog'].get('inverse_relations') or {})
    words = proposal.get('relation_words') or []
    if not isinstance(words, list):
        raise ValueError('relation_words must be a list')
    for w in words:
        t, inv, gloss = w.get('type'), w.get('inverse'), w.get('gloss')
        if not all(isinstance(x, str) and x.strip() for x in (t or '', inv or '', gloss or '')):
            raise ValueError('Every relation_words item needs non-empty type, inverse and gloss')
        if t in relation_types or t in inverse or inv in relation_types or inv in inverse:
            raise ValueError(f'Relation word clashes with the existing ones: {t}/{inv}')
        if t == inv:
            raise ValueError(f'Relation word equals its own inverse: {t}')
    for key in ('boundary_reason', 'difference_reason', 'impact_reason'):
        if not isinstance(proposal.get(key), str) or not proposal[key].strip():
            raise ValueError(f'{key} must be specific')
    return proposal


def _catalog_after(catalog: dict, packet: dict, proposal: dict) -> dict:
    data = json.loads(json.dumps(catalog))
    domain, moc, question, track = packet['domain'], packet['moc'], packet['question'], packet['track']
    data.setdefault('domains', {})[domain] = {'moc': moc, 'question': question}
    for t in data.get('tracks', []):
        if t.get('id') == track:
            t.setdefault('domains', [])
            if domain not in t['domains']:
                t['domains'].append(domain)
            break
    else:
        raise ValueError(f'Unknown track: {track}')
    new_words = []
    for w in proposal.get('relation_words') or []:
        new_words.append(w['type'])
        data.setdefault('relation_types', []).extend([w['type'], w['inverse']])
        data.setdefault('inverse_relations', {})[w['type']] = w['inverse']
        data['inverse_relations'][w['inverse']] = w['type']
        data.setdefault('relation_gloss', {})[w['type']] = w['gloss']
        if w.get('inverse_gloss'):  # otherwise its meaning is derived from the gloss of `type`
            data['relation_gloss'][w['inverse']] = w['inverse_gloss']
    if new_words:
        data.setdefault('domain_relations', {})[domain] = new_words
    return validate_catalog(data)


def apply(root: Path, job_id: str) -> dict:
    from .moc import build_mocs
    state = jobs.load(root, job_id)
    if state['kind'] != KIND:
        raise ValueError('This job is not a new-domain job')
    if state['status'] in {'applied', 'published', 'partial'}:
        return state
    resumed = jobs.resume_transaction(root, job_id)
    if resumed is not None:
        return resumed
    packet = jobs.read_input(root, job_id)
    proposal = validate(packet, jobs.artifact(root, state, 'candidate'))
    review = jobs.require_independent_review(root, state)
    items = review.get('items') or []
    if len(items) != 1 or items[0].get('domain') != packet['domain']:
        raise Conflict('The review must cover this domain exactly once')
    if items[0].get('decision') != 'accept':
        return jobs.update(root, job_id, status='needs_revision',
                           pending=[{'domain': packet['domain'],
                                     'decision': items[0].get('decision'),
                                     'reason': items[0].get('reason')}],
                           applied_paths=[])
    snapshot = read_snapshot(root)
    writable_snapshot(snapshot)
    catalog_now = snapshot.catalog
    if json.dumps(catalog_now, sort_keys=True, ensure_ascii=False) != json.dumps(packet['catalog'], sort_keys=True, ensure_ascii=False):
        raise Conflict('The catalog changed after preparation; prepare the job again')
    evidence_domains = {c['domain'] for c in packet['evidence_cards']}
    card_by_id = {c['id']: c for c in packet['evidence_cards']}

    changes: list[Change] = []
    new_catalog = _catalog_after(catalog_now, packet, proposal)
    lay = layouts.of(root)
    catalog_raw = read_bytes(root, lay.catalog)
    changes.append(Change(lay.catalog, digest(catalog_raw),
                          (json.dumps(new_catalog, ensure_ascii=False, indent=2) + '\n').encode('utf-8')))

    pillar_rel = lay.taxonomy_path(packet['moc'])
    if read_bytes(root, pillar_rel) is not None:
        raise Conflict(f'Taxonomy file exists already: {pillar_rel}')
    changes.append(Change(pillar_rel, None, _serialise_taxonomy(proposal['taxonomy'])))

    domain = packet['domain']
    updated = dict(snapshot.cards)
    moved = []
    affected = set(evidence_domains) | {domain}
    for row in proposal['migrations']:
        card = snapshot.cards[row['id']]
        if card.sha256 != card_by_id[row['id']].get('sha256', card.sha256):
            raise Conflict(f'Card changed after preparation: {row["id"]}')
        new_path = lay.card_path(domain, card.path.rsplit("/", 1)[1])
        if read_bytes(root, new_path) is not None:
            raise Conflict(f'Move target exists already: {new_path}')
        new_raw, new_fm, new_body = _relocated_bytes(card, domain, row)
        changes.append(Change(new_path, None, new_raw))
        changes.append(Change(card.path, card.sha256, None))
        updated[card.id] = replace(card, path=new_path, domain=domain, raw=new_raw,
                                   fm=new_fm, body=new_body, sha256=digest(new_raw))
        moved.append((card, domain))
    if moved:
        changes.append(_registry_change(root, moved))

    projected = replace(snapshot, cards=updated, catalog=new_catalog,
                        taxonomies={**snapshot.taxonomies, packet['moc']: proposal['taxonomy']})
    projection = build_mocs(projected, affected_domains=affected)
    changes.extend(_projection_changes(root, projection))

    return jobs.finish_transaction(root, job_id,
        {'status': 'applied', 'pending': [], 'created_domain': domain,
         'moc': packet['moc'], 'migrations': len(proposal['migrations']),
         'relation_words': [w['type'] for w in proposal.get('relation_words') or []],
         'projection_metrics': projection.metrics,
         'checks_summary': {'catalog_domains': len(new_catalog.get('domains') or {}),
                            'affected_domains': sorted(affected)}}, changes)

"""T3 新域创建：提案证据冻结 → 独立复核 → catalog/体系/迁移的确定性写入。

链路形状与其它任务一致：prepare 冻结证据包（目标域参数、候选证据卡、指标），
生产 agent 提交新域提案（体系、迁移清单、关系词、边界与影响论证），独立复核
通过后，本模块只做确定性写入：catalog.json 加域与关系词、pillar taxonomy、
逐卡迁移（只 patch domain/pillar/topic/flashcards 标签，正文/SR 不动）、
registry 同步、受影响 MOC 重投影。提案与复核都由模型完成，写入没有语义判断。
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
        raise ValueError(f'域名必须是安全 slug：{domain!r}')
    return domain


def prepare(root: Path, *, domain: str, moc: str, question: str, track: str,
            evidence_ids: list[str], job_id: str, reason: str | None = None) -> dict:
    """冻结新域证据包。evidence_ids 是提案方可迁入的候选卡全集（跨域）。"""
    domain = _safe_domain(domain)
    if not isinstance(moc, str) or not moc.strip():
        raise ValueError('moc 标题必须非空')
    if not isinstance(question, str) or not question.strip():
        raise ValueError('question 必须非空')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('prepare 需要具体发起理由（T3 触发判据）')
    snapshot = read_snapshot(root)
    catalog = snapshot.catalog
    domains = catalog.get('domains') or {}
    if domain in domains:
        raise Conflict(f'域已存在：{domain}')
    if any(meta.get('moc') == moc for meta in domains.values()):
        raise Conflict(f'MOC 标题已被占用：{moc}')
    tracks = {t.get('id'): t for t in (catalog.get('tracks') or [])}
    if track not in tracks:
        raise ValueError(f'track 不存在：{track}（现有：{sorted(tracks)}）')
    if not evidence_ids or len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError('evidence_ids 为空或重复')
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
        raise ValueError(f'证据卡不存在：{missing[:8]}')
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
        raise ValueError('新域提案必须是JSON对象')
    for key in ('domain', 'moc', 'question', 'track'):
        if proposal.get(key) != packet[key]:
            raise ValueError(f'提案 {key} 与任务给定值不一致：{proposal.get(key)!r}')
    tx = proposal.get('taxonomy')
    _normalise_taxonomy(tx, packet['domain'], packet['moc'])
    pairs = set()
    for p in tx.get('pillars', []):
        for t in p.get('topics', []):
            pairs.add((p['name'], t))
    evidence = {c['id'] for c in packet['evidence_cards']}
    migrations = proposal.get('migrations')
    if not isinstance(migrations, list) or not migrations:
        raise ValueError('migrations 必须是非空列表')
    seen = set()
    for row in migrations:
        cid = row.get('id')
        if cid not in evidence:
            raise ValueError(f'迁移卡不在证据集：{cid}')
        if cid in seen:
            raise ValueError(f'迁移卡重复：{cid}')
        seen.add(cid)
        if (row.get('pillar'), row.get('topic')) not in pairs:
            raise ValueError(f'迁移目标不在提案体系：{cid} → {row.get("pillar")}/{row.get("topic")}')
    relation_types = list(packet['catalog'].get('relation_types') or [])
    inverse = dict(packet['catalog'].get('inverse_relations') or {})
    words = proposal.get('relation_words') or []
    if not isinstance(words, list):
        raise ValueError('relation_words 必须是列表')
    for w in words:
        t, inv, gloss = w.get('type'), w.get('inverse'), w.get('gloss')
        if not all(isinstance(x, str) and x.strip() for x in (t or '', inv or '', gloss or '')):
            raise ValueError('relation_words 每项必须有非空 type/inverse/gloss')
        if t in relation_types or t in inverse or inv in relation_types or inv in inverse:
            raise ValueError(f'关系词与现有词表冲突：{t}/{inv}')
        if t == inv:
            raise ValueError(f'关系词与自身反向词相同：{t}')
    for key in ('boundary_reason', 'difference_reason', 'impact_reason'):
        if not isinstance(proposal.get(key), str) or not proposal[key].strip():
            raise ValueError(f'{key} 必须具体')
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
        raise ValueError(f'track 不存在：{track}')
    new_words = []
    for w in proposal.get('relation_words') or []:
        new_words.append(w['type'])
        data.setdefault('relation_types', []).append(w['type'])
        data.setdefault('inverse_relations', {})[w['type']] = w['inverse']
        data['inverse_relations'][w['inverse']] = w['type']
        data.setdefault('relation_gloss', {})[w['type']] = w['gloss']
        data['relation_gloss'][w['inverse']] = w.get('inverse_gloss') or f'{w["type"]} 的反向'
    if new_words:
        data.setdefault('domain_relations', {})[domain] = new_words
    return validate_catalog(data)


def apply(root: Path, job_id: str) -> dict:
    from .moc import build_mocs
    state = jobs.load(root, job_id)
    if state['kind'] != KIND:
        raise ValueError('任务类型不是新域创建')
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
        raise Conflict('复核必须恰好覆盖本域一次')
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
        raise Conflict('catalog 在准备后变化，请重建任务')
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
        raise Conflict(f'pillar 体系文件已存在：{pillar_rel}')
    changes.append(Change(pillar_rel, None, _serialise_taxonomy(proposal['taxonomy'])))

    domain = packet['domain']
    updated = dict(snapshot.cards)
    moved = []
    affected = set(evidence_domains) | {domain}
    for row in proposal['migrations']:
        card = snapshot.cards[row['id']]
        if card.sha256 != card_by_id[row['id']].get('sha256', card.sha256):
            raise Conflict(f'卡片在准备后变化：{row["id"]}')
        new_path = lay.card_path(domain, card.path.rsplit("/", 1)[1])
        if read_bytes(root, new_path) is not None:
            raise Conflict(f'移动新址已存在：{new_path}')
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

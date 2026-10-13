"""Body-preserving card relocation between existing catalog domains.

Selection uses prior classification jobs: rows whose independent review
accepted a ``needs_domain_review`` disposition, with a catalog-listed
``suggested_domain`` different from the current one, are frozen into a
``relocation`` packet. Applying performs the paired old-path removal + new-path
creation move trusted by ``store._validate_moves`` plus same-id registry, MOC
and entry/review projection changes in one recoverable transaction; card
bodies/SR/unknown frontmatter stay untouched. A card stays pending, with
concrete referencing files, whenever a still-standing markdown link would break
(explicit old-path links outside this transaction's regenerated projections, or
a directory-relative link inside the moving card). Reviewers never see the
earlier author's self-judgement.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
from dataclasses import replace
from datetime import date
from pathlib import Path
from urllib.parse import unquote

from . import classification as cls, jobs
from .catalog import category_pairs, taxonomy_packet
from .evidence import check_verbatim
from .classification import projection_changes, taxonomy_fingerprint, writable_snapshot
from .store import Change, Conflict, digest, read_bytes
from .vault import (Card, REGISTRY_RELPATH, card_packet, parse_note,
                    patch_frontmatter, read_snapshot)

METHOD_VERSION = 'relocation/1'
_MDPATH_RE = re.compile(r'\[[^\[\]]*\]\(([^)]*)\)')
_WIKI_RE = re.compile(r'\[\[([^\]|#^]+)')
_SCHEME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*:')


def prepare(root_, *, source_jobs, job_id, ids=None):
    """Freeze a relocation batch from independently reviewed domain-review rows."""
    snapshot = read_snapshot(root_)
    if snapshot.root.resolve() != root_.resolve():
        raise ValueError('批次快照不属于该知识库')
    writable_snapshot(snapshot)
    if (not isinstance(source_jobs, list) or not source_jobs
            or len(source_jobs) != len(set(source_jobs))):
        raise ValueError('source_jobs必须是非空且不重复的classification任务ID列表')
    catalog_domains = set((snapshot.catalog.get('domains') or {})
                          if isinstance(snapshot.catalog, dict) else {})
    selected, evidence = {}, {}
    for jid in source_jobs:
        state = jobs.load(root_, jid)
        if state['kind'] != 'classification':
            raise ValueError(f'来源任务不是classification：{jid}')
        for value in cls.reviewed_rows_with_repairs(root_, jid):
            row, finding = value['row'], value['finding']
            if row.get('disposition') != 'needs_domain_review' or finding['decision'] != 'accept':
                continue
            if ids is not None and row['id'] not in ids:
                continue
            reviewed_state = jobs.load(root_, value['job_id'])
            cid = row['id']
            if cid not in snapshot.cards:
                raise ValueError(f'核验通过的卡已不存在：{cid}')
            if snapshot.cards[cid].sha256 != row['input_sha256']:
                raise Conflict(f'已核迁域建议所据卡片已变化：{cid}')
            suggested = row.get('suggested_domain')
            if not isinstance(suggested, str) or not suggested:
                raise ValueError(f'缺建议目的域（suggested_domain）：{cid}')
            if suggested not in catalog_domains:
                raise ValueError(f'目的域不在catalog（新域另行裁定）：{suggested}')
            if suggested == snapshot.cards[cid].domain:
                raise ValueError(f'目的域必须不同于当前域：{cid}')
            if cid in selected and selected[cid] != suggested:
                raise Conflict(f'同一张卡被建议到不同目的域：{cid}')
            selected[cid] = suggested
            evidence[value['job_id']] = {'candidate_sha256': reviewed_state['candidate']['sha256'],
                                         'review_sha256': reviewed_state['review']['sha256']}
    if ids is not None:
        if len(ids) != len(set(ids)) or any(i not in selected for i in ids):
            raise ValueError('卡ID重复或不在独立核验通过的needs_domain_review集合')
        selected = {i: selected[i] for i in ids}
    cards = [snapshot.cards[cid] for cid in selected]
    cards.sort(key=lambda c: (c.domain, c.id))
    if not cards:
        return {'status': 'unchanged', 'job_id': job_id, 'input_count': 0}
    scope = sorted({c.domain for c in cards} | set(selected.values()))
    packet = {'kind': 'relocation', 'method_version': METHOD_VERSION,
              'cards': [card_packet(c) for c in cards],
              'destinations': {c.id: selected[c.id] for c in cards},
              'catalog': snapshot.catalog,
              'taxonomy': taxonomy_packet(snapshot, scope),
              'taxonomy_sha256': taxonomy_fingerprint(snapshot, scope),
              'taxonomy_scope': scope,
              'snapshot_fingerprint': snapshot.fingerprint,
              'source_evidence': evidence}
    state = jobs.create(root_, job_id, 'relocation', packet)
    return {**state, 'input_count': len(cards), 'destinations': packet['destinations']}


def validate(packet, proposal):
    """classification式全覆盖/版本/引用规则；assigned须落在绑定目的域及该域合法pillar/topic。"""
    rows = proposal.get('items')
    if not isinstance(rows, list):
        raise ValueError('迁移提案须为 {items:[...]}')
    expected = {c['id']: c for c in packet['cards']}
    ids = [r.get('id') for r in rows if isinstance(r, dict)]
    if len(ids) != len(rows) or len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError('迁移提案必须覆盖本批全部ID且各一次')
    for row in rows:
        card = expected[row['id']]
        if row.get('input_sha256') != card['sha256']:
            raise Conflict(f'提案版本错：{row["id"]}')
        if row.get('disposition') not in {'assigned', 'taxonomy_gap'}:
            raise ValueError(f'没有明确处理状态：{row["id"]}')
        problem = check_verbatim(card['body'], row.get('evidence_quote'))
        if problem:
            raise ValueError(f'证据不是卡正文逐字片段：{row["id"]}（{problem}）')
        if not isinstance(row.get('reason'), str) or not row['reason'].strip():
            raise ValueError(f'迁移理由缺失：{row["id"]}')
        if row['disposition'] != 'assigned':
            continue
        if row.get('domain') != packet['destinations'].get(row['id']):
            raise ValueError(f'assigned必须落在prepare绑定目的域：{row["id"]}')
        if not row.get('pillar') or not row.get('topic'):
            raise ValueError(f'不能只填一个迁移分类字段：{row["id"]}')
        if (row['pillar'], row['topic']) not in _packet_pairs(packet, row['domain']):
            raise ValueError(f'分类不在该域策展体系中：{row["id"]}')
    return rows


def _packet_pairs(packet, domain):
    tax = packet.get('taxonomy') or {}
    moc = (tax.get('domain_mocs') or {}).get(domain)
    body = (tax.get('taxonomies') or {}).get(moc) or {}
    return {(p['name'], t) for p in body.get('pillars', []) for t in p.get('topics', [])}


def apply(root_, job_id):
    """只有accept+assigned且无断链阻碍的卡成对移动；其余pending并给具体引用文件。"""
    from .moc import build_mocs
    from .projections import entry_projection, review_projection
    state = jobs.load(root_, job_id)
    if state['kind'] != 'relocation':
        raise ValueError('任务类型不是迁移')
    if state['status'] in {'applied', 'published', 'partial'}:
        return state
    resumed = jobs.resume_transaction(root_, job_id)
    if resumed is not None:
        return resumed
    packet = jobs.read_input(root_, job_id)
    rows = validate(packet, jobs.artifact(root_, state, 'candidate'))
    review = jobs.require_independent_review(root_, state)
    findings = cls.review_rows(rows, review)
    snapshot = read_snapshot(root_)
    writable_snapshot(snapshot)
    if taxonomy_fingerprint(snapshot, packet['taxonomy_scope']) != packet['taxonomy_sha256']:
        raise Conflict('分类体系在准备后变化，请重备受影响卡片')
    inputs = {c['id']: c for c in packet['cards']}
    pending, moves = [], []
    for row in rows:
        card = snapshot.cards.get(row['id'])
        if card is None or card.sha256 != inputs[row['id']]['sha256']:
            raise Conflict(f'卡片在准备后变化：{row["id"]}')
        decision = findings[row['id']]
        if decision['decision'] != 'accept' or row['disposition'] != 'assigned':
            pending.append({'id': row['id'], 'disposition': row['disposition'],
                            'reason': decision['reason'] if decision['decision'] != 'accept' else row['reason'],
                            'proposal': row})
            continue
        moves.append(row)
    documents = dict(_iter_md(root_))
    active = list(moves)
    while active:
        changes, projected, moved, affected = _stage_moves(root_, snapshot, packet, active)
        projection = build_mocs(projected, affected_domains=affected)
        entries = entry_projection(projected, date.today())
        reviews = review_projection(projected, date.today(), extra_targets=entries.files)
        replacements = {**projection.files, **entries.files, **reviews.files}
        obstacles = link_obstacles(root_, {c.id: c for c, _ in moved},
                                   documents=documents, replacements=replacements)
        if not obstacles:
            break
        for row in active:
            if row['id'] in obstacles:
                pending.append({'id': row['id'], 'disposition': 'assigned',
                                'reason': '迁移会留下旧路径或目录相对链接，未迁',
                                'proposal': row, 'referencing_files': obstacles[row['id']]})
        active = [row for row in active if row['id'] not in obstacles]
    if not active:
        return jobs.update(root_, job_id, status='needs_revision' if pending else 'applied',
                           pending=pending, applied_paths=[], moved_count=0)
    changes.append(_registry_change(root_, moved))
    changes.extend(projection_changes(root_, projection))
    changes.extend(projection_changes(root_, entries))
    changes.extend(projection_changes(root_, reviews))
    observed = dict(snapshot.read_hashes)
    for rel, raw in documents.items():
        sha = digest(raw)
        if rel in observed and observed[rel] != sha:
            raise Conflict(f'扫描链接时快照输入已变化：{rel}')
        observed[rel] = sha
    checks = cls.snapshot_checks(replace(snapshot, read_hashes=observed), changes)
    return jobs.finish_transaction(root_, job_id,
        {'status': 'partial' if pending else 'applied', 'pending': pending,
         'moved_count': len(moved), 'projection_metrics': projection.metrics}, changes, checks=checks)


def _stage_moves(root_, snapshot, packet, moves):
    changes, updated = [], dict(snapshot.cards)
    moved, affected = [], set()
    for row in moves:
        card = snapshot.cards[row['id']]
        dest = packet['destinations'][row['id']]
        if (row['pillar'], row['topic']) not in category_pairs(snapshot, dest):
            raise ValueError(f'分类不在该域策展体系中：{card.id}')
        new_path = f'10_Knowledge/{dest}/{card.path.rsplit("/", 1)[1]}'
        if read_bytes(root_, new_path) is not None:
            raise Conflict(f'移动新址已存在：{new_path}')
        new_raw, new_fm, new_body = _relocated_bytes(card, dest, row)
        changes.append(Change(new_path, None, new_raw))
        changes.append(Change(card.path, card.sha256, None))
        updated[card.id] = replace(card, path=new_path, domain=dest, raw=new_raw,
                                   fm=new_fm, body=new_body, sha256=digest(new_raw))
        moved.append((card, dest))
        affected.update({card.domain, dest})
    return changes, replace(snapshot, cards=updated), moved, affected


def _relocated_bytes(card, dest, row):
    """精确只patch domain/pillar/topic及机械的flashcards标签换域；正文/SR不动。"""
    patches = {'domain': dest, 'pillar': row['pillar'], 'topic': row['topic']}
    old_tag = f'flashcards/{card.domain}'
    tags = card.fm.get('tags')
    if isinstance(tags, list):
        swapped = [f'flashcards/{dest}' if t == old_tag else t for t in tags]
        if swapped != tags:
            patches['tags'] = swapped
    elif tags == old_tag:
        patches['tags'] = f'flashcards/{dest}'
    raw = patch_frontmatter(card.raw, patches)
    fm, body = parse_note(raw)
    if body != card.body:
        raise ValueError(f'迁移补丁意外修改正文：{card.id}')
    return raw, fm, body


def _registry_change(root_, moved):
    """同IDregistry项只改path/domain，保留未知字段；基于apply时最新字节做CAS。"""
    raw = read_bytes(root_, REGISTRY_RELPATH)
    if raw is None:
        raise ValueError('缺少notes_registry，不能迁移')
    data = json.loads(raw)
    notes = data.get('notes')
    if not isinstance(notes, list):
        raise ValueError('registry notes不是列表')
    index = {n['id']: i for i, n in enumerate(notes) if isinstance(n, dict)}
    for card, dest in moved:
        i = index.get(card.id)
        if i is None:
            raise ValueError(f'registry缺卡：{card.id}')
        note = dict(notes[i])
        note['path'] = f'10_Knowledge/{dest}/{card.path.rsplit("/", 1)[1]}'
        note['domain'] = dest
        notes[i] = note
    after = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    return Change(REGISTRY_RELPATH, digest(raw), after)


def link_obstacles(root_, moving, *, documents=None, replacements=None):
    """{id: [vault相对引用文件]}：迁移会留下断链的卡。

    对实际生成后的字节核链接，不按目录名豁免自定义MOC或指南中保留的正文。
    """
    if not moving:
        return {}
    stem_ids = {card.path[:-3]: cid for cid, card in moving.items()}
    path_ids = {card.path: cid for cid, card in moving.items()}
    obstacles = {cid: [] for cid in moving}
    documents = dict(_iter_md(root_)) if documents is None else documents
    # Generated MOC pages (one root page per catalog MOC plus its page folder)
    # are projections rebuilt from the moved snapshot, so their old links are
    # not real obstacles. Hand-written pages under 20_MOCs are still checked.
    documents = {rel: raw for rel, raw in documents.items() if not _generated_moc_page(root_, rel)}
    for rel, raw_bytes in {**documents, **(replacements or {})}.items():
        text = raw_bytes.decode('utf-8')
        for raw in _WIKI_RE.findall(text):
            norm = _normalize(unquote(raw))
            if '/' not in norm:
                continue
            candidates = {norm, _normalize(posixpath.normpath(posixpath.join(posixpath.dirname(rel), norm)))}
            for stem, cid in stem_ids.items():
                if stem in candidates or any(stem.endswith('/' + value) for value in candidates):
                    obstacles[cid].append(rel)
        md_targets = _MDPATH_RE.findall(text)
        md_targets += re.findall(r'^\s*\[[^]\n]+\]:\s*(<[^>]+>|\S+)', text, flags=re.M)
        for raw in md_targets:
            inner = raw.strip()
            if inner.startswith('<') and '>' in inner:
                inner = inner[1:inner.index('>')]
            else:
                inner = re.split(r'\s+["\']', inner, maxsplit=1)[0]
            if not inner or inner.startswith('#') or inner.startswith('//') or _SCHEME_RE.match(inner):
                continue
            target = unquote(inner.split('#')[0].split('?')[0].strip())
            if not target:
                continue
            if rel in path_ids and not target.startswith('/'):
                obstacles[path_ids[rel]].append(rel)      # 目录相对链接依赖本域目录
            if target.startswith('/'):
                norm = _normalize(target.lstrip('/'))
            else:
                norm = _normalize(posixpath.normpath(
                    posixpath.join(posixpath.dirname(rel), target)))
            if norm in stem_ids:
                obstacles[stem_ids[norm]].append(rel)
    return {cid: sorted(set(files)) for cid, files in obstacles.items() if files}


def _generated_moc_page(root_, rel):
    parts = rel.split('/')
    if parts[0] != '20_MOCs' or len(parts) < 2:
        return False
    raw = read_bytes(root_, '_data/secondbrain/catalog.json')
    mocs = {m.get('moc') for m in (json.loads(raw).get('domains') or {}).values()
            if isinstance(m, dict)} if raw else set()
    head = parts[1][:-3] if len(parts) == 2 and parts[1].endswith('.md') else parts[1]
    return head in mocs and (len(parts) == 2 or len(parts) == 3)


def _normalize(value):
    return (value.strip().strip('"').removesuffix('.md')
            .removeprefix('/').removeprefix('./').rstrip('/'))


def _iter_md(root_):
    """全vault通过no-follow句柄读md；只排除git内部。"""
    root_ = Path(root_)
    for base, dirs, files in os.walk(root_):
        base_path = Path(base)
        dirs[:] = sorted(d for d in dirs if d != '.git' and not (base_path / d).is_symlink())
        for name in sorted(files):
            if not name.endswith('.md'):
                continue
            full = base_path / name
            if full.is_symlink() or not full.is_file():
                continue
            rel = str(full.relative_to(root_)).replace(os.sep, '/')
            raw = read_bytes(root_, rel)
            if raw is None:
                raise Conflict(f'扫描链接时文件消失：{rel}')
            raw.decode('utf-8')
            yield rel, raw

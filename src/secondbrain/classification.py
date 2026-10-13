"""Classification packets and checked metadata-only changes."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from . import jobs
from .catalog import category_pairs, taxonomy_packet
from .evidence import check_verbatim
from .store import Change, Conflict, apply_changes, digest, read_bytes
from .vault import Card, Snapshot, card_packet, parse_note, patch_frontmatter, read_snapshot


def writable_snapshot(snapshot: Snapshot) -> None:
    # Missing curated MOCs are residuals to resolve; malformed identity/storage
    # is not safe to write through, even when a different card was requested.
    fatal = [x for x in snapshot.issues if x.get('code') not in {'missing_taxonomy'}]
    if fatal:
        raise ValueError(f'全库结构有未解决问题：{fatal[:5]}')


def prepare(root: Path, *, ids: list[str] | None = None, domain: str | None = None,
            limit: int | None = None, job_id: str,
            repair_invalid: bool = False, _snapshot: Snapshot | None = None) -> dict:
    snapshot = _snapshot or read_snapshot(root)
    if snapshot.root.resolve() != root.resolve():
        raise ValueError('批次快照不属于该知识库')
    writable_snapshot(snapshot)
    if ids is not None:
        if len(ids) != len(set(ids)) or any(i not in snapshot.cards for i in ids):
            raise ValueError('卡ID重复或不存在')
        cards = [snapshot.cards[i] for i in ids]
    else:
        def eligible(c):
            pair = (c.fm.get('pillar'), c.fm.get('topic'))
            return (all(pair) and pair not in category_pairs(snapshot, c.domain)) if repair_invalid else not all(pair)
        cards = [c for c in snapshot.cards.values() if (not domain or c.domain == domain) and eligible(c)]
    cards.sort(key=lambda c: (c.domain, c.id))
    if limit is not None:
        if limit < 1:
            raise ValueError('batch size必须为正')
        cards = cards[:limit]
    if not cards:
        return {'status': 'unchanged', 'job_id': job_id, 'input_count': 0}
    packet = {'kind': 'classification', 'method_version': 'classification/1',
              'repair_invalid': repair_invalid,
              'cards': [card_packet(c) for c in cards],
              'catalog': snapshot.catalog,
              'taxonomy': taxonomy_packet(snapshot, sorted({c.domain for c in cards})),
              'taxonomy_sha256': taxonomy_fingerprint(snapshot, sorted({c.domain for c in cards})),
              'taxonomy_scope': sorted({c.domain for c in cards}),
              'snapshot_fingerprint': snapshot.fingerprint}
    state = jobs.create(root, job_id, 'classification', packet)
    return {**state, 'input_count': len(cards), 'domains': sorted({c.domain for c in cards})}


def taxonomy_fingerprint(snapshot: Snapshot, domains: list[str] | None = None) -> str:
    taxonomies = snapshot.taxonomies
    if domains is not None:
        mocs = {snapshot.catalog['domains'][d]['moc'] for d in domains}
        taxonomies = {m: snapshot.taxonomies.get(m) for m in sorted(mocs)}
    return digest(jobs.encoded({'catalog': snapshot.catalog, 'taxonomies': taxonomies}))


def validate(packet: dict, proposal: dict, *, check_evidence: bool = True,
             check_assignment: bool = True) -> list[dict]:
    rows = proposal.get('items')
    if not isinstance(rows, list):
        raise ValueError('分类提案须为 {items:[...]}')
    expected = {c['id']: c for c in packet['cards']}
    ids = [r.get('id') for r in rows if isinstance(r, dict)]
    if len(ids) != len(rows) or len(ids) != len(set(ids)) or set(ids) != set(expected):
        import collections
        dupes = sorted(i for i, n in collections.Counter(ids).items() if n > 1)
        missing = sorted(set(expected) - set(ids))
        unknown = sorted(set(ids) - set(expected))
        detail = []
        if missing:
            detail.append('缺：' + '、'.join(missing[:12]) + (f' 等{len(missing)}个' if len(missing) > 12 else ''))
        if dupes:
            detail.append('重复：' + '、'.join(dupes[:8]))
        if unknown:
            detail.append('不在本批：' + '、'.join(unknown[:8]))
        raise ValueError('分类提案必须覆盖本批全部ID且各一次（' + '；'.join(detail) + '）')
    version_errors = []
    for row in rows:
        card = expected[row['id']]
        got = row.get('input_sha256')
        if isinstance(got, str):
            got = got.strip()
            row['input_sha256'] = got
        if not got:
            # 版本绑定的语义是「提案针对冻结字节」，真源在本包内；模型漏填时
            # 由服务端按冻结包补齐，不改变绑定强度（id 不存在已在上面挡住）。
            row['input_sha256'] = card['sha256']
        elif got != card['sha256']:
            exp = card['sha256']
            pos = next((i for i, (a, b) in enumerate(zip(exp, got)) if a != b), min(len(exp), len(got)))
            version_errors.append(
                f'{row["id"]} 应为 {exp}；实交串与期望值在第 {pos} 位起不同'
                f'（实交片段 {got[max(0, pos - 4):pos + 8]!r}，全长 {len(got)}，'
                f'注意不可见字符/全角字符/长度）')
    if version_errors:
        raise Conflict('提案版本错（逐项列出期望值，按冻结包修正后重交）：' + '；'.join(version_errors))
    for row in rows:
        card = expected[row['id']]
        if row.get('disposition') not in {'assigned', 'taxonomy_gap', 'needs_domain_review'}:
            raise ValueError(f'没有明确处理状态：{row["id"]}')
        if check_evidence:
            problem = check_verbatim(card['body'], row.get('evidence_quote'))
            if problem:
                raise ValueError(f'证据不是卡正文逐字片段：{row["id"]}（{problem}）')
        if not isinstance(row.get('reason'), str) or not row['reason'].strip():
            raise ValueError(f'分类理由缺失：{row["id"]}')
        if check_assignment and row['disposition'] == 'assigned' and (not row.get('pillar') or not row.get('topic')):
            raise ValueError(f'不能只填一个分类字段：{row["id"]}')
    return rows


def _frozen_category_pairs(packet: dict, domain: str) -> set[tuple[str, str]]:
    taxonomy = packet.get('taxonomy')
    if (not isinstance(taxonomy, dict)
            or not isinstance(taxonomy.get('taxonomies'), dict)
            or not isinstance(packet.get('catalog'), dict)):
        return set()
    return category_pairs(SimpleNamespace(catalog=packet.get('catalog'),
                                          taxonomies=taxonomy['taxonomies']), domain)


def _validate_candidate_shape(packet: dict, candidate: dict) -> list[dict]:
    if not isinstance(candidate, dict) or not isinstance(candidate.get('items'), list):
        raise ValueError('分类提案须为 {items:[...]}')
    rows = candidate['items']
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('分类提案每项必须是对象')
        cid = row.get('id')
        if not isinstance(cid, str) or not cid:
            raise ValueError('分类提案ID必须是非空字符串')
        disposition = row.get('disposition')
        if not isinstance(disposition, str) or disposition not in {
                'assigned', 'taxonomy_gap', 'needs_domain_review'}:
            raise ValueError(f'没有明确处理状态：{cid}')
    return rows


def validate_submission(packet: dict, payload: dict, *, role: str,
                        candidate: dict | None = None, **_context) -> dict:
    """Validate a submission before it can become a persisted artifact."""
    if role == 'producer':
        rows = _validate_candidate_shape(packet, payload)
        validate(packet, payload, check_evidence=True)
        for row in rows:
            for key in ('pillar', 'topic'):
                if key in row and row[key] is not None and not isinstance(row[key], str):
                    raise ValueError(f'{key}必须为null或字符串：{row["id"]}')
            card = next(c for c in packet['cards'] if c['id'] == row['id'])
            if 'domain' in row and (not isinstance(row['domain'], str)
                                    or row['domain'] != card.get('domain')):
                raise Conflict(f'分类提交不能迁移域：{row["id"]}')
            if row['disposition'] == 'assigned':
                if (not isinstance(row.get('pillar'), str) or not row['pillar'].strip()
                        or not isinstance(row.get('topic'), str) or not row['topic'].strip()):
                    raise ValueError(f'assigned分类字段必须是非空字符串：{row["id"]}')
                if (row['pillar'], row['topic']) not in _frozen_category_pairs(packet, card.get('domain')):
                    raise ValueError(f'分类不在冻结策展体系中：{row["id"]}')
        return payload
    if role != 'reviewer':
        raise ValueError(f'未知提交角色：{role}')
    if candidate is None:
        raise ValueError('复核提交缺少候选')
    candidate_rows = _validate_candidate_shape(packet, candidate)
    validate(packet, candidate, check_evidence=False, check_assignment=False)
    review_items = payload.get('items') if isinstance(payload, dict) else None
    if not isinstance(review_items, list):
        raise ValueError('复核须为 {items:[{id,decision,reason}]}')
    for finding in review_items:
        if not isinstance(finding, dict) or not isinstance(finding.get('id'), str) or not finding['id']:
            raise ValueError('复核项ID必须是非空字符串')
        if (not isinstance(finding.get('decision'), str)
                or finding['decision'] not in {'accept', 'revise'}
                or not isinstance(finding.get('reason'), str)
                or not finding['reason'].strip()):
            raise ValueError('每项复核必须有明确判定与具体理由')
    findings = review_rows(candidate_rows, payload)
    inputs = {c['id']: c for c in packet['cards']}
    for row in candidate_rows:
        card = inputs[row['id']]
        if 'domain' in row and (not isinstance(row['domain'], str)
                                or row['domain'] != card.get('domain')):
            raise Conflict(f'分类提交不能迁移域：{row["id"]}')
        if findings[row['id']]['decision'] != 'accept':
            continue
        for key in ('pillar', 'topic'):
            if key in row and row[key] is not None and not isinstance(row[key], str):
                raise ValueError(f'{key}必须为null或字符串：{row["id"]}')
        if check_verbatim(card['body'], row.get('evidence_quote')):
            raise ValueError(f'已接受项证据不是卡正文逐字片段：{row["id"]}')
        if row['disposition'] == 'assigned':
            if (row['pillar'], row['topic']) not in _frozen_category_pairs(packet, card.get('domain')):
                raise ValueError(f'分类不在冻结策展体系中：{row["id"]}')
    return payload


def evidence_errors(packet: dict, rows: list[dict]) -> dict[str, str]:
    inputs = {c['id']: c for c in packet['cards']}
    return {r['id']: '引文不是冻结正文逐字片段，必须修订后重新独立复核'
            for r in rows if check_verbatim(inputs[r['id']]['body'], r.get('evidence_quote'))}


def require_accepted_evidence(packet, rows, findings):
    for cid in evidence_errors(packet, rows):
        if findings[cid]['decision'] == 'accept':
            raise ValueError(f'已接受项证据不是卡正文逐字片段：{cid}')


def review_rows(rows: list[dict], review: dict) -> dict[str, dict]:
    findings = review.get('items')
    if not isinstance(findings, list):
        raise ValueError('复核须为 {items:[{id,decision,reason}]}')
    ids = [x.get('id') for x in findings if isinstance(x, dict)]
    if len(ids) != len(findings) or len(ids) != len(set(ids)) or set(ids) != {x['id'] for x in rows}:
        raise ValueError('独立复核没有逐项覆盖全部候选')
    for x in findings:
        if x.get('decision') not in {'accept', 'revise'} or not isinstance(x.get('reason'), str) or not x['reason'].strip():
            raise ValueError('每项复核必须有明确判定与具体理由')
    return {x['id']: x for x in findings}


def stage_changes(snapshot: Snapshot, packet: dict, rows: list[dict], findings: dict[str, dict]):
    require_accepted_evidence(packet, rows, findings)
    if taxonomy_fingerprint(snapshot, packet.get('taxonomy_scope')) != packet['taxonomy_sha256']:
        raise Conflict('分类体系在准备后变化，请重备受影响卡片')
    inputs = {c['id']: c for c in packet['cards']}
    pending, changes, updated = [], [], dict(snapshot.cards)
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
        if (row['pillar'], row['topic']) not in category_pairs(snapshot, card.domain):
            raise ValueError(f'分类不在该域策展体系中：{card.id}')
        old_pair = (card.fm.get('pillar'), card.fm.get('topic'))
        may_repair = packet.get('repair_invalid') is True and all(old_pair) and old_pair not in category_pairs(snapshot, card.domain)
        for key in ['pillar', 'topic']:
            if not may_repair and card.fm.get(key) and card.fm[key] != row[key]:
                raise Conflict(f'本次只补空字段，不能覆盖已有{key}：{card.id}')
        patches = {k: row[k] for k in ['pillar', 'topic'] if not card.fm.get(k) or (may_repair and card.fm.get(k) != row[k])}
        if not patches:
            continue
        raw = patch_frontmatter(card.raw, patches)
        fm, body = parse_note(raw)
        if body != card.body:
            raise ValueError(f'分类补丁意外修改正文：{card.id}')
        changes.append(Change(card.path, card.sha256, raw))
        updated[card.id] = replace(card, raw=raw, fm=fm, body=body, sha256=digest(raw))
    return changes, replace(snapshot, cards=updated), pending


def projection_changes(root: Path, projection) -> list[Change]:
    result = []
    for rel, data in sorted(projection.files.items()):
        before = read_bytes(root, rel)
        if before != data:
            result.append(Change(rel, None if before is None else digest(before), data))
    return result


def snapshot_checks(snapshot: Snapshot, changes: list[Change]) -> dict:
    """Bind projection inputs to the bytes actually read, including other cards."""
    checks = dict(snapshot.read_hashes)
    for change in changes:
        if change.relative_path in checks:
            if checks[change.relative_path] != change.before_sha256:
                raise Conflict(f'投影输入在读取后变化：{change.relative_path}')
            del checks[change.relative_path]
    return checks


def apply(root: Path, job_id: str) -> dict:
    from .moc import build_mocs
    state = jobs.load(root, job_id)
    if state['kind'] != 'classification':
        raise ValueError('任务类型不是分类')
    if state['status'] in {'applied', 'published', 'partial'}:
        return state
    resumed = jobs.resume_transaction(root, job_id)
    if resumed is not None:
        return resumed
    packet = jobs.read_input(root, job_id)
    proposal = jobs.artifact(root, state, 'candidate')
    rows = validate(packet, proposal, check_evidence=False)
    review = jobs.require_independent_review(root, state)
    findings = review_rows(rows, review)
    snapshot = read_snapshot(root)
    writable_snapshot(snapshot)
    changes, projected, pending = stage_changes(snapshot, packet, rows, findings)
    if not changes:
        return jobs.update(root, job_id, status='needs_revision' if pending else 'applied', pending=pending,
                           applied_paths=[], classified_count=0)
    affected = {snapshot.cards[r['id']].domain for r in rows if r['id'] not in {x['id'] for x in pending}}
    projection = build_mocs(projected, affected_domains=affected)
    changes.extend(projection_changes(root, projection))
    from datetime import date
    from .projections import entry_projection
    changes.extend(projection_changes(root, entry_projection(projected, date.today())))
    return jobs.finish_transaction(root, job_id,
        {'status': 'partial' if pending else 'applied', 'pending': pending,
         'classified_count': len([c for c in changes if snapshot.layout.is_card(c.relative_path)]),
         'projection_metrics': projection.metrics}, changes, checks=snapshot_checks(snapshot, changes))


def reviewed_rows_with_repairs(root: Path, job_id: str, *, _depth: int = 0) -> list[dict]:
    """Resolve the single-child repair chain without a depth-based cutoff."""
    resolved = {}
    seen = set()
    current_id = job_id
    root_rows = None
    while current_id:
        if current_id in seen:
            raise Conflict('修订子任务链存在循环')
        seen.add(current_id)
        state = jobs.load(root, current_id)
        packet = jobs.read_input(root, current_id)
        rows = validate(packet, jobs.artifact(root, state, 'candidate'), check_evidence=False)
        findings = review_rows(rows, jobs.require_independent_review(root, state))
        require_accepted_evidence(packet, rows, findings)
        if root_rows is None:
            root_rows = rows
        for row in rows:
            resolved[row['id']] = {'row': row, 'finding': findings[row['id']], 'job_id': current_id}
        child_id = state.get('repair_child')
        if not child_id:
            break
        child_packet = jobs.read_input(root, child_id)
        rejected = {i for i, f in findings.items() if f['decision'] == 'revise'}
        if (child_packet.get('repair_parent') != current_id
                or child_packet.get('repair_parent_candidate') != state['candidate']['sha256']
                or child_packet.get('repair_parent_review') != state['review']['sha256']
                or {c['id'] for c in child_packet['cards']} != rejected):
            raise Conflict('修订子任务不属于该复核版本的反例集')
        parent_cards = {c['id']: c for c in packet['cards']}
        if any(c != parent_cards[c['id']] for c in child_packet['cards']):
            raise Conflict('修订卡片全文版本与父任务不同')
        current_id = child_id
    return [resolved[r['id']] for r in root_rows]

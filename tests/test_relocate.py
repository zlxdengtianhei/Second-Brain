"""Relocation tests: body-preserving card moves across existing catalog domains.

Covers a real migration (snapshot clean, ID/body/SR preserved, registry unknown
fields kept, old/new MOC links), missing independent review, original/destination
conflicts, interrupt -> resume/rollback pair restoration, explicit old-path links
leaving cards pending untouched, and taxonomy-gap / unbound-domain refusals.
"""
from __future__ import annotations

import json

import pytest

from secondbrain import classification as cls, jobs, relocate as r, store
from secondbrain.store import Conflict
from secondbrain.vault import parse_note, read_snapshot
from tests.helpers import submit_reviewed

SRC, DST = 'attention', 'transformer'
SRC_MOC, DST_MOC = '注意力 MOC', 'Transformer MOC'
SRC_PILLAR, SRC_TOPIC = '示例支柱：信息路由', '示例主题：总览'
DST_PILLAR, DST_TOPIC = '并行与解码', '自回归解码'
CARD, QUOTE = 'card_a', '已有卡正文，保持逐字不动。'


def build_vault(root):
    root = root.resolve()
    (root / f'10_Knowledge/{SRC}').mkdir(parents=True)
    (root / f'10_Knowledge/{DST}').mkdir(parents=True)
    (root / f'10_Knowledge/{SRC}/{CARD}.md').write_text(
        f'---\nid: {CARD}\ntitle: 注意力卡\ndomain: {SRC}\n'
        f'pillar: {SRC_PILLAR}\ntopic: {SRC_TOPIC}\n'
        f'tags: [review, flashcards/{SRC}]\ncustom: {{keep: true}}\nsr-due: 2026-09-01\n'
        f'---\n## 本质\n{QUOTE}\n<!--SR:!2026-10-01,10,250-->\n', encoding='utf-8')
    (root / f'10_Knowledge/{DST}/card_b.md').write_text(
        f'---\nid: card_b\ntitle: 解码卡\ndomain: {DST}\n'
        f'pillar: {DST_PILLAR}\ntopic: {DST_TOPIC}\n---\n## X\n对侧域卡正文。\n',
        encoding='utf-8')
    (root / '20_MOCs/_pillars').mkdir(parents=True)
    for title, pillar, topic in [(SRC_MOC, SRC_PILLAR, SRC_TOPIC),
                                 (DST_MOC, DST_PILLAR, DST_TOPIC)]:
        (root / '20_MOCs/_pillars' / f'{title}.yaml').write_text(json.dumps(
            {'title': title, 'narrative': 'n',
             'pillars': [{'name': pillar, 'order': 1, 'why': 'w', 'topics': [topic]}]},
            ensure_ascii=False), encoding='utf-8')
    (root / '_data/secondbrain').mkdir(parents=True)
    catalog = {'schema': 'secondbrain-catalog/1',
               'tracks': [{'id': 'ml', 'title': 'ML', 'domains': [SRC, DST]}],
               'domains': {SRC: {'moc': SRC_MOC, 'question': '?'},
                           DST: {'moc': DST_MOC, 'question': '?'}},
               'relation_types': ['similar_to'],
               'inverse_relations': {'similar_to': 'similar_to'},
               'domain_relations': {}}
    (root / '_data/secondbrain/catalog.json').write_text(
        json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
    (root / '_data/notes_registry.json').write_text(json.dumps({'notes': [
        {'id': CARD, 'path': f'10_Knowledge/{SRC}/{CARD}.md',
         'title': '注意力卡', 'domain': SRC, 'custom': 'keep'},
        {'id': 'card_b', 'path': f'10_Knowledge/{DST}/card_b.md',
         'title': '解码卡', 'domain': DST}]}, ensure_ascii=False), encoding='utf-8')
    return root


def source_job(root, job_id='src-cls', *, suggested=DST, accept=True):
    """真实的classification任务：needs_domain_review并被独立复核accept。"""
    cls.prepare(root, ids=[CARD], job_id=job_id)
    packet = jobs.read_input(root, job_id)
    row = {'id': CARD, 'input_sha256': packet['cards'][0]['sha256'],
           'disposition': 'needs_domain_review', 'suggested_domain': suggested,
           'evidence_quote': QUOTE, 'reason': '该卡主题属于对侧域'}
    review = {'items': [{'id': CARD, 'decision': 'accept' if accept else 'revise',
                         'reason': '独立核对原文'}]}
    submit_reviewed(root, job_id, {'items': [row]}, review)
    cls.apply(root, job_id)


def relocation_rows(root, job_id='relo', *, disposition='assigned', domain=DST):
    packet = jobs.read_input(root, job_id)
    return [{'id': CARD, 'input_sha256': packet['cards'][0]['sha256'],
             'disposition': disposition, 'domain': domain,
             'pillar': DST_PILLAR if disposition == 'assigned' else None,
             'topic': DST_TOPIC if disposition == 'assigned' else None,
             'evidence_quote': QUOTE, 'reason': '目标域taxonomy容纳该主题'}]


def relocation_proposal(root, job_id='relo', *, disposition='assigned', domain=DST):
    rows = relocation_rows(root, job_id, disposition=disposition, domain=domain)
    review = {'items': [{'id': x['id'], 'decision': 'accept', 'reason': '独立核对该域归属'}
                        for x in rows]}
    submit_reviewed(root, job_id, {'items': rows}, review)
    return rows[0]


def test_real_relocation_across_existing_domains(tmp_path):
    root = build_vault(tmp_path)
    source_job(root)
    old_bytes = (root / f'10_Knowledge/{SRC}/{CARD}.md').read_bytes()
    prepared = r.prepare(root, source_jobs=['src-cls'], job_id='relo')
    assert prepared['kind'] == 'relocation'
    assert prepared['destinations'] == {CARD: DST}
    packet = jobs.read_input(root, 'relo')
    assert set(packet['taxonomy_scope']) == {SRC, DST}
    assert packet['source_evidence']['src-cls']['review_sha256']
    relocation_proposal(root)
    result = r.apply(root, 'relo')
    assert result['status'] == 'applied' and result['moved_count'] == 1
    old_path, new_path = (root / f'10_Knowledge/{SRC}/{CARD}.md',
                          root / f'10_Knowledge/{DST}/{CARD}.md')
    assert not old_path.exists() and new_path.exists()
    snapshot = read_snapshot(root)
    assert snapshot.issues == []
    fm, body = parse_note(new_path.read_bytes())
    assert fm['id'] == CARD and fm['domain'] == DST
    assert (fm['pillar'], fm['topic']) == (DST_PILLAR, DST_TOPIC)
    assert fm['custom'] == {'keep': True}
    assert fm['sr-due'].isoformat() == '2026-09-01'
    assert fm['tags'] == ['review', f'flashcards/{DST}']
    assert body == f'## 本质\n{QUOTE}\n<!--SR:!2026-10-01,10,250-->\n'
    reg = json.loads((root / '_data/notes_registry.json').read_text())
    note = next(n for n in reg['notes'] if n['id'] == CARD)
    assert note['path'] == f'10_Knowledge/{DST}/{CARD}.md'
    assert note['domain'] == DST and note['custom'] == 'keep'
    old_pages = list((root / '20_MOCs' / f'{SRC_MOC}').glob('*.md'))
    new_pages = list((root / '20_MOCs' / f'{DST_MOC}').glob('*.md'))
    assert not any(f'10_Knowledge/{SRC}/{CARD}' in p.read_text() for p in old_pages)
    assert any(f'10_Knowledge/{DST}/{CARD}' in p.read_text() for p in new_pages)
    assert (root / 'HOME.md').exists() and tuple(result['applied_paths']) != ()


def test_missing_independent_review_writes_nothing(tmp_path):
    root = build_vault(tmp_path)
    source_job(root)
    r.prepare(root, source_jobs=['src-cls'], job_id='relo')
    rows = relocation_rows(root)
    from tests.helpers import submit_as
    submit_as(root, 'relo', {'items': rows}, 'producer')  # producer only, no review
    with pytest.raises(ValueError):
        r.apply(root, 'relo')
    assert (root / f'10_Knowledge/{SRC}/{CARD}.md').exists()
    assert not (root / f'10_Knowledge/{DST}/{CARD}.md').exists()
    note = json.loads((root / '_data/notes_registry.json').read_text())['notes'][0]
    assert note['path'] == f'10_Knowledge/{SRC}/{CARD}.md'


def test_original_card_change_aborts(tmp_path):
    root = build_vault(tmp_path)
    source_job(root)
    r.prepare(root, source_jobs=['src-cls'], job_id='relo')
    before = (root / f'10_Knowledge/{SRC}/{CARD}.md').read_bytes()
    relocation_proposal(root)
    (root / f'10_Knowledge/{SRC}/{CARD}.md').write_bytes(before + b'<!-- human edit -->\n')
    with pytest.raises(Conflict, match='卡片在准备后变化'):
        r.apply(root, 'relo')
    assert not (root / f'10_Knowledge/{DST}/{CARD}.md').exists()


def test_destination_occupied_is_rejected(tmp_path):
    root = build_vault(tmp_path)
    source_job(root)
    r.prepare(root, source_jobs=['src-cls'], job_id='relo')
    relocation_proposal(root)
    blocker = root / f'10_Knowledge/{DST}/{CARD}.md'
    blocker.write_text('---\nid: other\ntitle: 同名文件\ndomain: transformer\n---\n已有同名文件。\n')
    reg = json.loads((root / '_data/notes_registry.json').read_text())
    reg['notes'].append({'id': 'other', 'path': f'10_Knowledge/{DST}/{CARD}.md',
                         'title': '同名文件', 'domain': DST})
    (root / '_data/notes_registry.json').write_text(json.dumps(reg, ensure_ascii=False),
                                                    encoding='utf-8')
    with pytest.raises(Conflict, match='移动新址已存在'):
        r.apply(root, 'relo')
    assert blocker.read_text().startswith('---')
    assert (root / f'10_Knowledge/{SRC}/{CARD}.md').exists()


def test_interrupt_resume_and_rollback_restore_pairs(tmp_path, monkeypatch):
    root = build_vault(tmp_path)
    source_job(root)
    r.prepare(root, source_jobs=['src-cls'], job_id='relo')
    relocation_proposal(root)
    old = (root / f'10_Knowledge/{SRC}/{CARD}.md').read_bytes()
    reg_before = (root / '_data/notes_registry.json').read_bytes()
    real = jobs.apply_changes
    with monkeypatch.context() as mp:  # 隔离上下文，避免undo掉autouse的SECONDBRAIN_RUNTIME
        mp.setattr(jobs, 'apply_changes',
                   lambda *a, **k: real(*a, **k, _crash_after=1))
        with pytest.raises(RuntimeError, match='injected interruption'):
            r.apply(root, 'relo')
    assert r.apply(root, 'relo')['status'] == 'applied'
    assert not (root / f'10_Knowledge/{SRC}/{CARD}.md').exists()
    assert (root / f'10_Knowledge/{DST}/{CARD}.md').exists()
    assert store.recover(root, 'relo', 'rollback')['status'] == 'rolled_back'
    assert (root / f'10_Knowledge/{SRC}/{CARD}.md').read_bytes() == old
    assert not (root / f'10_Knowledge/{DST}/{CARD}.md').exists()
    assert (root / '_data/notes_registry.json').read_bytes() == reg_before


def test_explicit_old_path_link_leaves_pending_untouched(tmp_path):
    root = build_vault(tmp_path)
    source_job(root)
    (root / f'10_Knowledge/{SRC}/card_c.md').write_text(
        f'---\nid: card_c\ntitle: 引用卡\ndomain: {SRC}\n---\n## L\n[[10_Knowledge/{SRC}/{CARD}|看]]\n',
        encoding='utf-8')
    reg = json.loads((root / '_data/notes_registry.json').read_text())
    reg['notes'].append({'id': 'card_c', 'path': f'10_Knowledge/{SRC}/card_c.md',
                         'title': '引用卡', 'domain': SRC})
    (root / '_data/notes_registry.json').write_text(json.dumps(reg, ensure_ascii=False),
                                                    encoding='utf-8')
    r.prepare(root, source_jobs=['src-cls'], job_id='relo')
    relocation_proposal(root)
    result = r.apply(root, 'relo')
    assert result['status'] == 'needs_revision' and result['moved_count'] == 0
    assert result['pending'][0]['id'] == CARD
    assert result['pending'][0]['referencing_files'] == [f'10_Knowledge/{SRC}/card_c.md']
    assert (root / f'10_Knowledge/{SRC}/{CARD}.md').exists()
    assert not (root / f'10_Knowledge/{DST}/{CARD}.md').exists()


def test_gap_or_unbound_domain_not_written(tmp_path):
    root = build_vault(tmp_path)
    source_job(root)
    r.prepare(root, source_jobs=['src-cls'], job_id='relo')
    before = (root / f'10_Knowledge/{SRC}/{CARD}.md').read_bytes()
    relocation_proposal(root, disposition='taxonomy_gap')
    result = r.apply(root, 'relo')
    assert result['status'] == 'needs_revision' and result['moved_count'] == 0
    assert result['pending'][0]['disposition'] == 'taxonomy_gap'
    assert (root / f'10_Knowledge/{SRC}/{CARD}.md').read_bytes() == before
    r.prepare(root, source_jobs=['src-cls'], job_id='relo2')
    relocation_proposal(root, job_id='relo2', domain='newdomain')
    with pytest.raises(ValueError, match='绑定目的域'):
        r.apply(root, 'relo2')
    assert not (root / f'10_Knowledge/{DST}/{CARD}.md').exists()


def test_prepare_requires_suggested_domain_in_catalog(tmp_path):
    root = build_vault(tmp_path)
    cls.prepare(root, ids=[CARD], job_id='j1')
    packet = jobs.read_input(root, 'j1')
    row = {'id': CARD, 'input_sha256': packet['cards'][0]['sha256'],
           'disposition': 'needs_domain_review', 'evidence_quote': QUOTE, 'reason': 'r'}
    submit_reviewed(root, 'j1', {'items': [row]},
                    {'items': [{'id': CARD, 'decision': 'accept', 'reason': '核过'}]})
    cls.apply(root, 'j1')
    with pytest.raises(ValueError, match='缺建议目的域'):
        r.prepare(root, source_jobs=['j1'], job_id='r1')
    source_job(root, job_id='j2', suggested='newdomain')
    with pytest.raises(ValueError, match='不在catalog'):
        r.prepare(root, source_jobs=['j2'], job_id='r2')
    source_job(root, job_id='j3', suggested=SRC)
    with pytest.raises(ValueError, match='必须不同于当前域'):
        r.prepare(root, source_jobs=['j3'], job_id='r3')
    # 复核revise的不入选：整批unchanged
    source_job(root, job_id='j4', accept=False)
    assert r.prepare(root, source_jobs=['j4'], job_id='r4')['status'] == 'unchanged'


@pytest.mark.parametrize('ref', ['20_MOCs/用户自定义.md', '使用指南.md', 'notes/links.md'])
def test_retained_pages_are_not_exempt_from_link_checks(tmp_path, ref):
    root = build_vault(tmp_path)
    source_job(root)
    p = root / ref
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'保留的说明 [[10_Knowledge/{SRC}/{CARD}|原卡]]\n')
    r.prepare(root, source_jobs=['src-cls'], job_id='relo')
    relocation_proposal(root)
    result = r.apply(root, 'relo')
    assert result['moved_count'] == 0
    assert ref in result['pending'][0]['referencing_files']


def test_title_filename_and_late_link_edit_are_checked(tmp_path, monkeypatch):
    root = build_vault(tmp_path)
    original = root / f'10_Knowledge/{SRC}/{CARD}.md'
    title_path = original.with_name('带空格的 标题.md')
    original.rename(title_path)
    registry = root / '_data/notes_registry.json'
    value = json.loads(registry.read_text())
    value['notes'][0]['path'] = title_path.relative_to(root).as_posix()
    registry.write_text(json.dumps(value))
    source_job(root)
    p = root / 'manual.md'
    p.write_text('暂时没有链接')
    r.prepare(root, source_jobs=['src-cls'], job_id='relo')
    relocation_proposal(root)
    real = jobs.finish_transaction
    def changed_after_scan(*a, **kw):
        p.write_text('[现在引用](<10_Knowledge/attention/带空格的 标题.md>)')
        return real(*a, **kw)
    monkeypatch.setattr(jobs, 'finish_transaction', changed_after_scan)
    with pytest.raises(Conflict, match='read changed after'):
        r.apply(root, 'relo')
    assert title_path.exists()
    assert not (root / f'10_Knowledge/{DST}' / title_path.name).exists()


def test_prior_domain_suggestion_cannot_authorize_changed_card(tmp_path):
    root = build_vault(tmp_path)
    source_job(root)
    p = root / f'10_Knowledge/{SRC}/{CARD}.md'
    p.write_bytes(p.read_bytes() + b'changed meaning')
    with pytest.raises(Conflict, match='所据卡片已变化'):
        r.prepare(root, source_jobs=['src-cls'], job_id='relo')

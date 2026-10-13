import json

import pytest

from secondbrain import classification as c, jobs
from secondbrain.store import Conflict
from secondbrain.vault import parse_note
from tests.helpers import build_vault, submit_reviewed, DOMAIN, EXISTING, PILLAR, TOPIC, MOC


def ready(tmp_path, *, classified=False):
    root = build_vault(tmp_path)
    path = root / f'10_Knowledge/{DOMAIN}/{EXISTING}.md'
    text = path.read_text().replace('---\nbody', '---\nbody')
    if not classified:
        text = text.replace(f'pillar: {PILLAR}', 'pillar: null').replace(f'topic: {TOPIC}', 'topic: null')
    text = text.replace('---\nid:', '---\n# keep comment\ncustom: {nested: [a, b]}\nsr-due: 2026-01-01\nid:')
    path.write_text(text + '<!--SR:!2026-10-01,10,250-->\n')
    c.prepare(root, ids=[EXISTING], job_id='classify')
    packet = jobs.read_input(root, 'classify')
    row = {'id': EXISTING, 'input_sha256': packet['cards'][0]['sha256'],
           'disposition': 'assigned', 'pillar': PILLAR, 'topic': TOPIC,
           'evidence_quote': '已有卡正文，保持逐字不动。', 'reason': '原文覆盖这个主题'}
    review = {'items': [{'id': EXISTING, 'decision': 'accept', 'reason': '独立原文核对'}]}
    return root, path, row, review


def test_metadata_only_application_preserves_unknown_fields_body_sr(tmp_path):
    root, path, row, review = ready(tmp_path)
    before = path.read_bytes()
    submit_reviewed(root, 'classify', {'items': [row]}, review)
    result = c.apply(root, 'classify')
    assert result['classified_count'] == 1
    expected = before.replace(b'pillar: null', ('pillar: ' + PILLAR).encode()).replace(b'topic: null', ('topic: ' + TOPIC).encode())
    assert path.read_bytes() == expected
    assert c.apply(root, 'classify')['classified_count'] == 1


def test_valid_existing_category_cannot_be_overwritten(tmp_path):
    root, path, row, review = ready(tmp_path, classified=True)
    tax_path = root / f'20_MOCs/_pillars/{MOC}.yaml'
    tax = json.loads(tax_path.read_text()); tax['pillars'][0]['topics'].append('另一个合法主题'); tax_path.write_text(json.dumps(tax))
    # Prepare again under a fresh identity after the taxonomy changed.
    c.prepare(root, ids=[EXISTING], job_id='overwrite')
    row['topic'] = '另一个合法主题'
    before = path.read_bytes()
    submit_reviewed(root, 'overwrite', {'items': [row]}, review)
    with pytest.raises(Conflict, match='cannot overwrite the existing topic'):
        c.apply(root, 'overwrite')
    assert path.read_bytes() == before


def test_accidental_body_edit_is_rejected_before_write(tmp_path, monkeypatch):
    root, path, row, review = ready(tmp_path)
    before = path.read_bytes()
    submit_reviewed(root, 'classify', {'items': [row]}, review)
    real = c.patch_frontmatter
    monkeypatch.setattr(c, 'patch_frontmatter', lambda raw, updates: real(raw, updates) + b'changed body')
    with pytest.raises(ValueError, match='would change the card body'):
        c.apply(root, 'classify')
    assert path.read_bytes() == before


def test_missing_hash_is_bound_to_the_frozen_card(tmp_path):
    root, path, row, review = ready(tmp_path)
    expected = row.pop('input_sha256')
    rows = c.validate(jobs.read_input(root, 'classify'), {'items': [row]})
    assert rows[0]['input_sha256'] == expected


@pytest.mark.parametrize('change', ['wrong_hash', 'missing_id', 'bad_quote', 'extra_id'])
def test_complete_hash_bound_proposals_required(tmp_path, change):
    root, path, row, review = ready(tmp_path)
    proposal = {'items': [row]}
    if change == 'wrong_hash': row['input_sha256'] = 'wrong'
    if change == 'missing_id': proposal['items'] = []
    if change == 'bad_quote': row['evidence_quote'] = '不在正文里的词'
    if change == 'extra_id': proposal['items'].append({**row, 'id': 'invented'})
    with pytest.raises(ValueError):
        c.validate(jobs.read_input(root, 'classify'), proposal)


def test_rejected_review_and_gap_do_not_clear_classification(tmp_path):
    root, path, row, review = ready(tmp_path)
    before = path.read_bytes()
    row.update(disposition='taxonomy_gap', pillar=None, topic=None)
    submit_reviewed(root, 'classify', {'items': [row]}, review)
    result = c.apply(root, 'classify')
    assert result['status'] == 'needs_revision'
    assert result['pending'][0]['id'] == EXISTING
    assert path.read_bytes() == before


def test_bad_quote_cannot_be_accepted_or_counted_as_reviewed(tmp_path):
    root, path, row, review = ready(tmp_path)
    before = path.read_bytes()
    row['evidence_quote'] = '不在冻结正文里的词'
    with pytest.raises(ValueError, match='verbatim'):
        submit_reviewed(root, 'classify', {'items': [row]}, review)
    state = jobs.load(root, 'classify')
    assert state['candidate'] is None and state['review'] is None
    assert [r['status'] for r in jobs.list_runs(root, 'classify')] == ['started']
    packet = jobs.read_input(root, 'classify')
    with pytest.raises(ValueError, match='Evidence of an accepted item'):
        c.require_accepted_evidence(packet, [row], {row['id']: review['items'][0]})
    assert path.read_bytes() == before


def test_repair_invalid_is_explicit_and_cannot_overwrite_valid_categories(tmp_path):
    root, path, row, review = ready(tmp_path, classified=True)
    before = path.read_bytes()
    assert c.prepare(root, job_id='empty-invalid', repair_invalid=True)['status'] == 'unchanged'
    path.write_bytes(before.replace(('topic: ' + TOPIC).encode(), b'topic: obsolete'))
    c.prepare(root, job_id='repair', repair_invalid=True)
    row['input_sha256'] = jobs.read_input(root, 'repair')['cards'][0]['sha256']
    submit_reviewed(root, 'repair', {'items': [row]}, review)
    result = c.apply(root, 'repair')
    assert result['classified_count'] == 1
    assert path.read_bytes() == before


def test_unrelated_taxonomy_change_does_not_invalidate_frozen_classification(tmp_path):
    from secondbrain.vault import read_snapshot
    from dataclasses import replace
    root, path, row, review = ready(tmp_path)
    snapshot = read_snapshot(root)
    unrelated = replace(snapshot, taxonomies={**snapshot.taxonomies, '无关领域': {'title': '后来新增'}})
    changes, _, pending = c.stage_changes(unrelated, jobs.read_input(root, 'classify'), [row], {EXISTING: review['items'][0]})
    assert len(changes) == 1 and pending == []
    altered = json.loads(json.dumps(snapshot.taxonomies))
    altered[MOC]['narrative'] = '同域实际变动'
    with pytest.raises(Conflict, match='taxonomy changed'):
        c.stage_changes(replace(snapshot, taxonomies=altered), jobs.read_input(root, 'classify'), [row], {EXISTING: review['items'][0]})


def test_stale_projection_cannot_overwrite_another_completed_batch(tmp_path, monkeypatch):
    from secondbrain import projections
    root, path, row, review = ready(tmp_path)
    other_id = 'card_other'
    other = root / '10_Knowledge/other' / (other_id + '.md')
    other.parent.mkdir()
    other.write_text(path.read_text().replace(EXISTING, other_id).replace('domain: '+DOMAIN, 'domain: other'))
    reg = root / '_data/notes_registry.json'
    data = json.loads(reg.read_text())
    data['notes'].append({**data['notes'][0], 'id': other_id, 'domain': 'other', 'path': other.relative_to(root).as_posix()})
    reg.write_text(json.dumps(data))
    catpath = root / '_data/secondbrain/catalog.json'
    cat = json.loads(catpath.read_text())
    cat['domains']['other'] = {'moc': 'Other', 'question': '?'}
    cat['tracks'][0]['domains'].append('other')
    catpath.write_text(json.dumps(cat))
    tx = json.loads((root / f'20_MOCs/_pillars/{MOC}.yaml').read_text())
    tx['title'] = 'Other'
    (root / '20_MOCs/_pillars/Other.yaml').write_text(json.dumps(tx))
    c.prepare(root, ids=[EXISTING], job_id='first')
    c.prepare(root, ids=[other_id], job_id='other')
    second = jobs.read_input(root, 'other')['cards'][0]
    submit_reviewed(root, 'first', {'items': [row]}, review)
    submit_reviewed(root, 'other', {'items': [{**row, 'id': other_id, 'input_sha256': second['sha256']}]},
                    {'items': [{**review['items'][0], 'id': other_id}]})
    real = projections.entry_projection
    once = []
    def interleave(snapshot, today):
        if not once:
            once.append(True)
            c.apply(root, 'other')
        return real(snapshot, today)
    monkeypatch.setattr(projections, 'entry_projection', interleave)
    with pytest.raises(Conflict, match='changed after it was|changed while it was read'):
        c.apply(root, 'first')
    assert b'pillar: null' in path.read_bytes()
    assert b'pillar: null' not in other.read_bytes()
    assert 'without pillar or topic: 1.' in (root / 'HOME.md').read_text()

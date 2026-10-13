import copy

import pytest

from secondbrain import classification, jobs, store
from secondbrain.service import Service
from tests.test_classification import ready


def reserve(root, job_id, run_id, role, candidate_ref=None):
    model = 'gpt-test' if role == 'producer' else 'claude-test'
    return jobs.start_run(root, job_id, role=role, step='review' if role == 'reviewer' else 'produce',
                          executor='fake', model_requested=model, run_id=run_id)


def setup(root):
    root, path, row, review = ready(root)
    return root, path, row, review


def test_direct_jobs_submit_guard_is_atomic_and_exact_duplicate_is_idempotent(tmp_path):
    root, path, row, _ = setup(tmp_path)
    payload = {'items': [row]}
    reserve(root, 'classify', 'p1', 'producer')
    state_after_reserve = store.read_state(root, jobs.location('classify'))
    with pytest.raises(ValueError):
        jobs.submit(root, 'classify', {'items': [{'id': row['id']}]}, role='producer', run_id='p1')
    assert store.read_state(root, jobs.location('classify')) == state_after_reserve
    assert not store.read_state(root, jobs.location('classify', 'producer-p1.json'))
    first = jobs.submit(root, 'classify', payload, role='producer', run_id='p1')
    artifact_before = store.read_state(root, first['artifact']['path'])
    assert jobs.submit(root, 'classify', copy.deepcopy(payload), role='producer', run_id='p1') == first
    assert store.read_state(root, first['artifact']['path']) == artifact_before


def test_service_gap_and_invalid_repair_payloads_are_checked_before_writes(tmp_path):
    root, path, row, review = setup(tmp_path)
    row['disposition'] = 'taxonomy_gap'
    row['pillar'] = row['topic'] = None
    reserve(root, 'classify', 'gap', 'producer')
    Service(root).submit('classify', {'items': [row]}, role='producer', run_id='gap')
    assert jobs.load(root, 'classify')['status'] == 'proposed'

    root2, path2, row2, _ = setup(tmp_path / 'repair')
    row2['disposition'] = 'needs_domain_review'
    row2['pillar'] = row2['topic'] = None
    reserve(root2, 'classify', 'repair', 'producer')
    jobs.submit(root2, 'classify', {'items': [row2]}, role='producer', run_id='repair')
    assert jobs.load(root2, 'classify')['status'] == 'proposed'


def test_reviewer_accept_is_strict_but_revise_may_describe_bad_candidate(tmp_path):
    root, path, row, review = setup(tmp_path)
    reserve(root, 'classify', 'p', 'producer')
    jobs.submit(root, 'classify', {'items': [row]}, role='producer', run_id='p')
    reserve(root, 'classify', 'r', 'reviewer')
    bad_review = {'items': [{'id': row['id'], 'decision': 'accept', 'reason': 'ok'}]}
    # The persisted candidate is valid, so acceptance itself is allowed.
    jobs.submit(root, 'classify', bad_review, role='reviewer', run_id='r')

    packet = jobs.read_input(root, 'classify')
    bad_candidate = {'items': [{**row, 'evidence_quote': 'not in frozen body',
                                'pillar': 'unknown pillar', 'topic': 'unknown topic'}]}
    with pytest.raises(ValueError):
        classification.validate_submission(packet, bad_review, role='reviewer',
                                           candidate=bad_candidate)
    classification.validate_submission(
        packet, {'items': [{'id': row['id'], 'decision': 'revise', 'reason': '修订引文和类别'}]},
        role='reviewer', candidate=bad_candidate)


@pytest.mark.parametrize('bad', [
    'wrapper', 'missing', 'duplicate', 'unknown', 'id_type', 'disposition_type',
    'sha', 'quote', 'reason', 'pillar_type', 'topic_type', 'illegal_pair', 'domain',
])
def test_malformed_submission_can_retry_same_slot_without_pollution(tmp_path, bad):
    root, _, row, _ = setup(tmp_path)
    valid = {'items': [copy.deepcopy(row)]}
    payload = copy.deepcopy(valid)
    r = payload['items'][0]
    if bad == 'wrapper': payload = {'candidate': payload}
    elif bad == 'missing': payload['items'] = []
    elif bad == 'duplicate': payload['items'].append(copy.deepcopy(r))
    elif bad == 'unknown': r['id'] = 'unknown-card'
    elif bad == 'id_type': r['id'] = []
    elif bad == 'disposition_type': r['disposition'] = []
    elif bad == 'sha': r['input_sha256'] = '0' * 64
    elif bad == 'quote': r['evidence_quote'] = 'not in the frozen body'
    elif bad == 'reason': r['reason'] = ' '
    elif bad == 'pillar_type': r['pillar'] = ['not a string']
    elif bad == 'topic_type': r['topic'] = ['not a string']
    elif bad == 'illegal_pair': r['topic'] = 'not a curated topic'
    elif bad == 'domain': r['domain'] = 'not-this-domain'
    reserve(root, 'classify', 'retry', 'producer')
    before = store.read_state(root, jobs.location('classify'))
    with pytest.raises(ValueError):
        Service(root).submit('classify', payload, role='producer', run_id='retry')
    assert store.read_state(root, jobs.location('classify')) == before
    assert store.read_state(root, jobs.location('classify', 'producer-retry.json')) is None
    result = Service(root).submit('classify', valid, role='producer', run_id='retry')
    after = store.read_state(root, jobs.location('classify'))
    assert result['written_to_knowledge'] is False
    assert len(jobs.load(root, 'classify')['runs']) == 1
    assert Service(root).submit('classify', valid, role='producer', run_id='retry') == result
    assert store.read_state(root, jobs.location('classify')) == after


def test_gap_disposition_rejects_non_string_metadata_before_persist(tmp_path):
    root, _, row, _ = setup(tmp_path)
    packet = jobs.read_input(root, 'classify')
    for bad in ([], {}, True, 7):
        candidate = {**row, 'disposition': 'taxonomy_gap', 'pillar': bad, 'topic': None}
        with pytest.raises(ValueError):
            classification.validate_submission(packet, {'items': [candidate]}, role='producer')


def test_reviewer_accept_rejects_bad_category_type_but_revise_keeps_it_describable(tmp_path):
    root, _, row, _ = setup(tmp_path)
    packet = jobs.read_input(root, 'classify')
    candidate = {**row, 'pillar': [], 'topic': {}}
    accept = {'items': [{'id': row['id'], 'decision': 'accept', 'reason': 'bad'}]}
    with pytest.raises(ValueError):
        classification.validate_submission(packet, accept, role='reviewer', candidate={'items': [candidate]})
    revise = {'items': [{'id': row['id'], 'decision': 'revise', 'reason': '修复坏类型'}]}
    assert classification.validate_submission(packet, revise, role='reviewer', candidate={'items': [candidate]}) == revise


def test_frozen_missing_taxonomy_rejects_assigned_but_preserves_gap(tmp_path):
    root, _, row, _ = setup(tmp_path)
    packet = jobs.read_input(root, 'classify')
    packet['taxonomy']['taxonomies'] = {}
    with pytest.raises(ValueError):
        classification.validate_submission(packet, {'items': [row]}, role='producer')
    gap = {**row, 'disposition': 'taxonomy_gap', 'pillar': None, 'topic': None}
    assert classification.validate_submission(packet, {'items': [gap]}, role='producer')

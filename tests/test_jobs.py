import base64
import json

import pytest

from secondbrain import config, jobs, store
from tests.helpers import submit_as, submit_reviewed


def reviewed(root, *, producer='gpt-test', reviewer='claude-test', attestation='test'):
    jobs.create(root, 'case', 'test-kind', {'cards': []})
    submit_as(root, 'case', {'items': []}, 'producer', model=producer, attestation=attestation)
    submit_as(root, 'case', {'items': []}, 'reviewer', model=reviewer, attestation=attestation)


def test_run_records_bind_input_candidate_and_distinct_runs(tmp_path):
    reviewed(tmp_path)
    state = jobs.load(tmp_path, 'case')
    producer, reviewer = jobs.list_runs(tmp_path, 'case')
    assert producer['run_id'] != reviewer['run_id']
    assert producer['packet_sha256'] == reviewer['packet_sha256'] == state['input_sha256']
    assert reviewer['candidate_sha256'] == state['candidate']['sha256'] == producer['output_sha256']
    assert state['review']['candidate_sha256'] == state['candidate']['sha256']
    assert producer['model_reported'] == 'gpt-test' and producer['attestation'] == 'test'
    assert jobs.require_independent_review(tmp_path, state) == {'items': []}


def test_new_candidate_invalidates_the_old_review(tmp_path):
    reviewed(tmp_path)
    submit_as(tmp_path, 'case', {'items': [], 'changed': True}, 'producer')
    state = jobs.load(tmp_path, 'case')
    assert state['review'] is None
    with pytest.raises(store.Conflict, match='缺少候选或复核'):
        jobs.require_independent_review(tmp_path, state)


def test_api_runs_of_the_same_family_fail_the_gate(tmp_path):
    reviewed(tmp_path, producer='deepseek-v4-flash', reviewer='deepseek-other')
    state = jobs.load(tmp_path, 'case')
    assert state['same_family'] is True
    with pytest.raises(ValueError, match='different model families'):
        jobs.require_independent_review(tmp_path, state)


def test_host_runs_may_share_a_family_but_are_marked(tmp_path):
    reviewed(tmp_path, producer='claude-a', reviewer='claude-b', attestation='self_reported')
    state = jobs.load(tmp_path, 'case')
    assert state['same_family'] is True
    assert jobs.list_runs(tmp_path, 'case')[1]['same_family'] is True
    assert jobs.require_independent_review(tmp_path, state) == {'items': []}
    config.set_review_policy(config.ReviewPolicy(allow_same_family_in_host_mode=False))
    with pytest.raises(ValueError, match='different model families'):
        jobs.require_independent_review(tmp_path, state)


def test_same_family_allowed_when_policy_does_not_require_difference(tmp_path):
    config.set_review_policy(config.ReviewPolicy(require_different_family=False))
    reviewed(tmp_path, producer='glm-a', reviewer='glm-b')
    assert jobs.require_independent_review(tmp_path, jobs.load(tmp_path, 'case')) == {'items': []}


def test_unknown_family_rejected_for_api_runs_only(tmp_path):
    reviewed(tmp_path, producer='mystery-model', reviewer='claude-test')
    with pytest.raises(ValueError, match='Unknown model family'):
        jobs.require_independent_review(tmp_path, jobs.load(tmp_path, 'case'))
    other = tmp_path / 'host'
    other.mkdir()
    jobs.create(other, 'case', 'test-kind', {'cards': []})
    submit_as(other, 'case', {'items': []}, 'producer', model='mystery-model', attestation='self_reported')
    submit_as(other, 'case', {'items': []}, 'reviewer', model='claude-test', attestation='self_reported')
    assert jobs.require_independent_review(other, jobs.load(other, 'case')) == {'items': []}


def test_run_level_guards(tmp_path):
    jobs.create(tmp_path, 'case', 'test-kind', {'cards': []})
    with pytest.raises(store.Conflict, match='REVIEW_BEFORE_CANDIDATE'):
        jobs.start_run(tmp_path, 'case', role='reviewer', step='review', executor='fake',
                       model_requested='claude-test')
    run = jobs.start_run(tmp_path, 'case', role='producer', step='produce', executor='fake',
                         model_requested='gpt-test')
    first = jobs.submit(tmp_path, 'case', {'items': []}, role='producer', run_id=run['run_id'], attestation='test')
    # exact repeat is idempotent; different bytes for the same run are refused
    assert jobs.submit(tmp_path, 'case', {'items': []}, role='producer', run_id=run['run_id'], attestation='test') == first
    with pytest.raises(store.Conflict, match='RUN_ALREADY_SUBMITTED'):
        jobs.submit(tmp_path, 'case', {'items': [1]}, role='producer', run_id=run['run_id'], attestation='test')
    with pytest.raises(store.Conflict, match='REPAIR_REQUIRED'):
        jobs.start_run(tmp_path, 'case', role='producer', step='produce', executor='fake', model_requested='gpt-test')
    # a review started against one candidate cannot land on a newer one
    review_run = jobs.start_run(tmp_path, 'case', role='reviewer', step='review', executor='fake',
                                model_requested='claude-test')
    submit_as(tmp_path, 'case', {'items': ['v2']}, 'producer')
    with pytest.raises(store.Conflict, match='STALE_CANDIDATE'):
        jobs.submit(tmp_path, 'case', {'items': []}, role='reviewer', run_id=review_run['run_id'], attestation='test')
    abandoned = jobs.abandon_run(tmp_path, 'case', review_run['run_id'], reason='superseded')
    assert abandoned['status'] == 'abandoned'
    with pytest.raises(store.Conflict, match='TERMINAL_RUN'):
        jobs.submit(tmp_path, 'case', {'items': []}, role='reviewer', run_id=review_run['run_id'], attestation='test')


def test_completion_after_crash_does_not_rebuild_from_changed_inputs(tmp_path, monkeypatch):
    reviewed(tmp_path)
    target = tmp_path / '10_Knowledge/d/c.md'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'old')
    real = jobs.update

    def crash(*args, **kwargs):
        raise RuntimeError('crash after transaction before job state')
    monkeypatch.setattr(jobs, 'update', crash)
    with pytest.raises(RuntimeError):
        jobs.finish_transaction(tmp_path, 'case', {'status': 'applied'},
                                [store.Change('10_Knowledge/d/c.md', store.digest(b'old'), b'new')])
    assert target.read_bytes() == b'new'
    assert jobs.load(tmp_path, 'case')['status'] == 'applying'
    with pytest.raises(store.Conflict):
        submit_as(tmp_path, 'case', {'items': []}, 'producer')
    monkeypatch.setattr(jobs, 'update', real)
    result = jobs.resume_transaction(tmp_path, 'case')
    assert result['status'] == 'applied'
    assert result['applied_paths'] == ['10_Knowledge/d/c.md']


def test_input_and_artifact_tamper_rejected(tmp_path):
    reviewed(tmp_path)
    state = jobs.load(tmp_path, 'case')
    (store.state_root(tmp_path) / state['review']['path']).write_text('{"items": ["forged"]}')
    with pytest.raises(store.Conflict, match='修改'):
        jobs.require_independent_review(tmp_path, state)
    (store.state_root(tmp_path) / jobs.location('case', 'input.json')).write_text('{}')
    with pytest.raises(store.Conflict, match='输入'):
        jobs.read_input(tmp_path, 'case')


def test_same_job_id_across_vaults_is_isolated(tmp_path):
    a = tmp_path / 'vault-a'
    a.mkdir()
    (a / '10_Knowledge').mkdir()
    b = tmp_path / 'vault-b'
    b.mkdir()
    (b / '10_Knowledge').mkdir()
    sa = jobs.create(a, 'same', 'classification', {'cards': ['a']})
    sb = jobs.create(b, 'same', 'classification', {'cards': ['b']})
    assert sa['input_sha256'] != sb['input_sha256']
    assert store.state_root(a) != store.state_root(b)
    jobs.update(a, 'same', status='proposed')
    assert jobs.load(a, 'same')['status'] == 'proposed'
    assert jobs.load(b, 'same')['status'] == 'prepared'
    assert jobs.read_input(b, 'same') == {'cards': ['b']}
    assert (store.state_root(a) / 'jobs' / 'same' / 'input.json').is_file()
    assert not (a / '_data').exists() and not (b / '_data').exists()


def test_resume_reads_original_intent_and_rechecks_the_gate(tmp_path, monkeypatch):
    reviewed(tmp_path)
    target = tmp_path / '10_Knowledge/d/d.md'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'old')
    real = jobs.apply_changes

    def crash(*args, **kwargs):
        raise RuntimeError('crash before the knowledge transaction')
    monkeypatch.setattr(jobs, 'apply_changes', crash)
    with pytest.raises(RuntimeError):
        jobs.finish_transaction(tmp_path, 'case', {'status': 'applied'},
                                [store.Change('10_Knowledge/d/d.md', store.digest(b'old'), b'new')])
    assert not (tmp_path / '_data').exists()
    intent_path = store.state_root(tmp_path) / jobs.location('case', 'commit-intent.json')
    intent = json.loads(intent_path.read_text())
    assert intent['result'] == {'status': 'applied'}
    assert intent['changes'] == [{'path': '10_Knowledge/d/d.md',
                                  'before_sha256': store.digest(b'old'),
                                  'after': base64.b64encode(b'new').decode('ascii')}]
    monkeypatch.setattr(jobs, 'apply_changes', real)
    # a forged reviewer run record blocks resume before any knowledge write
    state = jobs.load(tmp_path, 'case')
    run_path = store.state_root(tmp_path) / jobs.run_location('case', state['review']['run_id'])
    original = run_path.read_bytes()
    forged = json.loads(original)
    forged['run_id'] = state['candidate']['run_id']
    run_path.write_text(json.dumps(forged))
    with pytest.raises(ValueError):
        jobs.resume_transaction(tmp_path, 'case')
    assert target.read_bytes() == b'old'
    run_path.write_bytes(original)
    result = jobs.resume_transaction(tmp_path, 'case')
    assert result['status'] == 'applied'
    assert target.read_bytes() == b'new'


def test_finish_requires_a_reviewed_candidate(tmp_path):
    jobs.create(tmp_path, 'bare', 'test-kind', {'cards': []})
    with pytest.raises(store.Conflict, match='没有已复核'):
        jobs.finish_transaction(tmp_path, 'bare', {'status': 'applied'}, [])


def test_list_jobs_reports_transaction_only_records(tmp_path):
    root = tmp_path
    store.apply_changes(root, 'refresh-job',
                        [store.Change('10_Knowledge/test/a.md', None, b'refreshed\n')])
    entries = {e['job_id']: e for e in jobs.list_jobs(root)}
    assert entries['refresh-job']['kind'] == 'transaction'
    assert entries['refresh-job']['status'] == 'applied'
    assert entries['refresh-job']['paths'] == ['10_Knowledge/test/a.md']
    with pytest.raises(ValueError, match='任务不存在'):
        jobs.load(root, 'refresh-job')
    bad = store.state_root(root) / 'jobs' / 'broken-job'
    bad.mkdir(parents=True)
    (bad / 'state.json').write_text('not json')
    by_id = {e['job_id']: e for e in jobs.list_jobs(root)}
    assert by_id['broken-job']['status'] == 'unreadable'


def test_chunk_slots_are_independent(tmp_path):
    jobs.create(tmp_path, 'chunked', 'test-kind', {'chunks': ['c01', 'c02']})
    submit_reviewed(tmp_path, 'chunked', {'items': ['a']}, {'items': []}, chunk='c01')
    submit_as(tmp_path, 'chunked', {'items': ['b']}, 'producer', chunk='c02')
    state = jobs.load(tmp_path, 'chunked')
    assert jobs.require_independent_review(tmp_path, state, 'c01') == {'items': []}
    with pytest.raises(store.Conflict):
        jobs.require_independent_review(tmp_path, state, 'c02')
    assert state['chunks']['c02']['status'] == 'proposed'

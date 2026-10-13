import json

import pytest

from secondbrain import classification, config, jobs, runner
from secondbrain.executors import ExecutorFailure, FakeExecutor, Result
from tests.helpers import DOMAIN, EXISTING, PILLAR, TOPIC, submit_reviewed
from tests.test_classification import ready


def pair(root, row, *, reject_reviews=1):
    """Producer echoes the row; the reviewer rejects the first N reviews."""
    calls = []

    def produce(task):
        calls.append(('producer', task))
        packet = task.packet.get('input', task.packet)
        return {'items': [{**row, 'id': c['id'], 'input_sha256': c['sha256'],
                           'reason': f'候选第{len(calls)}版'} for c in packet['cards']]}

    def review(task):
        calls.append(('reviewer', task))
        n = sum(1 for role, _ in calls if role == 'reviewer')
        decision = 'revise' if n <= reject_reviews else 'accept'
        return {'items': [{'id': item['id'], 'decision': decision,
                           'reason': '需要原文定位' if decision == 'revise' else '已核原文定位'}
                          for item in task.packet['candidate']['items']]}

    return (FakeExecutor('gpt-test', responder=produce),
            FakeExecutor('claude-test', responder=review), calls)


def test_rejected_candidate_is_revised_once_then_independently_reviewed(tmp_path):
    root, path, row, _ = ready(tmp_path)
    before = path.read_bytes()
    producer, reviewer, calls = pair(root, row, reject_reviews=1)
    result = runner.run_job(root, 'classify', producer=producer, reviewer=reviewer, apply=False)
    assert [role for role, _ in calls] == ['producer', 'reviewer', 'producer', 'reviewer']
    assert len(result['runs']) == 2
    child = jobs.load(root, result['repair_child'])
    assert len(child['runs']) == 2
    assert jobs.require_independent_review(root, result)['items'][0]['decision'] == 'revise'
    assert jobs.require_independent_review(root, child)['items'][0]['decision'] == 'accept'
    assert path.read_bytes() == before


def test_repair_rounds_are_bounded_by_policy(tmp_path):
    root, path, row, _ = ready(tmp_path)
    before = path.read_bytes()
    producer, reviewer, calls = pair(root, row, reject_reviews=99)
    result = runner.run_job(root, 'classify', producer=producer, reviewer=reviewer)
    assert [role for role, _ in calls] == ['producer', 'reviewer'] * 2
    assert result['escalation_required'] is True
    child = jobs.load(root, result['repair_child'])
    assert child['escalation_required'] is True and child['status'] == 'needs_revision'
    assert path.read_bytes() == before
    runner.run_job(root, 'classify', producer=producer, reviewer=reviewer)
    assert len(calls) == 4
    # with two rounds allowed, the same rejection costs one more pair
    other = tmp_path / 'two-rounds'
    other.mkdir()
    root2, _, row2, _ = ready(other)
    config.set_review_policy(config.ReviewPolicy(max_repair_rounds=2))
    p2, r2, calls2 = pair(root2, row2, reject_reviews=99)
    runner.run_job(root2, 'classify', producer=p2, reviewer=r2)
    assert len(calls2) == 6


def test_reviewer_packet_withholds_producer_reasons(tmp_path):
    root, _, row, _ = ready(tmp_path)
    producer, reviewer, calls = pair(root, row, reject_reviews=0)
    runner.run_job(root, 'classify', producer=producer, reviewer=reviewer, apply=False)
    review_task = next(task for role, task in calls if role == 'reviewer')
    assert 'reason' not in json.dumps(review_task.packet['candidate'], ensure_ascii=False)
    assert review_task.packet['candidate']['items'][0]['evidence_quote'] == row['evidence_quote']
    runs = jobs.list_runs(root, 'classify')
    assert runs[0]['packet_sha256'] == runs[1]['packet_sha256'] == jobs.load(root, 'classify')['input_sha256']
    assert runs[0]['executor'] == 'fake' and runs[0]['model_reported'] == 'gpt-test'


def test_same_family_api_pair_is_refused_by_the_gate(tmp_path):
    root, path, row, _ = ready(tmp_path)
    before = path.read_bytes()
    producer, reviewer, _ = pair(root, row, reject_reviews=0)
    reviewer.model = reviewer.model_reported = 'gpt-other'
    with pytest.raises(ValueError, match='different model families'):
        runner.run_job(root, 'classify', producer=producer, reviewer=reviewer)
    assert path.read_bytes() == before


def test_invalid_producer_output_spends_one_round_with_feedback(tmp_path):
    root, path, row, _ = ready(tmp_path)
    outputs = [{'items': [{**row, 'evidence_quote': '不在正文里的词'}]}, {'items': [row]}]
    prompts = []

    def produce(task):
        prompts.append(task.prompt)
        return outputs.pop(0)
    producer = FakeExecutor('gpt-test', responder=produce)
    reviewer = FakeExecutor('claude-test', outputs={'reviewer': {'items': [
        {'id': EXISTING, 'decision': 'accept', 'reason': 'ok'}]}})
    result = runner.run_job(root, 'classify', producer=producer, reviewer=reviewer)
    assert result['status'] == 'applied'
    assert 'rejected by the validator' in prompts[1]
    statuses = [r['status'] for r in jobs.list_runs(root, 'classify')]
    assert statuses == ['failed', 'submitted', 'submitted']
    assert jobs.list_runs(root, 'classify')[0]['error']['code'] == 'INVALID_OUTPUT'


def test_executor_failure_is_recorded_and_job_stays_retryable(tmp_path):
    root, path, row, review = ready(tmp_path)
    failing = FakeExecutor('gpt-test', responder=lambda task: Result(
        status='failed', error={'code': 'HTTP_ERROR', 'message': '503', 'retryable': True}))
    reviewer = FakeExecutor('claude-test', outputs={'reviewer': review})
    with pytest.raises(ExecutorFailure, match='HTTP_ERROR'):
        runner.run_job(root, 'classify', producer=failing, reviewer=reviewer)
    assert jobs.list_runs(root, 'classify')[0]['status'] == 'failed'
    producer = FakeExecutor('gpt-test', outputs={'producer': {'items': [row]}})
    assert runner.run_job(root, 'classify', producer=producer, reviewer=reviewer)['status'] == 'applied'


def test_repairs_only_rejected_ids_and_resumes_without_repeating_models(tmp_path):
    root, path, row, _ = ready(tmp_path)
    second_id = 'card_second'
    second = path.with_name(second_id + '.md')
    second.write_text(path.read_text().replace(EXISTING, second_id))
    registry = root / '_data/notes_registry.json'
    value = json.loads(registry.read_text())
    template = next(x for x in value['notes'] if x['id'] == EXISTING)
    value['notes'].append({**template, 'id': second_id, 'path': str(second.relative_to(root))})
    registry.write_text(json.dumps(value))
    classification.prepare(root, ids=[EXISTING, second_id], job_id='pair')
    calls = []

    def produce(task):
        packet = task.packet.get('input', task.packet)
        calls.append(('producer', [x['id'] for x in packet['cards']]))
        return {'items': [{**row, 'id': x['id'], 'input_sha256': x['sha256']} for x in packet['cards']]}

    def review(task):
        ids = [x['id'] for x in task.packet['input']['cards']]
        calls.append(('reviewer', ids))
        assert 'repair_feedback' not in task.packet['input']
        return {'items': [{'id': i, 'decision': 'revise' if task.job_id == 'pair' and i == second_id else 'accept',
                           'reason': '逐卡对照全文'} for i in ids]}
    producer = FakeExecutor('gpt-test', responder=produce)
    reviewer = FakeExecutor('claude-test', responder=review)
    before = {p: p.read_bytes() for p in [path, second]}
    result = runner.run_job(root, 'pair', producer=producer, reviewer=reviewer, apply=False)
    assert calls == [('producer', [EXISTING, second_id]), ('reviewer', [EXISTING, second_id]),
                     ('producer', [second_id]), ('reviewer', [second_id])]
    assert all(p.read_bytes() == raw for p, raw in before.items())
    decisions = classification.reviewed_rows_with_repairs(root, 'pair')
    assert all(x['finding']['decision'] == 'accept' for x in decisions)
    assert decisions[0]['job_id'] == 'pair' and decisions[1]['job_id'] == result['repair_child']
    runner.run_job(root, 'pair', producer=producer, reviewer=reviewer, apply=True)
    runner.run_job(root, 'pair', producer=producer, reviewer=reviewer, apply=True)
    assert len(calls) == 4
    assert all(p.read_bytes() == raw.replace(b'pillar: null', ('pillar: ' + PILLAR).encode())
               .replace(b'topic: null', ('topic: ' + TOPIC).encode()) for p, raw in before.items())


def test_proposal_only_does_not_resume_interrupted_knowledge_write(tmp_path, monkeypatch):
    root, path, row, review = ready(tmp_path)
    submit_reviewed(root, 'classify', {'items': [row]}, review)
    real = jobs.apply_changes
    with monkeypatch.context() as patch:
        patch.setattr(jobs, 'apply_changes', lambda *a, **kw: real(*a, **kw, _crash_after=1))
        with pytest.raises(RuntimeError):
            classification.apply(root, 'classify')
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    idle = FakeExecutor('gpt-test', outputs={})
    result = runner.run_job(root, 'classify', producer=idle, reviewer=idle, apply=False)
    assert result['status'] == 'applying'
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert runner.run_job(root, 'classify', producer=idle, reviewer=idle, apply=True)['status'] == 'applied'
    assert idle.calls == []


def test_second_controller_on_the_same_job_is_refused(tmp_path):
    from secondbrain.locks import job_lock
    from secondbrain.store import Conflict
    import threading
    root, _, row, review = ready(tmp_path)
    errors = []
    with job_lock(root, 'classify'):
        def other():
            try:
                with job_lock(root, 'classify'):
                    pass
            except Conflict as exc:
                errors.append(exc)
        t = threading.Thread(target=other)
        t.start()
        t.join()
        with job_lock(root, 'classify'):  # re-entrant for the holder
            pass
    assert errors and 'already being driven' in str(errors[0])


def test_taxonomy_job_repairs_in_place_once_then_applies(tmp_path):
    from secondbrain import curation
    from tests.test_curation import (DOM, EXISTING as CEXISTING, MOC as CMOC, PILLAR as CPILLAR,
                                     TOPIC as CTOPIC, build_vault as cbuild, item, make_catalog,
                                     proposal, single_taxonomy)
    root = cbuild(tmp_path, catalog=make_catalog({DOM: CMOC}),
                  cards={CEXISTING: ("已有卡", DOM, CPILLAR, CTOPIC)},
                  taxonomies={CMOC: single_taxonomy()})
    curation.prepare(root, [DOM], job_id='tax')
    seen = []

    def produce(task):
        seen.append(task.step)
        narrative = '第二版叙事' if task.step == 'repair' else '第一版叙事'
        return proposal([item(DOM, single_taxonomy(narrative=narrative), [CEXISTING])])

    def review(task):
        ok = task.packet['candidate']['items'][0]['taxonomy']['narrative'] == '第二版叙事'
        assert 'reason' not in task.packet['candidate']['items'][0]
        return {'items': [{'domain': DOM, 'decision': 'accept' if ok else 'revise',
                           'reason': '叙事已解释学习顺序' if ok else '叙事没有解释学习顺序'}]}
    result = runner.run_job(root, 'tax', producer=FakeExecutor('gpt-test', responder=produce),
                            reviewer=FakeExecutor('claude-test', responder=review))
    assert seen == ['produce', 'repair']
    assert result['status'] == 'applied' and result['repair_round'] == 1
    assert '第二版叙事' in (root / f'20_MOCs/_pillars/{CMOC}.yaml').read_text(encoding='utf-8')

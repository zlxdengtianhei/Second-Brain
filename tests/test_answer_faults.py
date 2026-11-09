"""Any error while an answer is read or checked goes through the one gate, and the owner sees it as the program's.

The sixth review found an error the repair step raised (a ``RuntimeError``) that ``jobs.submit`` did not count, as it
caught only ``ValueError``: in API mode each advance failed until the job did, and in host mode the run could be
submitted again without end. It was the second time a new path went round the gate. Now every error while an answer
is checked at submission (the kind's check, registered for every kind that asks a model) and while an API answer is
read is counted against the slot's budget like a refused answer, a host run is closed when the budget is spent, and
an error that is not a refusal of the answer (not a ``ValueError``) is kept as the program's (``program_errors``)
and listed by ``sb status --owner-items``. The curation, relocation and domain-creation answers, which were first
checked after they were kept (so a failing one stopped every later run uncounted), are checked at submission now.

The table is read from the code, as in tests/test_refusal_gate.py: every kind that asks a model and every role, in API
and host mode. A kind without a check at submission, or a role added without an entry, fails it.
"""
from __future__ import annotations

import copy
import importlib
import json

import pytest

from secondbrain import cli, config, jobs, runner
from secondbrain.executors import ExecutorFailure, FakeExecutor
from secondbrain.executors.base import Result, Task
from secondbrain.executors.host import HostExecutor
from secondbrain.pipelines import answerfix, ingest
from secondbrain.service import Service
from secondbrain.store import Conflict
from tests.helpers import build_vault
from tests.ingest_helpers import first_candidate, scripted
from tests.test_answer_repair import accept_all, lantern_job
from tests.test_refusal_gate import CASES, asks, counted

LIMIT = config.ReviewPolicy().max_invalid_retries + 1


def program_errors(root):
    return [i for i in Service(root).owner_items() if i.get('kind') == 'program_error']


def test_every_kind_that_asks_a_model_checks_its_answers_at_the_gate():
    asking = {k for k, asks_ in jobs.KINDS.items() if asks_}
    assert asking == set(jobs.SUBMISSION_VALIDATORS)


@pytest.mark.parametrize('kind, slot, role, step, mode', CASES,
                         ids=[f'{k}-{s or "job"}-{r}-{st}-{m}' for k, s, r, st, m in CASES])
def test_an_error_while_an_answer_is_checked_is_counted_closes_a_host_run_and_is_listed(
        tmp_path, monkeypatch, kind, slot, role, step, mode):
    root = build_vault(tmp_path / 'vault')  # a vault, so the owner list can be read
    job_id = f'{kind}-job'
    jobs.create(root, job_id, kind, {'settings': None, 'cards': []})
    if role == 'reviewer':
        jobs.put_program_candidate(root, job_id, slot, {'stand_in': True})
    module, name = jobs.SUBMISSION_VALIDATORS[kind].split(':')

    def broken(*args, **kwargs):
        raise RuntimeError('the check itself broke')
    monkeypatch.setattr(importlib.import_module(module), name, broken)
    limit = asks(kind, slot, role)
    answer = tmp_path / 'answer.json'
    answer.write_text(json.dumps({'items': []}), encoding='utf-8')
    if mode == 'host':  # one run, answered through `sb job submit` until the budget is spent; then it is closed
        jobs.start_run(root, job_id, role=role, step=step, executor='host', run_id='r1', model_requested='m',
                       chunk=slot)
        codes = [cli.main(['--vault', str(root), 'job', 'submit', job_id, '--role', role, '--run-id', 'r1',
                           '--file', str(answer), '--model', 'm']) for _ in range(limit)]
        assert all(code != 0 for code in codes)
        run = jobs.load_run(root, job_id, 'r1')
        assert run['status'] == 'failed' and run['error']['given_up'] is True
        with pytest.raises(Conflict, match='TERMINAL_RUN'):
            jobs.submit(root, job_id, {'items': []}, role=role, run_id='r1')
    else:  # each API answer is a run of its own, refused through finish_call
        for n in range(limit):
            run = jobs.start_run(root, job_id, role=role, step=step, executor='fake', run_id=f'r{n}',
                                 model_requested='m', chunk=slot)
            task = Task(job_id=job_id, run_id=run['run_id'], role=role, step=step, prompt='p', packet={},
                        packet_sha256=jobs.load(root, job_id)['input_sha256'], output_schema={}, chunk=slot)
            with pytest.raises(ExecutorFailure) as failure:
                runner.finish_call(root, task, Result(status='completed', output={'items': []}))
            assert failure.value.error.get('counted') and jobs.load_run(root, job_id, run['run_id'])['status'] == \
                'failed'
    assert counted(root, job_id, kind, slot, role, step) == limit
    [item] = program_errors(root)
    assert (item['job_id'], item['slot'], item['role'], item['step'], item['error'], item['times']) == \
        (job_id, slot, role, step, 'RuntimeError', limit)
    assert item['message'] == 'the check itself broke'


def test_an_api_answer_the_program_fails_to_read_is_counted_and_listed(tmp_path):
    """An error while the executor reads the provider's answer is the same: counted, the program's, listed."""
    from secondbrain.config import EndpointConfig
    from secondbrain.executors.api import ApiExecutor

    class Response:
        status_code, text, headers = 200, '{}', {}

        @staticmethod
        def json():
            return {'choices': 'not a list'}  # the reader indexes it and fails
    executor = ApiExecutor(EndpointConfig(base_url='http://x', model='gpt-test', api_key_env='K'))
    task = Task(job_id='j', run_id='r', role='producer', step='produce', prompt='p', packet={}, packet_sha256='s',
                output_schema={})
    result = executor._parse(Response(), task)
    assert result.status == 'failed' and result.error['code'] == 'INVALID_OUTPUT'
    assert result.error['program_error']['type'] in {'AttributeError', 'TypeError'}
    root = build_vault(tmp_path / 'vault')
    jobs.create(root, 'classify-job', 'classification', {'settings': None, 'cards': []})
    run = jobs.start_run(root, 'classify-job', role='producer', step='produce', executor='api', run_id='r1',
                         model_requested='gpt-test')
    task = Task(job_id='classify-job', run_id=run['run_id'], role='producer', step='produce', prompt='p', packet={},
                packet_sha256=jobs.load(root, 'classify-job')['input_sha256'], output_schema={})
    with pytest.raises(ExecutorFailure):
        runner.finish_call(root, task, result)
    assert jobs.load(root, 'classify-job')['refused']['producer@0']['count'] == 1
    [item] = program_errors(root)
    assert item['error'] == result.error['program_error']['type'] and item['step'] == 'produce'


# --- in a job: the repair step fails on every answer -------------------------------------------------------------------
def failing_repair(monkeypatch):
    def fail(payload, ctx, rules=None):
        raise answerfix.RepairFault('a repair changed the knowledge text; nothing was kept')
    monkeypatch.setattr(answerfix, 'repair', fail)


def test_in_host_mode_the_same_answer_submitted_four_times_closes_the_run(tmp_path, monkeypatch):
    root, job_id, _ = lantern_job(tmp_path)
    failing_repair(monkeypatch)
    host = HostExecutor()
    out = ingest.advance(root, job_id, producer=host, reviewer=host)
    [h] = [x for x in out['handoffs'] if x['role'] == 'producer']
    path = tmp_path / 'answer.json'
    path.write_text(json.dumps(copy.deepcopy(first_candidate())), encoding='utf-8')
    results = []
    for _ in range(4):
        try:
            results.append(cli.main(['--vault', str(root), 'job', 'submit', job_id, '--role', 'producer',
                                     '--run-id', h['run_id'], '--file', str(path), '--model', 'gpt-test']))
        except Conflict as closed:
            results.append('closed' if 'TERMINAL_RUN' in str(closed) else str(closed))
    run = jobs.load_run(root, job_id, h['run_id'])
    assert run['status'] == 'failed' and run['error']['given_up'] is True and run['rejected_submits'] == LIMIT
    assert results[:LIMIT] == [results[0]] * LIMIT and results[0] != 0 and results[LIMIT] != 0
    info = jobs.load(root, job_id)['chunks']['c01']
    assert ingest._refusals(info, ingest.ANSWER_ROLES[0]) == LIMIT
    [item] = program_errors(root)
    assert (item['error'], item['times'], item['step']) == ('RepairFault', LIMIT, 'produce')
    done = ingest.advance(root, job_id, producer=host, reviewer=host)
    assert 'the program failed while checking this answer' in done['error']['pending'][0]['reason']


def test_in_api_mode_the_job_goes_on_and_the_chunk_waits_for_the_owner(tmp_path, monkeypatch):
    root, job_id, _ = lantern_job(tmp_path)
    failing_repair(monkeypatch)
    produce, review, seen = scripted([copy.deepcopy(first_candidate())] * LIMIT, [accept_all])
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=review))  # no error comes out of it
    assert len(seen['producer']) == LIMIT
    assert 'the program failed while checking this answer (RepairFault' in done['error']['pending'][0]['reason']
    [item] = program_errors(root)
    assert item['times'] == LIMIT


def test_a_maintenance_proposal_that_fails_its_check_is_refused_at_submission_and_asked_again(tmp_path):
    """A curation proposal the check refuses is counted and asked for again; before, it was kept, and every later
    run of the job stopped on it, uncounted."""
    from secondbrain import curation
    from tests.test_curation import DOM, EXISTING, MOC, PILLAR, TOPIC, build_vault as cbuild, item, make_catalog, \
        proposal, single_taxonomy
    root = cbuild(tmp_path, catalog=make_catalog({DOM: MOC}), cards={EXISTING: ('已有卡', DOM, PILLAR, TOPIC)},
                  taxonomies={MOC: single_taxonomy()})
    curation.prepare(root, [DOM], job_id='tax')
    asked = []

    def produce(task):
        asked.append(task.step)
        return proposal([item('nowhere', single_taxonomy(), [EXISTING])])
    for _ in range(LIMIT + 1):
        try:
            state = runner.run_job(root, 'tax', producer=FakeExecutor('gpt-test', responder=produce),
                                   reviewer=FakeExecutor('claude-test', responder=lambda task: {'items': []}))
        except ExecutorFailure:
            continue
        if state.get('escalation_required'):
            break
    state = jobs.load(root, 'tax')  # counted per round, as every maintenance refusal (an invalid answer spends a round)
    assert not state.get('candidate') and state['escalation_required']
    assert sum(row['count'] for row in state['refused'].values()) == len(asked) >= 2
    assert all('must cover every prepared domain' in row['message'] for row in state['refused'].values())

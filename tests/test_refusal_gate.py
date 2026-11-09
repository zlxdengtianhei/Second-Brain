"""Every answer that fails the program's checks, of every job kind and role, in API and host mode, is counted at one gate.

The fifth verdict found the format budget still had gaps outside the ingest table: in host mode a submission that is
not a JSON object, or a file that is not JSON, was refused without being counted (probe p2), so a sub-agent could
submit for ever; export counted with a second copy of the code; a host run given up without an answer counted only
for the merge check; and the maintenance kinds (classification, curation, relocation, domcreate) had no budget for
their reviewer at all. Now every refused answer goes through one count (``jobs.receive``; the validator's refusal, a payload that is
not an object, a file that is not JSON, an executor's answer that is no JSON object, a run given up without an
answer), each kind counts it with ``budget.count`` against ``max_invalid_retries``, and a host run whose slot is given
up is closed.

The tables here are read from the code: every job kind the package creates (``jobs.KINDS``, checked against the
``jobs.create`` calls in the source), and for each kind that asks a model, every role (ingest's ``ANSWER_ROLES``,
export's ``STEPS``, a maintenance job's producer and reviewer). A kind or role added later without an entry fails.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from secondbrain import cli, config, jobs, runner
from secondbrain.executors import ExecutorFailure, FakeExecutor
from secondbrain.executors.host import HostExecutor
from secondbrain.export import pipeline as export
from secondbrain.pipelines import ingest
from secondbrain.store import Conflict
from tests.ingest_helpers import packet_in
from tests.test_classification import ready
from tests.test_patch_set import job, producer

PACKAGE = Path(__file__).resolve().parents[1] / 'src' / 'secondbrain'
LIMIT = config.ReviewPolicy().max_invalid_retries + 1

#: For each kind that asks a model: (slot, role, step) of every role, read from the code that defines them.
ROLES = {
    'ingest': [(slot, role, 'produce' if role == 'producer' else 'review')
               for slot, role in (('c01', 'producer'), ('c01', 'reviewer'), ('xc01', 'reviewer'),
                                  ('xr1-a-b', 'reviewer'), ('sp01', 'producer'), ('sp01', 'reviewer'),
                                  ('nt-concepts', 'producer'), ('dg01', 'producer'), ('nc-c01', 'reviewer'))],
    'export': [('u01', 'reviewer' if step == 'review' else 'producer', step) for step in export.STEPS],
    **{kind: [(None, 'producer', 'produce'), (None, 'reviewer', 'review')]
       for kind in ('classification', 'curation', 'relocation', 'domcreate')},
}


def created_kinds() -> set:
    """Every kind a ``jobs.create`` call in the package names (a literal, a module constant, or ``jobs.<NAME>``)."""
    found = set()
    for path in PACKAGE.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
                  for t in n.targets if isinstance(t, ast.Name) and isinstance(n.value, ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'create' \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == 'jobs' and len(node.args) >= 3:
                kind = node.args[2]
                if isinstance(kind, ast.Constant):
                    found.add(kind.value)
                elif isinstance(kind, ast.Name):
                    found.add(consts[kind.id])
                elif isinstance(kind, ast.Attribute) and isinstance(kind.value, ast.Name) and kind.value.id == 'jobs':
                    found.add(getattr(jobs, kind.attr))
                else:
                    raise AssertionError(f'{path.name}: a jobs.create call whose kind this test cannot read')
    return found


def test_every_job_kind_is_declared_and_every_kind_that_asks_a_model_has_a_counter_and_its_roles_here():
    assert created_kinds() == set(jobs.KINDS)
    asking = {k for k, asks in jobs.KINDS.items() if asks}
    assert asking == set(jobs.REFUSAL_RECORDERS) == set(ROLES)
    assert sorted({ingest.answer_role(slot, role).name for slot, role, _ in ROLES['ingest']}) == \
        sorted(r.name for r in ingest.ANSWER_ROLES)
    for kind in asking - {'ingest', 'export'}:
        assert runner._spec(kind) and jobs.REFUSAL_RECORDERS[kind] == 'secondbrain.runner:record_refusal'


CASES = [(kind, slot, role, step, mode) for kind, rows in ROLES.items() for slot, role, step in rows
         for mode in ('api', 'host')]


@pytest.mark.parametrize('kind, slot, role, step, mode', CASES,
                         ids=[f'{k}-{s or "job"}-{r}-{st}-{m}' for k, s, r, st, m in CASES])
def test_each_way_an_answer_is_refused_is_counted_once_and_a_host_run_is_closed_when_the_budget_is_spent(
        tmp_path, kind, slot, role, step, mode):
    """Three refused answers of one run, by the ways in that need no pipeline (a submission that is not an object, a
    file that is not JSON): the count the kind reads is 3, the budget is spent, and a host run is closed; an API run
    is left to its caller. A run given up without an answer counts the same. (The validator's refusal is counted
    at the same gate; the ingest and export tables drive it, tests/test_answer_budget.py and test_export_budget.py.)"""
    root = tmp_path / 'vault'
    root.mkdir()
    job_id = f'{kind}-job'
    jobs.create(root, job_id, kind, {'settings': None, 'cards': []})
    if role == 'reviewer':  # a review answers a candidate: a stand-in the program made
        jobs.put_program_candidate(root, job_id, slot, {'stand_in': True})
    executor = 'host' if mode == 'host' else 'fake'
    limit = asks(kind, slot, role)

    def start(run_id):
        return jobs.start_run(root, job_id, role=role, step=step, executor=executor, run_id=run_id,
                              model_requested='m', chunk=slot)
    start('r1')
    ways = [lambda: jobs.submit(root, job_id, ['not', 'an', 'object'], role=role, run_id='r1'),
            lambda: jobs.receive(root, job_id, 'r1', role=role, raw=b'the answer file is not JSON')]
    for n in range(limit):
        try:
            ways[n % 2]()
        except ValueError as refused:
            assert getattr(refused, 'counted', False), refused
    run = jobs.load_run(root, job_id, 'r1')
    assert run['rejected_submits'] == limit == counted(root, job_id, kind, slot, role, step)
    if mode == 'host':  # the budget is spent: the run is closed, and nothing more is counted
        assert run['status'] == 'failed' and run['error']['given_up'] is True
        for way in ways:
            with pytest.raises(Conflict, match='TERMINAL_RUN'):
                way()
    else:  # an API run is left to its caller, which fails it
        assert run['status'] == 'started'
        jobs.fail_run(root, job_id, 'r1', error={'code': 'INVALID_OUTPUT', 'message': 'x', 'counted': True})
    assert counted(root, job_id, kind, slot, role, step) == limit
    start('r2')
    jobs.abandon_run(root, job_id, 'r2', reason='the sub-agent is gone')
    assert counted(root, job_id, kind, slot, role, step) == limit + 1


def asks(kind, slot, role) -> int:
    """How many answers of the role one slot may have refused: its budget, ``max_invalid_retries`` + 1 (a diagram
    asks again in a rewrite slot of its own instead)."""
    if kind == 'ingest':
        return ingest.answer_role(slot, role).retries(config.ReviewPolicy()) + 1
    return LIMIT


def counted(root, job_id, kind, slot, role, step) -> int:
    """The count the kind itself reads to give a slot up."""
    state = jobs.load(root, job_id)
    if kind == 'ingest':
        return ingest._refusals(state['chunks'][slot], ingest.answer_role(slot, role))
    if kind == 'export':
        return state['chunks'][slot]['refused'][f'{step}@0']['count']
    return state['refused'][f'{role}@0']['count']


# --- a maintenance job, driven as a whole: its reviewer's answers keep failing -----------------------------------------
def classification_job(tmp_path):
    root, _, row, _ = ready(tmp_path)
    return root, FakeExecutor('gpt-test', responder=lambda task: {'items': [
        {**row, 'id': c['id'], 'input_sha256': c['sha256']} for c in task.packet.get('input', task.packet)['cards']]})


def test_a_maintenance_review_that_keeps_failing_is_asked_within_the_budget_then_the_job_waits(tmp_path):
    root, produce = classification_job(tmp_path)
    asked = []

    def review(task):
        asked.append(task.run_id)
        return ['not an object']
    for _ in range(LIMIT):
        with pytest.raises(ExecutorFailure):
            runner.run_job(root, 'classify', producer=produce, reviewer=FakeExecutor('claude-test', responder=review))
    state = runner.run_job(root, 'classify', producer=produce, reviewer=FakeExecutor('claude-test', responder=review))
    assert len(asked) == LIMIT and state['escalation_required']
    assert f'the reviewer answer failed validation {LIMIT} times' in state['escalation_reason']
    assert state['status'] != 'applied' and not state.get('review')
    runner.run_job(root, 'classify', producer=produce, reviewer=FakeExecutor('claude-test', responder=review))
    assert len(asked) == LIMIT  # never asked again


def test_in_host_mode_a_maintenance_review_refused_three_times_closes_its_run_and_the_job_waits(tmp_path):
    root, produce = classification_job(tmp_path)
    host = HostExecutor()
    runner.run_job(root, 'classify', producer=produce, reviewer=host)  # the producer answers; the review is handed
    out = runner.run_job(root, 'classify', producer=produce, reviewer=host)
    handoff = out['handoff']
    results = []
    for _ in range(6):
        try:
            jobs.submit(root, 'classify', {'items': 'x'}, role='reviewer', run_id=handoff['run_id'])
        except Conflict as closed:
            results.append('closed' if 'TERMINAL_RUN' in str(closed) else str(closed))
        except ValueError:
            results.append('refused')
    assert results == ['refused'] * LIMIT + ['closed'] * (6 - LIMIT)
    state = runner.run_job(root, 'classify', producer=produce, reviewer=host)
    assert state['escalation_required'] and state.get('status') != 'awaiting_submission'
    assert jobs.load_run(root, 'classify', handoff['run_id'])['error']['given_up'] is True


# --- the fifth verdict's probe p2: a host submission that is not an object, or a file that is not JSON ---------------
@pytest.mark.parametrize('how', ['a JSON list through jobs.submit', 'a file that is not JSON through sb job submit'])
def test_p2_a_host_answer_that_is_not_a_json_object_is_counted_and_its_run_closed(tmp_path, how):
    root, job_id, _ = job(tmp_path)
    host, produce = HostExecutor(), producer()
    out = ingest.advance(root, job_id, producer=host, reviewer=host)
    for h in out['handoffs']:  # the chunk producers answer well
        text = open(h['packet_path'], encoding='utf-8').read()
        task = SimpleNamespace(packet=packet_in(text), chunk=h['chunk'], role=h['role'], prompt=text, step=h['step'])
        jobs.submit(root, job_id, produce(task), role='producer', run_id=h['run_id'], model_reported='gpt-test')
    out = ingest.advance(root, job_id, producer=host, reviewer=host)
    h = next(x for x in out['handoffs'] if x['role'] == 'reviewer')
    results = []
    for i in range(6):
        if how.startswith('a JSON list'):
            try:
                jobs.submit(root, job_id, [], role='reviewer', run_id=h['run_id'], model_reported='claude-test')
                results.append('accepted')
            except Conflict as exc:
                results.append('closed' if 'TERMINAL_RUN' in str(exc) else str(exc))
            except ValueError:
                results.append('refused')
        else:
            answer = tmp_path / f'answer{i}.json'
            answer.write_text('{"cards": [', encoding='utf-8')
            code = cli.main(['--vault', str(root), 'job', 'submit', job_id, '--role', 'reviewer',
                             '--run-id', h['run_id'], '--file', str(answer), '--model', 'claude-test'])
            results.append(code)
    info = jobs.load(root, job_id)['chunks'][h['chunk']]
    run = jobs.load_run(root, job_id, h['run_id'])
    assert info['invalid_reviews'] == LIMIT and info['given_up'] and run['status'] == 'failed'
    assert run['error']['given_up'] is True
    if how.startswith('a JSON list'):
        assert results == ['refused'] * LIMIT + ['closed'] * (6 - LIMIT)
    else:
        assert all(code != 0 for code in results)

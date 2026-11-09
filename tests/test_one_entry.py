"""Every answer is taken through one entry point, ``jobs.receive``, and an error at any of its steps is counted.

Three reviews in a row found an error while an answer was taken that went past the gate, each time on a path the last
fix had not named (a host submission that was not an object; an error of the repair step; then the decision recorder,
the host's reading of a file, an answer past 8 MB, and fields only the program writes copied into an answer). The
cause was the same each time: the gate was a ``try`` written at each place that took an answer. Now a host's file
bytes (`sb job submit`), an object (the MCP tool, the service) and an API executor's result all go to
``jobs.receive``, whose steps (read, size, program fields, check, store, record) run in one ``try``: an error of the
answer is refused and counted, any other error is counted as a fault of the program and listed for the owner.

The table is read from the code: every kind that asks a model and every role (tests/test_refusal_gate.py), each of
the three sources, each step of ``jobs.RECEIVE_STEPS``. A step or a source added without going through the entry
point, or a second way into the checks, fails here. The cards and answers are invented.
"""
from __future__ import annotations

import ast
import copy
import importlib
import json
from pathlib import Path

import pytest

from secondbrain import jobs, runner
from secondbrain.executors import ExecutorFailure, FakeExecutor
from secondbrain.executors.base import Result, Task
from secondbrain.executors.host import HostExecutor
from secondbrain.pipelines import ingest
from secondbrain.service import Service
from secondbrain.store import Conflict
from tests.helpers import build_vault
from tests.ingest_helpers import first_candidate, scripted
from tests.test_answer_repair import accept_all, lantern_job
from tests.test_refusal_gate import ROLES, asks, counted

PACKAGE = Path(__file__).resolve().parents[1] / 'src' / 'secondbrain'
SOURCES = ('file', 'object', 'api')
ROWS = [(kind, slot, role, step, source, at) for kind, rows in ROLES.items() for slot, role, step in rows
        for source in SOURCES for at in jobs.RECEIVE_STEPS]


def program_errors(root):
    return [i for i in Service(root).owner_items() if i.get('kind') == 'program_error']


def test_the_steps_of_the_entry_point_are_the_ones_this_table_breaks():
    assert jobs.RECEIVE_STEPS == ('read', 'size', 'fields', 'check', 'store', 'record')


def broken(*args, **kwargs):
    raise RuntimeError('a step of the entry point broke')


def break_step(monkeypatch, kind, at):
    """Break the one step ``at`` of ``jobs.receive``; the kind's check passes the answer on in the other cases, and
    no decision is recorded (the bare job here has nothing for a recorder to read)."""
    module, name = jobs.SUBMISSION_VALIDATORS[kind].split(':')
    monkeypatch.setattr(importlib.import_module(module), name,
                        broken if at == 'check' else (lambda packet, payload, **kw: payload))
    monkeypatch.setattr(jobs, '_recorder', (lambda kind: broken) if at == 'record' else (lambda kind: None))
    if at == 'read':
        monkeypatch.setattr(jobs, '_read', broken)
    elif at == 'size':
        monkeypatch.setattr(jobs, 'encoded', broken)
    elif at == 'fields':
        monkeypatch.setattr(jobs, '_without_program_fields', broken)
    elif at == 'store':
        write = jobs.write_json

        def write_json(root, rel, value):
            if '/out/' in rel:
                raise RuntimeError('a step of the entry point broke')
            return write(root, rel, value)
        monkeypatch.setattr(jobs, 'write_json', write_json)


@pytest.mark.parametrize('kind, slot, role, step, source, at', ROWS,
                         ids=[f'{k}-{s or "job"}-{r}-{so}-{a}' for k, s, r, _, so, a in ROWS])
def test_an_error_at_any_step_of_the_entry_point_is_counted_and_listed(tmp_path, monkeypatch, kind, slot, role, step,
                                                                       source, at):
    root = build_vault(tmp_path / 'vault')
    job_id = f'{kind}-job'
    jobs.create(root, job_id, kind, {'settings': None, 'cards': []})
    if role == 'reviewer':
        jobs.put_program_candidate(root, job_id, slot, {'stand_in': True})
    break_step(monkeypatch, kind, at)
    limit = asks(kind, slot, role)
    service = Service(root)
    if source in ('file', 'object'):  # a host run: answered until the budget is spent, then closed
        jobs.start_run(root, job_id, role=role, step=step, executor='host', run_id='r1', model_requested='m',
                       chunk=slot)
        for _ in range(limit):
            with pytest.raises(ValueError) as refused:
                if source == 'file':
                    service.submit(job_id, role=role, run_id='r1', raw=json.dumps({'items': []}).encode())
                else:
                    service.submit(job_id, {'items': []}, role=role, run_id='r1')
            assert refused.value.counted
        run = jobs.load_run(root, job_id, 'r1')
        assert run['status'] == 'failed' and run['error']['given_up'] is True
        with pytest.raises(Conflict, match='TERMINAL_RUN'):
            service.submit(job_id, {'items': []}, role=role, run_id='r1')
    else:  # each API answer is a run of its own
        for n in range(limit):
            run = jobs.start_run(root, job_id, role=role, step=step, executor='fake', run_id=f'r{n}',
                                 model_requested='m', chunk=slot)
            task = Task(job_id=job_id, run_id=run['run_id'], role=role, step=step, prompt='p', packet={},
                        packet_sha256=jobs.load(root, job_id)['input_sha256'], output_schema={}, chunk=slot)
            with pytest.raises(ExecutorFailure) as failure:
                runner.finish_call(root, task, Result(status='completed', output={'items': []}))
            assert failure.value.error.get('counted')
            assert jobs.load_run(root, job_id, run['run_id'])['status'] == 'failed'
    assert counted(root, job_id, kind, slot, role, step) == limit
    [item] = program_errors(root)
    assert (item['job_id'], item['slot'], item['role'], item['stage'], item['error'], item['times']) == \
        (job_id, slot, role, at, 'RuntimeError', limit)
    state = jobs.load(root, job_id)  # what the failed steps did is not kept: no answer, no file
    info = state if slot is None else (state.get('chunks') or {}).get(slot) or {}
    assert not info.get('review' if role == 'reviewer' else 'candidate')
    runs = ['r1'] if source in ('file', 'object') else [f'r{n}' for n in range(limit)]
    assert all(jobs.read_state(root, jobs.location(job_id, 'out', f'{role}-{r}.json')) is None for r in runs)


# --- one way in --------------------------------------------------------------------------------------------------------
def calls(tree, owner, names):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in names and isinstance(n.func.value, ast.Name) and n.func.value.id == owner]


def test_the_package_takes_answers_only_through_the_entry_point():
    """Outside ``jobs``, nothing in the package calls ``jobs.submit`` or ``jobs.refuse``; the CLI, the MCP tool and
    the service go through ``Service.submit``, which calls ``jobs.receive``, and the runner calls ``jobs.receive``.
    Inside ``jobs``, only ``receive`` runs a kind's check or its recorder, so there is no second way in."""
    callers = {}
    for path in PACKAGE.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        rel = path.relative_to(PACKAGE).as_posix()
        assert not calls(tree, 'jobs', {'submit', 'refuse', '_refused_answer'}), rel
        if calls(tree, 'jobs', {'receive'}):
            callers[rel] = len(calls(tree, 'jobs', {'receive'}))
    assert callers == {'runner.py': 1, 'service.py': 1}
    tree = ast.parse((PACKAGE / 'jobs.py').read_text(encoding='utf-8'))
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and fn.name != 'receive':
            used = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            assert not used & {'_validator', '_recorder', '_read'}, fn.name
    for path in ('cli.py', 'mcp_server.py'):
        tree = ast.parse((PACKAGE / path).read_text(encoding='utf-8'))
        assert calls(tree, 'service', {'submit'}), path


# --- the sixth review's probes, as regressions ---------------------------------------------------------------------------
RESERVED = {
    'held_str': lambda a, c: a.__setitem__('held_cards', 'x'),
    'held_ghost': lambda a, c: a.__setitem__('held_cards', [{'slug': 'ghost', 'title': 'Ghost', 'problems': ['p']}]),
    'held_noslug': lambda a, c: a.__setitem__('held_cards', [{'title': 'Ghost'}]),
    'repairs_str': lambda a, c: a.__setitem__('program_repairs', 'x'),
    'repairs_list_str': lambda a, c: a.__setitem__('program_repairs', ['x']),
    'card_turned': lambda a, c: c.__setitem__('turned', 'x'),
    'card_held': lambda a, c: c.__setitem__('held_cards', 'x'),
    'held_real': lambda a, c: a.__setitem__('held_cards', [{'slug': 'lantern-queue', 'title': 'Lantern queue',
                                                            'problems': ['p']}]),
}


@pytest.mark.parametrize('variant', sorted(RESERVED))
def test_an_answer_with_fields_only_the_program_writes_is_taken_without_them(tmp_path, variant):
    root, job_id, _ = lantern_job(tmp_path)
    answer = copy.deepcopy(first_candidate())
    RESERVED[variant](answer, answer['cards'][0])
    produce, review, _ = scripted([answer], [accept_all])
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=review))
    assert done['status'] == 'applied' and done['pending'] == []
    assert set(done['outcomes']) == {c['slug'] for c in first_candidate()['cards']}
    cand = jobs.artifact(root, jobs.load(root, job_id), 'candidate', 'c01')
    assert 'held_cards' not in cand and 'program_repairs' not in cand and 'turned' not in cand['cards'][0]
    removed = [r for r in done['program_repairs'] if r['rule'] == 'program_fields']
    assert removed and removed[0]['removed']


def test_a_review_row_with_a_turned_field_is_taken_without_it_and_the_job_goes_on(tmp_path):
    root, job_id, _ = lantern_job(tmp_path)

    def review(task):
        answer = accept_all(task)
        answer['cards'][0]['turned'] = 'x'
        return answer
    produce, _, _ = scripted([copy.deepcopy(first_candidate())], [])
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=review))
    assert done['status'] == 'applied' and done['pending'] == []


def test_a_redo_round_shows_the_producer_its_last_answer_without_the_program_fields(tmp_path):
    root, job_id, prefix = lantern_job(tmp_path)
    from tests.test_answer_repair import slipped

    def first_round_revises(task):
        answer = accept_all(task)
        if task.chunk == 'c01':
            answer['cards'][0].update(decision='revise', reason='say more')
            answer['cards'][0].pop('overlap', None)
        return answer
    seen = []

    def produce(task):
        seen.append(task)
        return copy.deepcopy(slipped(prefix, bad=()) if task.chunk == 'c01' else {
            'cards': [first_candidate()['cards'][0]], 'new_topics': [], 'coverage': first_candidate()['coverage'][:1]})
    ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                   reviewer=FakeExecutor('claude-test', responder=first_round_revises))
    redo = next(t for t in seen if t.chunk == 'c01.r1')
    assert jobs.artifact(root, jobs.load(root, job_id), 'candidate', 'c01').get('program_repairs')
    assert 'program_repairs' not in redo.packet['previous_candidate']
    assert 'held_cards' not in redo.packet['previous_candidate']


def test_a_broken_decision_recorder_is_counted_in_host_mode_and_the_run_closes(tmp_path, monkeypatch):
    root, job_id, _ = lantern_job(tmp_path)
    host = HostExecutor()
    produce, _, _ = scripted([copy.deepcopy(first_candidate())], [])
    out = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce), reviewer=host)
    [h] = [x for x in out['handoffs'] if x['role'] == 'reviewer']
    cand = jobs.artifact(root, jobs.load(root, job_id), 'candidate', 'c01')
    answer = accept_all(type('T', (), {'packet': {'candidate': cand}, 'chunk': 'c01'})())
    monkeypatch.setattr(ingest, 'record_decisions', broken)
    results = []
    for _ in range(4):
        try:
            Service(root).submit(job_id, role='reviewer', run_id=h['run_id'], raw=json.dumps(answer).encode())
            results.append('taken')
        except Conflict as closed:
            results.append('closed' if 'TERMINAL_RUN' in str(closed) else str(closed))
        except ValueError as refused:
            results.append('counted' if refused.counted else 'refused')
    assert results == ['counted', 'counted', 'counted', 'closed']
    [item] = program_errors(root)
    assert (item['stage'], item['error'], item['times']) == ('record', 'RuntimeError', 3)
    assert not jobs.load(root, job_id)['chunks']['c01'].get('review')


def test_a_broken_decision_recorder_in_api_mode_leaves_the_chunk_to_the_owner(tmp_path, monkeypatch):
    root, job_id, _ = lantern_job(tmp_path)
    monkeypatch.setattr(ingest, 'record_decisions', broken)
    produce, review, _ = scripted([copy.deepcopy(first_candidate()) for _ in range(4)], [accept_all] * 4)
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=review))  # no error comes out of it
    assert done['status'] == 'failed' and 'the program failed' in done['error']['pending'][0]['reason']
    assert program_errors(root)[0]['stage'] == 'record'


@pytest.mark.parametrize('mode', ['host', 'api'])
def test_an_answer_past_8_mb_or_nested_too_deep_is_counted_in_both_modes(tmp_path, mode):
    root, job_id, _ = lantern_job(tmp_path)
    large = copy.deepcopy(first_candidate())
    large['cards'][0]['essence'] += ' ' + 'x' * 8_100_000
    deep = '{"cards": ' + '[' * 200_000 + ']' * 200_000 + '}'
    if mode == 'host':
        host = HostExecutor()
        out = ingest.advance(root, job_id, producer=host, reviewer=host)
        [h] = [x for x in out['handoffs'] if x['role'] == 'producer']
        for text in (json.dumps(large), deep):
            with pytest.raises(ValueError) as refused:
                Service(root).submit(job_id, role='producer', run_id=h['run_id'], raw=text.encode())
            assert refused.value.counted
    else:
        from secondbrain.config import EndpointConfig
        from secondbrain.executors.api import ApiExecutor
        answers = iter([json.dumps(large), deep])

        class Response:
            status_code, headers = 200, {}

            def __init__(self):
                self.text = next(answers)

            def json(self):
                return {'choices': [{'message': {'content': self.text}, 'finish_reason': 'stop'}], 'model': 'm'}
        executor = ApiExecutor(EndpointConfig(base_url='http://x', model='gpt-test', api_key_env='K'))
        for _ in range(2):
            task = runner.start_call(root, job_id, executor, role='producer', step='produce', prompt_text='p',
                                     packet={}, output_schema={}, chunk='c01')
            with pytest.raises(ExecutorFailure) as failure:
                runner.finish_call(root, task, executor._parse(Response(), task))
            assert failure.value.error.get('counted')
    info = jobs.load(root, job_id)['chunks']['c01']
    assert ingest._refusals(info, ingest.ANSWER_ROLES[0]) == 2
    [item] = program_errors(root)  # the nesting is too deep for the reader: a fault of the program, in both modes
    assert item['error'] == 'RecursionError' and item['times'] == 1


def test_mcp_job_submit_counts_an_answer_that_is_not_an_object(tmp_path):
    pytest.importorskip('mcp')
    import asyncio
    from mcp import Client
    from secondbrain.mcp_server import create_server
    root, job_id, _ = lantern_job(tmp_path)
    host = HostExecutor()
    out = ingest.advance(root, job_id, producer=host, reviewer=host)
    [h] = [x for x in out['handoffs'] if x['role'] == 'producer']

    async def submit():
        async with Client(create_server(root, writer=True)) as client:
            return await client.call_tool('job_submit', {'job': job_id, 'role': 'producer', 'run_id': h['run_id'],
                                                         'payload': ['not', 'an', 'object']})
    try:
        result = asyncio.run(submit())
        assert result.is_error
    except Exception as exc:  # noqa: BLE001 - a client that raises the tool's error
        assert 'JSON object' in str(exc)
    info = jobs.load(root, job_id)['chunks']['c01']
    assert ingest._refusals(info, ingest.ANSWER_ROLES[0]) == 1

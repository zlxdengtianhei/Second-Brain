"""An export step whose answers keep failing the program's checks is given up within the budget, never asked forever.

Fourth verdict, not blocking 4: the export counted failed steps in memory only, three per ``advance``; each run of
the job counted from 0 again, so a model that kept answering in the wrong shape stopped the export every time. Now a
refused answer is counted in the job state (per unit, step and repair round) against ``max_invalid_retries``; once
it is used up the unit is kept and marked as one whose repair rounds ran out: each paragraph its review was to judge
is marked "not reviewed", a unit its writer never wrote is left out and named, and both are on the owner's list.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from secondbrain import jobs
from secondbrain.config import ReviewPolicy
from secondbrain.executors.host import HostExecutor
from secondbrain.store import Conflict
from secondbrain.executors.base import ExecutorFailure, Result
from secondbrain.executors.fake import FakeExecutor
from secondbrain.export import pipeline
from tests.test_export import DOMAIN, FakeMathJax, reviewer, vault_root, writer  # noqa: F401 - the fixture

LIMIT = ReviewPolicy().max_invalid_retries + 1


def advance(root, job_id, produce, review):
    return pipeline.advance(root, job_id, producer=FakeExecutor('gpt-writer', responder=produce),
                            reviewer=FakeExecutor('claude-reviewer', responder=review), checker=FakeMathJax(), workers=1)


def test_a_review_that_always_fails_the_checks_is_asked_three_times_then_its_paragraphs_are_marked(vault_root):
    from secondbrain.service import Service
    job_id = pipeline.prepare(vault_root, domain=DOMAIN)['job_id']
    asked, inner = [], reviewer()

    def review(task):
        if task.chunk == 'u01':
            asked.append(task.chunk)
            return {'items': 5}
        return inner(task)
    result = advance(vault_root, job_id, writer(), review)
    assert result['status'] == 'complete' and len(asked) == LIMIT
    report = result['report']
    assert report['escalated_units'] == ['u01'] and report['coverage'] == 1.0  # the unit's text stays in
    flagged = [f for f in report['flagged'] if f['paragraph'].startswith('u01/')]
    assert flagged and all(f'failed validation {LIMIT} times, so it was not reviewed' in f['reason'] for f in flagged)
    items = [i for i in Service(vault_root).owner_items() if i.get('kind') == 'export_paragraph']
    assert {i['paragraph'] for i in items} == {f['paragraph'] for f in flagged}


def test_the_count_is_kept_when_the_export_is_run_again(vault_root):
    job_id = pipeline.prepare(vault_root, domain=DOMAIN)['job_id']
    asked, inner = [], reviewer()

    def review(task):
        if task.chunk == 'u01':
            asked.append(task.chunk)
            if len(asked) == 3:  # the third ask ends in a failure that is not an answer: the export stops
                return Result(status='failed', attestation='test',
                              error={'code': 'CONFIG_ERROR', 'message': 'no key', 'retryable': False})
            return {'items': 5}
        return inner(task)
    with pytest.raises(ExecutorFailure):
        advance(vault_root, job_id, writer(), review)
    result = advance(vault_root, job_id, writer(), review)
    assert result['status'] == 'complete' and len(asked) == LIMIT + 1  # two refused, a stop, one more refused
    assert result['report']['escalated_units'] == ['u01']


def test_a_unit_its_writer_never_writes_is_left_out_and_named_for_the_owner(vault_root):
    from secondbrain.service import Service
    job_id = pipeline.prepare(vault_root, domain=DOMAIN, mode='sequential')['job_id']
    asked, inner = [], writer()

    def produce(task):
        if task.chunk == 'u02':
            asked.append(task.chunk)
            return {'parts': 5}
        return inner(task)
    result = advance(vault_root, job_id, produce, reviewer())
    assert result['status'] == 'complete' and len(asked) == LIMIT
    report = result['report']
    assert 'u02' in report['escalated_units'] and report['units']['u02']['status'] == 'escalated'
    assert [f['reason'] for f in report['flagged'] if f['paragraph'] == 'u02'] == [
        f'its writer answer failed validation {LIMIT} times: the answer needs a "parts" list']
    assert report['coverage'] < 1.0 and report['units']['u03']['status'] == 'accepted'  # the next unit is written
    items = [i for i in Service(vault_root).owner_items() if i.get('kind') == 'export_paragraph']
    assert 'u02' in {i['paragraph'] for i in items}


def test_in_host_mode_a_review_refused_three_times_closes_its_run_and_the_unit_is_marked(vault_root):
    """A host sub-agent's refused answers are counted at the gate as an API run's are: after the budget its run is
    closed, and the unit is settled as one whose review never passed."""
    job_id = pipeline.prepare(vault_root, domain=DOMAIN)['job_id']
    host, produce, review, refused = HostExecutor(command=['sb']), writer(), reviewer(), []
    for _ in range(30):
        out = pipeline.advance(vault_root, job_id, producer=host, reviewer=host, checker=FakeMathJax())
        if out['status'] != 'awaiting_submission':
            break
        for handoff in out['handoffs']:
            run = jobs.load_run(vault_root, job_id, handoff['run_id'])
            text = open(handoff['packet_path'], encoding='utf-8').read()
            start = text.index('<task_packet>\n') + len('<task_packet>\n')
            task = SimpleNamespace(packet=json.loads(text[start:text.index('\n</task_packet>', start)]),
                                   chunk=run['chunk'], step=run['step'])
            model = 'gpt-writer' if run['role'] == 'producer' else 'claude-reviewer'
            if run['chunk'] == 'u01' and run['step'] == 'review':
                for _ in range(10):
                    try:
                        jobs.submit(vault_root, job_id, {'items': 5}, role='reviewer', run_id=run['run_id'],
                                    model_reported=model)
                    except Conflict as closed:
                        assert 'TERMINAL_RUN' in str(closed)
                        break
                    except ValueError:
                        refused.append(run['run_id'])
                continue
            answer = (produce if run['role'] == 'producer' else review)(task)
            jobs.submit(vault_root, job_id, answer, role=run['role'], run_id=run['run_id'], model_reported=model)
    assert out['status'] == 'complete' and len(refused) == LIMIT and len(set(refused)) == 1
    assert jobs.load_run(vault_root, job_id, refused[0])['error']['given_up'] is True
    assert out['report']['escalated_units'] == ['u01']
    assert any(f'failed validation {LIMIT} times, so it was not reviewed' in f['reason']
               for f in out['report']['flagged'])

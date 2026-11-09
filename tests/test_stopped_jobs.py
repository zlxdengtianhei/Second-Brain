"""A maintenance job that stops to wait for the owner is on the owner's list.

A classification, curation, relocation or domain-creation job whose answers kept failing the program's checks, or
whose repair rounds ran out, set ``escalation_required`` with a reason, but `sb status --owner-items` did not list it:
the owner did not see that it waits. Now the list has one item for it (``pending``, ``kind: maintenance_job``) naming
the job, its kind, the step that stopped and why. A later run of the same command takes its place; a classification's
revision job is shown through the job it revises. The vault is the invented one of tests/helpers.py.
"""
from __future__ import annotations

import json

from secondbrain import classification, cli, config, runner
from secondbrain.config import ReviewPolicy
from secondbrain.executors import FakeExecutor
from secondbrain.service import Service
from tests.helpers import EXISTING
from tests.test_classification import ready

LIMIT = ReviewPolicy().max_invalid_retries + 1


def stopped(root):
    return [i for i in Service(root).owner_items() if i.get('kind') == 'maintenance_job']


def producer(row, answer=None):
    return FakeExecutor('gpt-test', responder=answer or (lambda task: {'items': [
        {**row, 'id': c['id'], 'input_sha256': c['sha256']} for c in task.packet.get('input', task.packet)['cards']]}))


def run_until_it_stops(root, produce, review, job_id='classify'):
    for _ in range(LIMIT + 1):
        try:
            state = runner.run_job(root, job_id, producer=produce, reviewer=review, apply=False)
        except runner.ExecutorFailure:
            continue
        if state.get('escalation_required'):
            return state
    raise AssertionError('the job did not stop')


def test_a_review_that_keeps_failing_the_checks_puts_the_job_on_the_owner_list(tmp_path, capsys):
    config.set_review_policy(ReviewPolicy())
    root, _, row, _ = ready(tmp_path)
    assert stopped(root) == []
    state = run_until_it_stops(root, producer(row), FakeExecutor('claude-test', responder=lambda task: ['no']))
    [item] = stopped(root)
    assert item['owner_reason'] == 'pending' and item['job_id'] == 'classify'
    assert item['job_kind'] == 'classification' and item['step'] == 'review'
    assert item['reason'] == state['escalation_reason'] and f'failed validation {LIMIT} times' in item['reason']
    assert 'sb job show classify' in item['detail']
    assert Service(root).status()['owner_items']['pending'] == 1
    assert cli.main(['--vault', str(root), 'status', '--owner-items']) == 0
    assert [i['job_id'] for i in json.loads(capsys.readouterr().out) if i.get('kind') == 'maintenance_job'] == \
        ['classify']


def test_a_producer_that_keeps_failing_the_checks_names_the_produce_step(tmp_path):
    config.set_review_policy(ReviewPolicy())
    root, _, row, _ = ready(tmp_path)
    run_until_it_stops(root, producer(row, lambda task: {'items': 'not a list'}),
                       FakeExecutor('claude-test', responder=lambda task: {'items': []}))
    [item] = stopped(root)
    assert item['step'] == 'produce' and 'the producer answer failed validation' in item['reason']


def test_repair_rounds_that_run_out_name_the_repair_step_once_for_the_job_and_its_revision_job(tmp_path):
    config.set_review_policy(ReviewPolicy())
    root, _, row, _ = ready(tmp_path)

    def review(task):
        return {'items': [{'id': i['id'], 'decision': 'revise', 'reason': 'needs the passage'}
                          for i in task.packet['candidate']['items']]}
    runner.run_job(root, 'classify', producer=producer(row), reviewer=FakeExecutor('claude-test', responder=review),
                   apply=False)
    [item] = stopped(root)  # the revision jobs are not listed on their own
    assert item['job_id'] == 'classify' and item['step'] == 'repair'
    assert 'repair rounds exhausted' in item['reason'] and 'in its revision job repair-' in item['reason']


def test_a_later_run_of_the_same_command_takes_the_place_of_the_stopped_one(tmp_path):
    config.set_review_policy(ReviewPolicy())
    root, _, row, _ = ready(tmp_path)
    run_until_it_stops(root, producer(row), FakeExecutor('claude-test', responder=lambda task: ['no']))
    assert [i['job_id'] for i in stopped(root)] == ['classify']
    classification.prepare(root, ids=[EXISTING], job_id='classify-again')
    assert stopped(root) == []

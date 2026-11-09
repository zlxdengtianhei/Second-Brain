"""A maintenance job's review is checked, when it is taken, for covering the proposal it answers.

A review of a curation, relocation or domain-creation job was checked only for its schema when it was taken. One
that did not cover the proposal as the job's apply reads it (a domain written another way, an item left out, another
domain) was kept, and every later run of the job then stopped with the same error, never counted and never handed to
the owner. Now each maintenance kind checks a review against the proposal it answers when the review is taken
(``runner.KindSpec.review_check``): such a review is refused and counted, asked for again within the budget, and the
job then waits for the owner. The table is read from ``jobs.KINDS``, so a new maintenance kind fails here until it
has a row. The cards, maps and answers are invented.
"""
from __future__ import annotations

import copy

import pytest

from secondbrain import config, curation, domcreate, jobs, runner
from secondbrain import relocate as relocation
from secondbrain.executors import ExecutorFailure, FakeExecutor
from secondbrain.executors.host import HostExecutor
from secondbrain.schemas import AnswerError
from secondbrain.service import Service
from tests import helpers, test_curation as tc, test_relocate as tr
from tests.test_refusal_gate import classification_job

LIMIT = config.ReviewPolicy().max_invalid_retries + 1


def classification(tmp_path):
    root, produce = classification_job(tmp_path)
    return root, 'classify', produce, lambda candidate: {'items': []}


def curation_job(tmp_path):
    root = tc.build_vault(tmp_path / 'vault', catalog=tc.make_catalog({tc.DOM: tc.MOC}),
                          cards={tc.EXISTING: ('已有卡', tc.DOM, tc.PILLAR, tc.TOPIC)},
                          taxonomies={tc.MOC: tc.single_taxonomy()})
    curation.prepare(root, [tc.DOM], job_id='tax')
    tx = {'title': tc.MOC, 'narrative': '新叙事',
          'pillars': [{'name': tc.PILLAR, 'order': 1, 'why': '支柱说明', 'topics': [tc.TOPIC]},
                      {'name': '训练与对齐', 'order': 2, 'why': '新增支柱', 'topics': ['训练目标']}]}
    body = tc.proposal([tc.item(tc.DOM, tx, [tc.EXISTING])])
    wrong = {'items': [{'domain': tc.DOM.capitalize(), 'decision': 'accept', 'reason': '具体理由'}]}
    return root, 'tax', FakeExecutor('gpt-test', responder=lambda t: copy.deepcopy(body)), lambda candidate: wrong


def relocation_job(tmp_path):
    root = tr.build_vault(tmp_path / 'vault')
    tr.source_job(root)
    relocation.prepare(root, source_jobs=['src-cls'], job_id='relo')
    body = {'items': tr.relocation_rows(root)}
    return root, 'relo', FakeExecutor('gpt-test', responder=lambda t: copy.deepcopy(body)), \
        lambda candidate: {'items': []}  # its one item left out


def domcreate_job(tmp_path):
    root = helpers.build_vault(tmp_path / 'vault')
    domcreate.prepare(root, domain='robotics', moc='Robotics Map', question='How do robots move?', track='ml',
                      evidence_ids=[helpers.EXISTING], job_id='dc', reason='the cards on motion have no map of their own')
    body = {'domain': 'robotics', 'moc': 'Robotics Map', 'question': 'How do robots move?', 'track': 'ml',
            'taxonomy': {'title': 'Robotics Map', 'narrative': 'how robots move',
                         'pillars': [{'name': 'Motion', 'order': 1, 'why': 'what moves', 'topics': ['Gait']}]},
            'migrations': [{'id': helpers.EXISTING, 'pillar': 'Motion', 'topic': 'Gait'}],
            'relation_words': [], 'boundary_reason': 'motion only', 'difference_reason': 'not perception',
            'impact_reason': 'one card moves'}
    wrong = {'items': [{'domain': helpers.DOMAIN, 'decision': 'accept', 'reason': 'a specific reason'}]}
    return root, 'dc', FakeExecutor('gpt-test', responder=lambda t: copy.deepcopy(body)), lambda candidate: wrong


#: Each maintenance kind: a job whose producer gives a lawful proposal, and a review of the right schema that does
#: not cover it (classification: an item left out; curation: the domain written ``Attention`` for ``attention``;
#: relocation: its one item left out; domain creation: another domain).
JOBS = {'classification': classification, 'curation': curation_job, 'relocation': relocation_job,
        'domcreate': domcreate_job}


def test_every_maintenance_kind_has_a_row():
    asking = {kind for kind, asks in jobs.KINDS.items() if asks and kind not in ('ingest', 'export')}
    assert asking == set(JOBS)


@pytest.mark.parametrize('kind', sorted(JOBS))
def test_an_api_review_that_does_not_cover_the_proposal_is_counted_until_the_job_waits(tmp_path, kind):
    root, job_id, produce, wrong = JOBS[kind](tmp_path)
    reviewer = FakeExecutor('claude-test', responder=lambda task: wrong(task.packet['candidate']))
    failures = 0
    for _ in range(LIMIT + 2):
        try:
            out = runner.run_job(root, job_id, producer=produce, reviewer=reviewer)
        except ExecutorFailure as failure:  # the refused answer, counted, as for any review the checks refuse
            assert failure.error.get('counted'), failure.error
            failures += 1
            continue
        break
    assert failures == LIMIT
    assert out['escalation_required'] is True and out['escalation_step'] == 'review'
    assert not jobs.load(root, job_id).get('review')
    assert not [i for i in Service(root).owner_items() if i.get('kind') == 'program_error']
    assert runner.run_job(root, job_id, producer=produce, reviewer=reviewer)['escalation_required'] is True


@pytest.mark.parametrize('kind', sorted(JOBS))
def test_a_host_review_that_does_not_cover_the_proposal_is_refused_and_the_run_closes(tmp_path, kind):
    root, job_id, produce, wrong = JOBS[kind](tmp_path)
    out = runner.run_job(root, job_id, producer=produce, reviewer=HostExecutor())
    handoff = out['handoff']
    candidate = jobs.artifact(root, jobs.load(root, job_id), 'candidate')
    for _ in range(LIMIT):
        with pytest.raises(AnswerError) as refused:
            Service(root).submit(job_id, wrong(candidate), role='reviewer', run_id=handoff['run_id'])
        assert refused.value.counted
    assert jobs.load_run(root, job_id, handoff['run_id'])['status'] == 'failed'
    out = runner.run_job(root, job_id, producer=produce, reviewer=HostExecutor())  # does not raise
    assert out['escalation_required'] is True and not jobs.load(root, job_id).get('review')


def covering(kind, candidate):
    if kind in ('classification', 'relocation'):
        return {'items': [{'id': row['id'], 'decision': 'accept', 'reason': 'checked against the card'}
                          for row in candidate['items']]}
    domains = [row['domain'] for row in candidate['items']] if kind == 'curation' else [candidate['domain']]
    return {'items': [{'domain': d, 'decision': 'accept', 'reason': 'checked against the map'} for d in domains]}


@pytest.mark.parametrize('kind', sorted(JOBS))
def test_a_review_that_covers_the_proposal_is_taken(tmp_path, kind):
    root, job_id, produce, _ = JOBS[kind](tmp_path)
    handoff = runner.run_job(root, job_id, producer=produce, reviewer=HostExecutor())['handoff']
    candidate = jobs.artifact(root, jobs.load(root, job_id), 'candidate')
    done = Service(root).submit(job_id, covering(kind, candidate), role='reviewer', run_id=handoff['run_id'])
    assert done['status'] == 'review_submitted'

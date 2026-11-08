"""Jobs an earlier version froze (``ingest/3``, sb 7e42c7b): a partial one is retried to the end; an in-flight one
says how to get out, and that way works.

The fixtures are real jobs of that version (tests/fixtures/ingest3_*/README.md): its records judge repeats on the
old scale, so this version does not finish them, but it reads them, and a retry child is a job of this version.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from secondbrain import cli, jobs
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest
from secondbrain.store import state_root
from secondbrain.vault import read_snapshot
from tests.ingest_helpers import DIMMING_IR, FIXED_DIMMING, SOURCE_NAME, scripted

FIXTURES = Path(__file__).parent / 'fixtures'
JOB = 'ingest-cde2d135ac446f37'


def old_job(tmp_path, name):
    """The vault and the job of ``fixtures/<name>``, the job in the vault's state root."""
    root = tmp_path / 'vault'
    shutil.copytree(FIXTURES / name / 'vault', root)
    shutil.copytree(FIXTURES / name / 'job', state_root(root) / 'jobs' / JOB)
    assert jobs.read_input(root, JOB)['method_version'] == 'ingest/3'
    return root


def fixed_answers():
    answer = {'cards': [FIXED_DIMMING], 'new_topics': [], 'coverage': [{
        'source_quote': FIXED_DIMMING['source_quotes'][0], 'disposition': 'card', 'cards': ['marker-dimming'],
        'reason': 'fixed'}]}
    review = {'cards': [{'slug': 'marker-dimming', 'decision': 'accept', 'overlap': 'distinct', 'reason': 'ok'}],
              'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}
    return scripted([answer], [review], {'marker-dimming': DIMMING_IR})


def test_a_partial_job_of_an_earlier_version_is_retried_and_its_child_runs_to_the_end(tmp_path):
    root = old_job(tmp_path, 'ingest3_partial')
    assert jobs.load(root, JOB)['status'] == 'partial'
    child = ingest.retry(root, JOB)
    assert jobs.read_input(root, child['job_id'])['method_version'] == ingest.METHOD
    produce, respond, seen = fixed_answers()
    done = ingest.advance(root, child['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=respond))
    assert done['status'] == 'applied', done.get('pending')
    titles = {c.title for c in read_snapshot(root).cards.values()}
    assert {'Lantern queue', 'Lit marker memory cost', FIXED_DIMMING['title']} <= titles
    # the cards the parent wrote are shown to the child's reviewer, not written again
    assert {'kc_lantern-queue', 'kc_lit-marker-cost'} <= set(seen['reviewer'][0].packet['candidate_cards'])
    assert sum(t == 'Lantern queue' for t in (c.title for c in read_snapshot(root).cards.values())) == 1


def test_a_finished_job_of_an_earlier_version_names_no_next_step(tmp_path):
    root = old_job(tmp_path, 'ingest3_partial')
    rows = ingest.outcomes(root, JOB)
    assert rows and all(row['next'] is None for row in rows)
    assert [p['slug'] for row in rows for p in row['pending']] == ['marker-dimming']


def test_an_in_flight_job_of_an_earlier_version_says_how_to_get_out_and_that_way_works(tmp_path):
    root = old_job(tmp_path, 'ingest3_inflight')
    dead = FakeExecutor('x', responder=lambda task: pytest.fail('nothing may be asked'))
    with pytest.raises(ingest.IngestError) as refused:
        ingest.advance(root, JOB, producer=dead, reviewer=dead)
    message = str(refused.value)
    assert f'sb job reset {JOB} --reason' in message and f'sb ingest 00_Inbox/{SOURCE_NAME}' in message
    assert cli.main(['--vault', str(root), 'job', 'reset', JOB, '--reason', 'frozen by ingest/3']) == 0
    again = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    assert again['status'] == 'prepared' and jobs.read_input(root, again['job_id'])['method_version'] == ingest.METHOD


def test_a_retry_child_an_earlier_version_made_says_to_reset_it_and_retry_the_parent(tmp_path):
    root = old_job(tmp_path, 'ingest3_partial')
    child = ingest.retry(root, JOB)['job_id']
    path = state_root(root) / 'jobs' / child / 'input.json'  # as 7e42c7b's retry froze it
    import json
    frozen = json.loads(path.read_text(encoding='utf-8'))
    path.write_text(json.dumps({**frozen, 'method_version': 'ingest/3'}), encoding='utf-8')
    state = jobs.load(root, child)
    from secondbrain.store import digest, write_json
    write_json(root, jobs.location(child), {**state, 'input_sha256': digest(path.read_bytes())})
    dead = FakeExecutor('x', responder=lambda task: pytest.fail('nothing may be asked'))
    with pytest.raises(ingest.IngestError) as refused:
        ingest.advance(root, child, producer=dead, reviewer=dead)
    assert f'sb job reset {child} --reason' in str(refused.value) and f'sb ingest --retry {JOB}' in str(refused.value)
    assert cli.main(['--vault', str(root), 'job', 'reset', child, '--reason', 'frozen by ingest/3']) == 0
    new = ingest.retry(root, JOB)
    assert jobs.read_input(root, new['job_id'])['method_version'] == ingest.METHOD

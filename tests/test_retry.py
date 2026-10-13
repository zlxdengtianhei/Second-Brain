"""sb ingest --retry: a child job redoes only the pending items of a partial job."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from secondbrain import cli, config, inbox, jobs
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest
from secondbrain.store import recover
from secondbrain.vault import read_snapshot
from tests.ingest_helpers import (DIMMING_IR, FIXED_DIMMING, SOURCE_NAME, build_ingest_vault, first_candidate,
                                  first_review, repair_candidate, repair_review, scripted, tree)


def partial_job(root):
    produce, review, _ = scripted([first_candidate(), repair_candidate()], [first_review(), repair_review('revise')])
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, prepared['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                            reviewer=FakeExecutor('claude-test', responder=review))
    assert result['status'] == 'partial'
    return prepared['job_id']


def retry_answer():
    return {'cards': [FIXED_DIMMING], 'new_topics': [],
            'coverage': [{'source_quote': FIXED_DIMMING['source_quotes'][0], 'disposition': 'card',
                          'cards': ['marker-dimming'], 'reason': 'the pending card, fixed'}]}


def test_retry_redoes_only_the_pending_card(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    config.activate(replace(config.current(), ingest=replace(config.current().ingest, after_ingest='keep')))
    parent = partial_job(root)
    [item] = inbox.scan(root)
    assert item['status'] == 'partial' and item['pending'] == 1 and f'--retry {parent}' in item['note']
    before = tree(root)
    child = ingest.retry(root, parent)
    assert child['job_id'] == f'{parent}-retry1' and child['chunks'] == ['c01']
    assert ingest.retry(root, parent)['job_id'] == child['job_id']  # asking again returns the same child
    review = {'cards': [{'slug': 'marker-dimming', 'decision': 'accept', 'reason': 'matches the text now'}],
              'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'only the open card was missing'}}
    produce, respond, seen = scripted([retry_answer()], [review], {'marker-dimming': DIMMING_IR})
    done = ingest.advance(root, child['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=respond))
    assert done['status'] == 'applied' and [p['title'] for p in done['produced']] == ['Marker dimming']
    task = seen['producer'][0]
    assert 'This chunk was ingested before' in task.prompt
    assert [f['slug'] for f in task.packet['retry']['findings']] == ['marker-dimming']
    assert task.packet['retry']['findings'][0]['reason'] == 'still wrong'
    assert {c['title'] for c in task.packet['retry']['written_cards']} == {'Lantern queue', 'Lit marker memory cost'}
    assert {c['title'] for c in seen['reviewer'][0].packet['already_written_cards']} == {
        'Lantern queue', 'Lit marker memory cost'}
    cards = {c.title: c for c in read_snapshot(root).cards.values()}
    assert {'Lantern queue', 'Lit marker memory cost', 'Marker dimming'} <= set(cards)
    # a relation to a card the parent wrote may use its slug; it is stored as that card's id
    assert ('depends_on', cards['Lit marker memory cost'].id) in {
        (r['type'], r['target']) for r in cards['Marker dimming'].fm['relations']}
    [item] = inbox.scan(root)
    assert item['status'] == 'done'
    assert ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)['status'] == 'unchanged'
    # the child has its own transaction: rolling it back restores the partial state exactly
    assert recover(root, child['job_id'], 'rollback')['status'] == 'rolled_back'
    assert tree(root) == before


def test_only_partial_jobs_can_be_retried(tmp_path, capsys):
    root = build_ingest_vault(tmp_path / 'vault')
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    with pytest.raises(ingest.IngestError, match='Only a partial ingest job'):
        ingest.retry(root, prepared['job_id'])
    assert cli.main(['--vault', str(root), 'ingest', '--retry', prepared['job_id']]) == 2
    assert 'Only a partial' in capsys.readouterr().err


def test_retry_through_the_cli_in_host_mode(tmp_path, capsys):
    root = build_ingest_vault(tmp_path / 'vault')
    parent = partial_job(root)
    assert cli.main(['--vault', str(root), 'ingest', '--retry', parent, '--executor', 'host']) == 0
    [state] = json.loads(capsys.readouterr().out)
    assert state['job_id'] == f'{parent}-retry1' and state['handoffs'][0]['chunk'] == 'c01'
    assert jobs.load(root, parent)['retried_by'] == state['job_id']

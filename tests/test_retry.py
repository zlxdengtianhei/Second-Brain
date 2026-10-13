"""sb ingest --retry: a child job redoes only the pending items of a partial job."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from secondbrain import cli, config, inbox, jobs
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest
from secondbrain.store import Conflict, recover, state_root
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


def rollback(root, job_id):
    """`sb recover <job> --rollback`, which also marks the job rolled back."""
    assert cli.main(['--vault', str(root), 'recover', job_id, '--rollback']) == 0
    assert jobs.load(root, job_id)['status'] == 'rolled_back'


def apply_retry(root, parent):
    """Retry ``parent`` and write the fixed pending card; returns the child's final state."""
    child = ingest.retry(root, parent)
    review = {'cards': [{'slug': 'marker-dimming', 'decision': 'accept', 'reason': 'matches the text now'}],
              'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'only the open card was missing'}}
    produce, respond, _ = scripted([retry_answer()], [review], {'marker-dimming': DIMMING_IR})
    done = ingest.advance(root, child['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=respond))
    assert done['status'] == 'applied', done
    assert 'Marker dimming' in {c.title for c in read_snapshot(root).cards.values()}
    return done


def archived(root, job_id):
    """Names of the set-aside copies of ``job_id`` (``<id>.reset-<UTC>``)."""
    return sorted(p.name for p in (state_root(root) / 'jobs').iterdir() if p.name.startswith(f'{job_id}.reset-'))


def test_a_rolled_back_retry_child_does_not_block_a_new_retry(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    parent = partial_job(root)
    before = tree(root)
    first = apply_retry(root, parent)
    rollback(root, first['job_id'])
    assert tree(root) == before
    second = apply_retry(root, parent)  # a new child under the same id; the rolled-back one is set aside
    assert second['job_id'] == first['job_id'] == f'{parent}-retry1'
    assert second['created_at'] > first['created_at'] and len(archived(root, second['job_id'])) == 1
    assert jobs.load(root, parent)['retried_by'] == second['job_id']


def test_a_reset_retry_child_does_not_block_a_new_retry(tmp_path, capsys):
    root = build_ingest_vault(tmp_path / 'vault')
    parent = partial_job(root)
    first = apply_retry(root, parent)
    rollback(root, first['job_id'])
    assert cli.main(['--vault', str(root), 'job', 'reset', first['job_id'], '--reason', 'try again']) == 0
    capsys.readouterr()
    second = apply_retry(root, parent)
    assert second['job_id'] == first['job_id'] and second['created_at'] > first['created_at']


def test_after_parent_and_child_are_rolled_back_the_source_can_be_ingested_and_retried_again(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')  # default after_ingest = move
    before = tree(root)
    parent = partial_job(root)
    first = apply_retry(root, parent)
    rollback(root, first['job_id'])
    rollback(root, parent)
    assert tree(root) == before and inbox.scan(root)[0]['status'] == 'new'
    again = partial_job(root)  # the rolled-back parent is set aside; same id, new job
    assert again == parent and len(archived(root, parent)) == 1
    second = apply_retry(root, parent)  # the old child (same id, same input) no longer answers for it
    assert second['job_id'] == first['job_id'] and second['created_at'] > first['created_at']
    assert len(archived(root, first['job_id'])) == 1


def test_an_unwritten_child_of_an_earlier_run_of_the_parent_is_set_aside(tmp_path):
    # adapted from the verifier's test (V1, scratch/v1c/scripts/test_v1c_stale.py)
    root = build_ingest_vault(tmp_path / 'vault')
    before = tree(root)
    parent = partial_job(root)
    stale = ingest.retry(root, parent)  # made, never advanced
    rollback(root, parent)  # allowed: the child wrote nothing
    assert tree(root) == before
    assert partial_job(root) == parent  # a new run of the parent under the same id
    done = apply_retry(root, parent)
    assert done['job_id'] == stale['job_id'] and done['created_at'] > stale['created_at']
    assert len(archived(root, stale['job_id'])) == 1


def test_a_written_child_that_looks_stale_is_refused_not_replaced(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    parent = partial_job(root)
    child = apply_retry(root, parent)
    before = tree(root)
    path = state_root(root) / jobs.location(parent)
    state = json.loads(path.read_text(encoding='utf-8'))
    state['created_at'] = '2099-01-01T00:00:00+00:00'  # as after a clock jump
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with pytest.raises(Conflict, match='roll it back first'):
        ingest.retry(root, parent)
    assert jobs.load(root, child['job_id'])['status'] == 'applied'
    assert archived(root, child['job_id']) == [] and tree(root) == before


def test_retrying_a_parent_whose_child_is_partial_points_to_the_child(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    parent = partial_job(root)
    child = ingest.retry(root, parent)
    still = {'cards': [{'slug': 'marker-dimming', 'decision': 'revise', 'reason': 'still wrong'}],
             'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'only the open card was missing'}}
    produce, respond, _ = scripted([retry_answer(), retry_answer()], [still, still])
    done = ingest.advance(root, child['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=respond))
    assert done['status'] == 'partial'
    again = ingest.retry(root, parent)
    assert again['job_id'] == child['job_id'] and again['resumed']
    assert f'sb ingest --retry {child["job_id"]}' in again['hint']


def test_a_retry_child_made_before_an_interruption_is_reused(tmp_path, monkeypatch):
    root = build_ingest_vault(tmp_path / 'vault')
    parent = partial_job(root)
    real = jobs._internal_update

    def interrupted(root_, job_id, **fields):
        if 'retried_by' in fields:
            raise KeyboardInterrupt
        return real(root_, job_id, **fields)
    monkeypatch.setattr(jobs, '_internal_update', interrupted)
    with pytest.raises(KeyboardInterrupt):
        ingest.retry(root, parent)
    monkeypatch.setattr(jobs, '_internal_update', real)
    made = jobs.load(root, f'{parent}-retry1')
    again = ingest.retry(root, parent)
    assert again['resumed'] and again['created_at'] == made['created_at'] and archived(root, made['job_id']) == []
    assert jobs.load(root, parent)['retried_by'] == made['job_id']


def test_a_moved_partial_source_stays_in_the_inbox_list(tmp_path, capsys):
    root = build_ingest_vault(tmp_path / 'vault')  # default after_ingest = move
    parent = partial_job(root)
    assert not (root / '00_Inbox' / SOURCE_NAME).exists()
    [item] = inbox.scan(root)
    assert item['status'] == 'partial' and item['in_inbox'] is False and item['job_id'] == parent
    assert item['path'].startswith('40_Sources/') and (root / item['path']).is_file() and item['pending'] == 1
    assert f'--retry {parent}' in item['note']
    assert cli.main(['--vault', str(root), 'status']) == 0
    assert json.loads(capsys.readouterr().out)['inbox'] == {'partial': 1}
    child = apply_retry(root, parent)
    assert inbox.scan(root) == []  # done now
    # the child's registry row names where the source is, not the inbox path it left
    rows = [r for r in inbox.registry_rows(root) if r['job_id'] == child['job_id']]
    assert [r['path'] for r in rows] == [item['path']]


def test_rolling_back_a_parent_before_its_retry_child_says_which_to_roll_back_first(tmp_path, capsys):
    root = build_ingest_vault(tmp_path / 'vault')
    parent = partial_job(root)
    child = apply_retry(root, parent)
    capsys.readouterr()
    assert cli.main(['--vault', str(root), 'recover', parent, '--rollback']) == 3
    error = json.loads(capsys.readouterr().err)['error']
    assert f'sb recover {child["job_id"]} --rollback' in error and 'retry child first' in error
    assert jobs.load(root, parent)['status'] == 'partial'
    rollback(root, child['job_id'])
    rollback(root, parent)


def test_a_retry_packet_offers_the_domain_the_reviewer_named(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    config.activate(replace(config.current(), ingest=replace(config.current().ingest, candidate_domains=1)))
    review = repair_review('revise')
    review['cards'][0].update(suggested_domain='transformer', reason='belongs with the decoder cards')
    produce, respond, _ = scripted([first_candidate(), repair_candidate()], [first_review(), review])
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    routed = jobs.read_input(root, prepared['job_id'])['chunks'][0]['candidate_domains']
    assert 'transformer' not in routed
    assert ingest.advance(root, prepared['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=respond))['status'] == 'partial'
    child = ingest.retry(root, prepared['job_id'])
    [chunk] = jobs.read_input(root, child['job_id'])['chunks']
    assert 'transformer' in chunk['candidate_domains'] and 'transformer' in chunk['routing']['named_by_review']
    packet = ingest.producer_packet(root, jobs.load(root, child['job_id']),
                                    jobs.read_input(root, child['job_id']), 'c01')
    assert 'transformer' in packet['candidate_taxonomies']

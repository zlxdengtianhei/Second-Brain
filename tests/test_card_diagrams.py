"""The ingest chain's diagram step: batches, one rewrite, skips, and the off switch."""
from __future__ import annotations

import json
from dataclasses import replace

from secondbrain import config, jobs
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import card_diagrams, ingest
from secondbrain.vault import read_snapshot
from tests.ingest_helpers import (DIMMING_IR, FIXED_DIMMING, SOURCE_NAME, build_ingest_vault, diagram_answer,
                                  first_candidate, first_review, repair_candidate, repair_review, scripted)

CHAIN_IR = {**DIMMING_IR, 'nodes': DIMMING_IR['nodes'][:3],
            'edges': [{'from': 'c', 'to': 'q'}, {'from': 'q', 'to': 'd'}]}


def card(i, kind='mechanism'):
    return {'ref': f'card-{i}', 'title': f'Card {i}', 'kind': kind, 'domain': 'd',
            'essence': f'Essence {i}.', 'propositions': [f'Step one of {i}.', f'Step two of {i}.'],
            'related_titles': []}


def test_cards_are_batched_by_ten_and_pregated_kinds_skip_the_model():
    cards = [card(i) for i in range(12)] + [card(99, 'contrast')]
    first = card_diagrams.evaluate(cards, lambda slot: (False, None))
    assert [p[0] for p in first['pending']] == ['dg01', 'dg02']
    assert len(first['pending'][0][3]['cards']) == 10 and len(first['pending'][1][3]['cards']) == 2
    assert first['diagrams'] == {'card-99': None}
    assert first['report'][0]['reason'].startswith('contrast card')
    skip = {'decision': 'skip', 'reason': 'text is enough'}

    def answered(slot):
        refs = [c['ref'] for c in cards[:10]] if slot == 'dg01' else ['card-10', 'card-11']
        return True, {'results': [{'ref': r, 'ir': skip} for r in refs]}
    done = card_diagrams.evaluate(cards, answered)
    assert done['pending'] == [] and set(done['diagrams']) == {c['ref'] for c in cards}
    assert all(v is None for v in done['diagrams'].values())


def test_invalid_answers_get_one_rewrite_then_skip():
    cards = [card(1)]
    state = {'dg01': (True, None)}  # first answer was not a JSON object of the right shape
    step = card_diagrams.evaluate(cards, lambda slot: state.get(slot, (False, None)))
    [(slot, kind, prompt, request)] = step['pending']
    assert (slot, kind, request['attempt']) == ('dg01.r1', 'repair', 1)
    assert request['cards'][0]['errors'] and request['cards'][0]['previous'] is None
    state['dg01.r1'] = (True, {'results': []})
    final = card_diagrams.evaluate(cards, lambda slot: state.get(slot, (False, None)))
    assert final['pending'] == [] and final['diagrams'] == {'card-1': None}
    assert final['report'][0]['status'] == 'skip' and final['report'][0]['attempt'] == 1


def test_a_rejected_ir_is_rewritten_once_in_the_ingest_chain(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    produce, review, seen = scripted([first_candidate(), repair_candidate()], [first_review(), repair_review()])
    answers = [{'marker-dimming': CHAIN_IR}, {'marker-dimming': DIMMING_IR}]
    calls = []

    def producer_fn(task):
        if card_diagrams.is_slot(task.chunk):
            calls.append(task)
            return diagram_answer(task.packet, answers.pop(0))
        return produce(task)
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, prepared['job_id'], producer=FakeExecutor('gpt-test', responder=producer_fn),
                            reviewer=FakeExecutor('claude-test', responder=review))
    assert result['status'] == 'applied'
    assert [t.chunk for t in calls] == ['dg01', 'dg01.r1'] and [t.step for t in calls] == ['produce', 'repair']
    rewrite = calls[1].packet
    assert rewrite['attempt'] == 1 and [c['ref'] for c in rewrite['cards']] == ['marker-dimming']
    assert any(e.startswith('V5') for e in rewrite['cards'][0]['errors'])
    drawn = {d['ref']: d for d in result['diagrams']}
    assert drawn['marker-dimming']['status'] == 'draw' and drawn['marker-dimming']['attempt'] == 1
    dimming = next(c for c in read_snapshot(root).cards.values() if c.title == FIXED_DIMMING['title'])
    assert '```mermaid\n%% diagram v1' in dimming.body


def test_diagrams_can_be_switched_off(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    config.activate(replace(config.current(), card_diagrams=False))
    produce, review, seen = scripted([first_candidate(), repair_candidate()], [first_review(), repair_review()],
                                     {'marker-dimming': DIMMING_IR})
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    assert jobs.read_input(root, prepared['job_id'])['settings']['diagrams'] is False
    result = ingest.advance(root, prepared['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                            reviewer=FakeExecutor('claude-test', responder=review))
    assert result['status'] == 'applied' and result['diagrams'] == [] and seen['diagram'] == []
    assert not any('```mermaid' in c.body for c in read_snapshot(root).cards.values())


def test_the_diagram_answer_shape_is_checked_at_submit(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    calls = []
    produce, review, _ = scripted([first_candidate(), repair_candidate()], [first_review(), repair_review()])

    def producer_fn(task):
        if card_diagrams.is_slot(task.chunk):
            calls.append(task.chunk)
            return {'diagrams': 'not the expected shape'}
        return produce(task)
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, prepared['job_id'], producer=FakeExecutor('gpt-test', responder=producer_fn),
                            reviewer=FakeExecutor('claude-test', responder=review))
    assert result['status'] == 'applied' and calls == ['dg01', 'dg01.r1']
    assert {d['status'] for d in result['diagrams']} == {'skip'}
    failed = [r for r in jobs.list_runs(root, prepared['job_id']) if r['chunk'] in calls]
    assert [r['status'] for r in failed] == ['failed', 'failed']
    assert 'results' in json.dumps(failed[0]['error'])

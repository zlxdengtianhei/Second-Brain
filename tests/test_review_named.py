"""The reviewer packet holds every existing card the producer named (invented source and cards)."""
from __future__ import annotations

import copy
import json

import pytest

from secondbrain import config, jobs
from secondbrain import layout as layouts
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest
from secondbrain.vault import read_snapshot
from tests.helpers import DOMAIN, EXISTING, PILLAR, TOPIC
from tests.ingest_helpers import (SOURCE_NAME, build_ingest_vault, first_candidate, first_review, repair_candidate,
                                  repair_review, scripted)


def write_card(root, cid, title, essence, propositions, topic=TOPIC):
    lay = layouts.DEFAULT
    rel = lay.card_path(DOMAIN, f'{title}.md')
    body = (f'## Essence\n{essence}\n\n## Propositions\n' + ''.join(f'- P{i}. {p}\n' for i, p in
                                                                   enumerate(propositions, 1))
            + '\n## Review cards\nWhat is it?::' + essence + '\n')
    (root / rel).write_text(f'---\nid: {cid}\ntitle: {title}\ndomain: {DOMAIN}\npillar: {PILLAR}\n'
                            f'topic: {topic}\n---\n{body}', encoding='utf-8')
    registry = json.loads((root / lay.registry).read_text(encoding='utf-8'))
    registry['notes'].append({'id': cid, 'path': rel, 'title': title, 'domain': DOMAIN})
    (root / lay.registry).write_text(json.dumps(registry, ensure_ascii=False), encoding='utf-8')


def lantern_vault(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    # far from every card's words: no search finds it, only the producer's naming does
    write_card(root, 'demo_far', 'Pebble weights', 'Pebbles are weighed in grams.', ['A pebble weighs grams.'])
    write_card(root, 'demo_lantern', 'Lantern queues in brief', 'A lantern queue keeps arrival order.',
               ['Items keep arrival order.', 'Readers skip ahead by lit markers.'])
    return root


def naming(card_slug='lantern-queue', target='demo_far'):
    """The first candidate, with one card linking ``target`` and a coverage row saying ``target`` holds a passage."""
    candidate = copy.deepcopy(first_candidate())  # the fixture cards are shared
    for card in candidate['cards']:
        if card['slug'] == card_slug:
            card['relations'] = [*card['relations'], {'type': 'similar_to', 'target': target, 'note': 'named'}]
    candidate['coverage'].append({'source_quote': 'Thanks for reading', 'disposition': 'same',
                                  'with': [target], 'reason': 'PRODUCER-REASON-9'})
    return candidate


def review_packet_for(root, candidate, **ingest_settings):
    settings = config.parse({'ingest': ingest_settings}) if ingest_settings else None
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME, settings=settings)
    produce, respond, seen = scripted([candidate, repair_candidate()], [first_review(), repair_review()])
    ingest.advance(root, prepared['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                   reviewer=FakeExecutor('claude-test', responder=respond))
    return seen['reviewer'][0].packet, prepared['job_id']


def test_every_named_existing_card_is_shown_whole_and_outside_the_budget(tmp_path):
    root = lantern_vault(tmp_path)
    packet, _ = review_packet_for(root, naming(), candidates_chars=1000)
    rows = {e['id']: e for e in packet['candidates']['lantern-queue']}
    assert 'named' in rows['demo_far']['via'] and rows[EXISTING]['via'][0] == 'named'
    assert packet['candidate_cards']['demo_far']['propositions'] == ['A pebble weighs grams.']
    assert packet['candidates'][ingest.COVERAGE_KEY] == [{'id': 'demo_far', 'rank': 1, 'via': ['named']}]
    # the other cards name nothing: what they see the search found
    assert all('named' not in e['via'] for s in ('marker-dimming', 'queue-order') for e in packet['candidates'][s])


def test_without_review_named_the_packet_is_the_search_alone(tmp_path):  # H4 group A
    root = lantern_vault(tmp_path)
    packet, _ = review_packet_for(root, naming(), review_named=False)
    assert ingest.COVERAGE_KEY not in packet['candidates']
    assert all('named' not in e['via'] for rows in packet['candidates'].values() for e in rows)


def test_review_words_view_brief_keeps_named_cards_full(tmp_path):  # H4 group B'
    root = lantern_vault(tmp_path)
    packet, _ = review_packet_for(root, naming(), review_words_view='brief')
    assert 'propositions' in packet['candidate_cards']['demo_far']
    assert 'propositions' not in packet['candidate_cards']['demo_lantern']


def test_the_shelf_and_the_second_route_add_their_cards(tmp_path):  # H4 group C
    root = lantern_vault(tmp_path)
    write_card(root, 'demo_shelf', 'Unrelated title on the shelf', 'Nothing in common.', ['Nothing.'], topic=TOPIC)
    packet, _ = review_packet_for(root, first_candidate(), candidates_shelf=True, candidates_hop=1)
    rows = {e['id']: e for e in packet['candidates']['lantern-queue']}  # filed under the shelf's topic
    assert 'shelf' in rows['demo_shelf']['via']
    hop = {e['id'] for rows_ in packet['candidates'].values() for e in rows_ if 'hop' in e['via']}
    snapshot = read_snapshot(root)
    first = {e['id'] for e in packet['candidates']['lantern-queue'] if 'words' in e['via']}
    assert hop <= {r['target'] for cid in snapshot.cards for r in snapshot.cards[cid].fm.get('relations') or []}
    assert first


def test_named_cards_past_twice_the_budget_split_the_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, 'MIN_SPLIT_CHARS', 100)
    root = lantern_vault(tmp_path)
    write_card(root, 'demo_huge', 'A very long card', 'Long.', [f'Point {i} ' + 'x' * 300 for i in range(8)])
    candidate = naming(target='demo_huge')
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME, settings=config.parse(
        {'ingest': {'candidates_chars': 1000}}))
    calls = []

    def produce(task):
        calls.append(task.chunk)
        if task.chunk == 'c01':
            return candidate
        raise AssertionError('stop after the split')
    with pytest.raises(AssertionError, match='stop after the split'):
        ingest.advance(root, prepared['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                       reviewer=FakeExecutor('claude-test', responder=lambda task: first_review()))
    state = jobs.load(root, prepared['job_id'])
    assert state['chunks']['c01']['split'] and 'named cards' in state['chunks']['c01']['split_error']
    assert calls[:2] == ['c01', 'c01-1']  # the halves are produced again
    parts = [c['chunk_id'] for c in ingest.effective_chunks(jobs.read_input(root, prepared['job_id']), state)]
    assert parts[0] == 'c01-1' and len(parts) >= 2 and all(p.startswith('c01-') for p in parts)

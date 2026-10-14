"""The source check (drill 3): cards and new topics that two chunks of one source repeat are settled before writing."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from secondbrain import cli, jobs
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest, source_check
from secondbrain.vault import read_snapshot
from tests.helpers import PILLAR, TOPIC
from tests.ingest_helpers import (CARDS, SOURCE_NAME, TOPIC_PROPOSAL, build_ingest_vault, diagram_answer,
                                  is_source_check_task, keep_all, packet_in, with_diagrams)
from tests.test_ingest import settings

ORDER_QUOTE = 'a lantern queue keeps every item in arrival order'
FILLER = ' '.join(f'Filler sentence {i} says nothing about queues.' for i in range(14))
SOURCE = ('# Lantern queues\n\n' + CARDS['lantern-queue']['source_quotes'][0] + '\n\n## Notes\n\n' + FILLER + '\n\n'
          + FILLER + '\n\n## Lit markers\n\nA writer lights a marker every eight items. Markers cost one extra '
          'word of memory each, so a queue of 800 items carries 100 markers. Note again that '
          + ORDER_QUOTE + '.\n')
#: what the second chunk says again, as its own card
ARRIVAL = {**CARDS['queue-order'], 'slug': 'arrival-order', 'title': 'Lantern queues keep arrival order',
           'source_quotes': [ORDER_QUOTE],
           'relations': [{'type': 'part_of', 'target': 'lantern-queue', 'note': 'a property of the queue'}]}
ORDERING = {'domain': TOPIC_PROPOSAL['domain'], 'pillar': PILLAR, 'name': 'Ordering rules',
            'boundary': 'Which order a structure keeps.', 'reason': 'no topic covers order'}
ORDERING_AGAIN = {**ORDERING, 'name': 'Arrival sequencing', 'reason': 'the same, under another name'}


def produce(topics=False):
    """The producer of each chunk: the first chunk's cards, the second chunk's cards, nothing for filler."""
    def respond(task):
        text = task.packet['chunk']['text']
        cards, new_topics = [], []
        if CARDS['lantern-queue']['source_quotes'][0] in text:
            order = {**CARDS['queue-order'], 'relations': []}
            if topics:
                order['topic'] = ORDERING['name']
                new_topics.append(ORDERING)
            cards += [{**CARDS['lantern-queue'], 'relations': []}, order]
        if ORDER_QUOTE in text:
            cost = {**CARDS['lit-marker-cost'],
                    'relations': [{'type': 'depends_on', 'target': 'arrival-order', 'note': 'markers follow the order'}]}
            arrival = dict(ARRIVAL)
            if topics:  # the second chunk names the first chunk's new subject again; the cost card uses Markers
                arrival['topic'] = ORDERING_AGAIN['name']
                new_topics.append(ORDERING_AGAIN)
                cost['topic'] = TOPIC
            else:
                new_topics.append(TOPIC_PROPOSAL)
            cards += [cost, arrival]
        rows = [{'source_quote': c['source_quotes'][0], 'disposition': 'card', 'cards': [c['slug']], 'reason': 'r'}
                for c in cards]
        rows.append({'source_quote': text.strip().splitlines()[-1].strip(), 'disposition': 'out_of_scope',
                     'reason': 'rest'})
        return {'cards': cards, 'new_topics': new_topics, 'coverage': rows}
    return respond


def review(task):
    cand = task.packet['candidate']
    return {'cards': [{'slug': c['slug'], 'decision': 'accept', 'reason': 'ok'} for c in cand['cards']],
            'new_topics': [{**{k: t[k] for k in ('domain', 'pillar', 'name')}, 'decision': 'accept', 'reason': 'ok'}
                           for t in cand['new_topics']],
            'coverage': {'decision': 'accept', 'reason': 'ok'}}


def run(tmp_path, check, *, topics=False):
    """Ingest the two-chunk source in API mode; ``check`` answers the source check."""
    root = build_ingest_vault(tmp_path / 'vault')
    settings(chunk_chars=1000)
    (root / '00_Inbox' / SOURCE_NAME).write_text(SOURCE, encoding='utf-8')
    seen = []

    def reviewer(task):
        if is_source_check_task(task):
            seen.append(task)
            return check(task.packet)
        return review(task)
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    assert len(jobs.read_input(root, job['job_id'])['chunks']) >= 2
    result = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=with_diagrams(produce(topics))),
                            reviewer=FakeExecutor('claude-test', responder=reviewer))
    return root, job['job_id'], result, seen


def test_a_card_two_chunks_both_write_is_written_once_and_links_follow_it(tmp_path):
    same = {'arrival-order': {'decision': 'same', 'with': 'queue-order', 'reason': 'both say items keep arrival order'}}
    root, job_id, result, seen = run(tmp_path, lambda packet: keep_all(packet, **same))
    assert result['status'] == 'applied'
    [task] = seen
    assert task.role == 'reviewer' and task.chunk == source_check.SLOT
    assert {c['slug'] for c in task.packet['cards']} == {'lantern-queue', 'queue-order', 'lit-marker-cost',
                                                          'arrival-order'}
    assert {c['chunk'] for c in task.packet['cards']} >= {'c01'} and len({c['chunk'] for c in task.packet['cards']}) == 2
    ids = {p['slug']: p['id'] for p in result['produced']}
    assert set(ids) == {'lantern-queue', 'queue-order', 'lit-marker-cost'}
    assert result['source_check']['merged'] == [{'slug': 'arrival-order', 'title': 'Lantern queues keep arrival order',
                                                 'into': ids['queue-order'],
                                                 'reason': 'both say items keep arrival order'}]
    assert result['skipped_relations'] == []
    cards = read_snapshot(root).cards
    # the link to the merged card goes to the card it repeats, and the merged card's own link moved there
    assert {'type': 'depends_on', 'target': ids['queue-order'], 'note': 'markers follow the order'} in \
        cards[ids['lit-marker-cost']].fm['relations']
    assert {'type': 'part_of', 'target': ids['lantern-queue'], 'note': 'a property of the queue'} in \
        cards[ids['queue-order']].fm['relations']
    assert {'type': 'part_of', 'target': ids['queue-order']} in cards[ids['lantern-queue']].fm['relations']
    assert result['source_check']['moved_relations'] == [{'from': ids['queue-order'], 'type': 'part_of',
                                                          'target': ids['lantern-queue'], 'was_on': 'arrival-order'}]
    assert not any(c.title == 'Lantern queues keep arrival order' for c in cards.values())


def test_an_overlapping_card_is_written_as_needs_review(tmp_path):
    overlap = {'arrival-order': {'decision': 'overlap', 'with': 'queue-order', 'reason': 'adds the second mention'}}
    root, _, result, _ = run(tmp_path, lambda packet: keep_all(packet, **overlap))
    ids = {p['slug']: p['id'] for p in result['produced']}
    assert len(ids) == 4
    assert result['needs_review'] == [{'id': ids['arrival-order'], 'title': 'Lantern queues keep arrival order',
                                       'overlap_with': ids['queue-order'], 'reason': 'adds the second mention'}]
    assert read_snapshot(root).cards[ids['arrival-order']].fm['status'] == 'needs-review'
    assert result['source_check']['overlap'][0]['with'] == ids['queue-order']


def test_two_new_topics_for_one_subject_become_one(tmp_path):
    def check(packet):
        by_name = {t['name']: t['id'] for t in packet['new_topics']}
        return keep_all(packet, **{by_name['Arrival sequencing']: {
            'decision': 'same', 'with': by_name['Ordering rules'], 'reason': 'both are about kept order'}})
    root, _, result, _ = run(tmp_path, check, topics=True)
    assert result['status'] == 'applied'
    assert [t['name'] for t in result['new_topics']] == ['Ordering rules']
    [moved] = result['source_check']['refiled_topics']
    assert moved['from']['name'] == 'Arrival sequencing' and moved['into']['name'] == 'Ordering rules'
    assert moved['cards'] == 1
    arrival = next(c for c in read_snapshot(root).cards.values() if c.title == 'Lantern queues keep arrival order')
    assert arrival.fm['topic'] == 'Ordering rules'


def test_an_answer_that_keeps_failing_validation_skips_the_check(tmp_path):
    root, job_id, result, seen = run(tmp_path, lambda packet: {'cards': [], 'new_topics': []})
    assert result['status'] == 'applied' and len(result['produced']) == 4
    assert result['source_check']['status'] == 'skipped' and 'failed validation' in result['source_check']['reason']
    assert len(seen) == 2  # max_repair_rounds = 1: the first answer and one more
    assert 'missing' in jobs.load(root, job_id)['chunks'][source_check.SLOT]['last_validation_error']


def test_one_chunk_with_cards_needs_no_check(tmp_path):
    from tests.ingest_helpers import first_candidate, first_review, repair_candidate, repair_review, scripted
    root = build_ingest_vault(tmp_path / 'vault')
    producer, reviewer, seen = scripted([first_candidate(), repair_candidate()], [first_review(), repair_review()])
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=producer),
                            reviewer=FakeExecutor('claude-test', responder=reviewer))
    assert result['status'] == 'applied' and 'source_check' not in result and seen['source_check'] == []


def test_host_mode_hands_the_check_to_a_reviewer(tmp_path):
    from secondbrain.executors.host import HostExecutor
    root = build_ingest_vault(tmp_path / 'vault')
    settings(chunk_chars=1000)
    (root / '00_Inbox' / SOURCE_NAME).write_text(SOURCE, encoding='utf-8')
    job_id = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)['job_id']
    host, producer = HostExecutor(), produce()

    def submit(handoff, payload):
        path = tmp_path / f"{handoff['run_id']}.json"
        path.write_text(json.dumps(payload), encoding='utf-8')
        model = 'gpt-test' if handoff['role'] == 'producer' else 'claude-test'
        return cli.main(['--vault', str(root), 'job', 'submit', job_id, '--role', handoff['role'], '--run-id',
                         handoff['run_id'], '--file', str(path), '--model', model])
    waiting = ingest.advance(root, job_id, producer=host, reviewer=host)
    first_round = waiting['handoffs']
    assert len(first_round) >= 2 and all(h['step'] == 'produce' for h in first_round)
    for handoff in first_round:  # all at once, as a coordinator runs them
        packet = packet_in(open(handoff['packet_path'], encoding='utf-8').read())
        assert packet['other_chunks_cards'] == []  # written at once: no chunk sees another's cards
        assert submit(handoff, producer(SimpleNamespace(packet=packet))) == 0
    for handoff in ingest.advance(root, job_id, producer=host, reviewer=host)['handoffs']:
        assert handoff['step'] == 'review'
        packet = packet_in(open(handoff['packet_path'], encoding='utf-8').read())
        assert submit(handoff, review(SimpleNamespace(packet=packet))) == 0
    [check] = ingest.advance(root, job_id, producer=host, reviewer=host)['handoffs']
    assert check['role'] == 'reviewer' and check['chunk'] == source_check.SLOT and check['step'] == 'source_check'
    assert ingest.packet_for(root, job_id, role='reviewer', chunk=None, executor=host)['run_id'] == check['run_id']
    text = open(check['packet_path'], encoding='utf-8').read()
    assert 'repeat each other across the chunks' in text
    packet = packet_in(text)
    assert submit(check, {'cards': [], 'new_topics': []}) != 0  # every card needs a decision
    assert submit(check, keep_all(packet, **{'arrival-order': {'decision': 'same', 'with': 'queue-order',
                                                               'reason': 'same point'}})) == 0
    [diagram] = ingest.advance(root, job_id, producer=host, reviewer=host)['handoffs']
    request = packet_in(open(diagram['packet_path'], encoding='utf-8').read())
    assert 'arrival-order' not in [c['ref'] for c in request['cards']]  # no diagram for a card that is not written
    assert submit(diagram, diagram_answer(request)) == 0
    done = ingest.advance(root, job_id, producer=host, reviewer=host)
    assert done['status'] == 'applied' and len(done['produced']) == 3
    assert [m['slug'] for m in done['source_check']['merged']] == ['arrival-order']


# --- the answer's form ------------------------------------------------------------------------------
FROZEN = {'cards': [{'slug': s} for s in ('a', 'b', 'c')], 'new_topics': [{'id': 'T1'}, {'id': 'T2'}]}


def answer(cards=None, topics=None):
    rows = {s: {'slug': s, 'decision': 'keep'} for s in ('a', 'b', 'c')}
    rows.update(cards or {})
    trows = {t: {'id': t, 'decision': 'keep'} for t in ('T1', 'T2')}
    trows.update(topics or {})
    return {'cards': list(rows.values()), 'new_topics': list(trows.values())}


@pytest.mark.parametrize('cards, topics, message', [
    ({'a': {'slug': 'a', 'decision': 'same', 'with': 'b', 'reason': 'r'},
      'b': {'slug': 'b', 'decision': 'same', 'with': 'c', 'reason': 'r'}}, None, 'name c instead'),
    ({'a': {'slug': 'a', 'decision': 'keep', 'with': 'b'}}, None, '"keep" takes no "with"'),
    ({'a': {'slug': 'a', 'decision': 'same', 'with': 'a', 'reason': 'r'}}, None, 'another slug'),
    ({'a': {'slug': 'a', 'decision': 'same', 'with': 'b'}}, None, 'what the two share'),
    ({'z': {'slug': 'z', 'decision': 'keep'}}, None, "'z' is not in the packet"),
    (None, {'T1': {'id': 'T1', 'decision': 'same', 'with': 'T2', 'reason': 'r'},
            'T2': {'id': 'T2', 'decision': 'same', 'with': 'T1', 'reason': 'r'}}, 'name the topic that stays'),
])
def test_the_answer_must_settle_every_card_without_chains(cards, topics, message):
    with pytest.raises(ValueError, match=message):
        source_check.validate_answer(FROZEN, answer(cards, topics))


def test_every_card_needs_a_decision():
    partial = answer()
    partial['cards'] = partial['cards'][:2]
    with pytest.raises(ValueError, match='missing: c'):
        source_check.validate_answer(FROZEN, partial)
    assert source_check.validate_answer(FROZEN, answer()) is not None


def test_a_moved_link_never_makes_a_pair_point_both_ways():
    def card(slug, *targets):
        return {'slug': slug, 'title': slug, 'domain': 'd', 'pillar': 'p', 'topic': 't',
                'relations': [{'type': 'similar_to', 'target': t} for t in targets]}
    settled = [{'chunk': 'c01', 'accepted': {'a': card('a'), 'c': card('c', 'a')}, 'topics': {}},
               {'chunk': 'c02', 'accepted': {'b': card('b', 'c', 'a')}, 'topics': {}}]
    frozen = source_check.frozen_input(settled, {'filename': 'f'})
    out, report = source_check.apply(settled, frozen, answer({'b': {'slug': 'b', 'decision': 'same', 'with': 'a',
                                                                    'reason': 'r'}}, {}) | {'new_topics': []})
    kept = {s: c for o in out for s, c in o['accepted'].items()}
    assert set(kept) == {'a', 'c'}
    assert kept['a']['relations'] == []  # b -> c would be a -> c while c -> a exists; b -> a would be a -> a
    assert kept['c']['relations'] == [{'type': 'similar_to', 'target': 'a'}]
    assert report['moved_relations'] == [] and report['merged_into'] == {'b': 'a'}

"""The source check (drill 3): cards and topics two chunks repeat are merged, cards that belong together linked."""
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
           'propositions': CARDS['queue-order']['propositions'] + ['The order holds while markers are lit.'],
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
            cost = {**CARDS['lit-marker-cost'], 'confusions': ['Unlike [[Lantern queues keep arrival order]], it is '
                                                              'about memory, not order.'],
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


def test_a_card_two_chunks_both_write_is_written_once_with_what_it_adds_and_links_follow_it(tmp_path):
    same = {'arrival-order': {'decision': 'same', 'with': 'queue-order', 'reason': 'both say items keep arrival order',
                              'carry': {'propositions': CARDS['queue-order']['propositions']  # already there
                                        + ['The order holds while markers are lit.']}}}
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
                                                 'reason': 'both say items keep arrival order',
                                                 'carried': {'propositions': 1, 'flashcards': 0},
                                                 'repeats_skipped': 1}]
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
    kept = cards[ids['queue-order']]
    assert '- P2. The order holds while markers are lit.' in kept.body  # what it added, once
    assert kept.body.count('A lantern queue keeps every item in arrival order.') == 1
    # where the carried proposition stands in the source (J4 on the drill 3 replay: carried items lost it)
    assert [(s['locator'], s.get('propositions')) for s in kept.fm['sources']] == [
        (CARDS['queue-order']['source_quotes'][0], None), (ORDER_QUOTE, ['P2'])]
    # a [[link]] in another card's text to the merged card goes to the card it repeats
    cost = cards[ids['lit-marker-cost']].body
    assert '[[Arrival order in lantern queues]]' in cost and 'Lantern queues keep arrival order' not in cost
    assert result['source_check']['rewritten_links'] == 1


def test_cards_that_belong_together_are_linked_and_an_isolated_card_must_be_settled(tmp_path):
    def check(packet):
        cards = {c['slug']: c for c in packet['cards']}
        assert cards['queue-order']['isolated'] and not cards['lantern-queue']['isolated']
        assert cards['lantern-queue']['linked_from'] == ['arrival-order']
        assert 'part_of' in json.dumps(packet['relation_types'])
        bare = keep_all(packet)
        for row in bare['cards']:
            row.pop('alone', None)
        with pytest.raises(ValueError, match='queue-order has no relation'):
            source_check.validate_answer(packet, bare)
        return keep_all(packet, **{'queue-order': {'links': [
            {'target': 'arrival-order', 'type': 'similar_to', 'note': 'both say the order is kept'}]}})
    root, _, result, _ = run(tmp_path, check)
    ids = {p['slug']: p['id'] for p in result['produced']}
    assert len(ids) == 4 and result['needs_review'] == []
    assert result['source_check']['added_links'] == [
        {'from': ids['queue-order'], 'type': 'similar_to', 'target': ids['arrival-order']}]
    cards = read_snapshot(root).cards
    assert {'type': 'similar_to', 'target': ids['arrival-order'], 'note': 'both say the order is kept'} in \
        cards[ids['queue-order']].fm['relations']
    assert {'type': 'similar_to', 'target': ids['queue-order']} in cards[ids['arrival-order']].fm['relations']


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
    asked = []

    def bad(packet):
        asked.append(1)
        assert len(asked) < 5, 'the check is asked again and again'  # fail, do not hang, if the bound breaks
        return {'cards': [], 'new_topics': []}
    root, job_id, result, seen = run(tmp_path, bad)
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


def host_until_check(tmp_path):
    """The two-chunk source in host mode up to the source check: (root, job id, its hand-off, submit)."""
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

    def advance():
        return ingest.advance(root, job_id, producer=host, reviewer=host)
    first_round = advance()['handoffs']
    assert len(first_round) >= 2 and all(h['step'] == 'produce' for h in first_round)
    for handoff in first_round:  # all at once, as a coordinator runs them
        packet = packet_in(open(handoff['packet_path'], encoding='utf-8').read())
        assert packet['other_chunks_cards'] == []  # written at once: no chunk sees another's cards
        assert submit(handoff, producer(SimpleNamespace(packet=packet))) == 0
    for handoff in advance()['handoffs']:
        assert handoff['step'] == 'review'
        packet = packet_in(open(handoff['packet_path'], encoding='utf-8').read())
        assert submit(handoff, review(SimpleNamespace(packet=packet))) == 0
    [check] = advance()['handoffs']
    return root, job_id, check, submit, advance


def test_host_mode_hands_the_check_to_a_reviewer(tmp_path):
    root, job_id, check, submit, advance = host_until_check(tmp_path)
    assert check['role'] == 'reviewer' and check['chunk'] == source_check.SLOT and check['step'] == 'source_check'
    from secondbrain.executors.host import HostExecutor
    assert ingest.packet_for(root, job_id, role='reviewer', chunk=None, executor=HostExecutor())['run_id'] == \
        check['run_id']
    text = open(check['packet_path'], encoding='utf-8').read()
    assert 'merge cards that repeat each other' in text
    packet = packet_in(text)
    assert submit(check, {'cards': [], 'new_topics': []}) != 0  # every card needs a decision
    assert submit(check, keep_all(packet, **{'arrival-order': {'decision': 'same', 'with': 'queue-order',
                                                               'reason': 'same point'}})) == 0
    [diagram] = advance()['handoffs']
    request = packet_in(open(diagram['packet_path'], encoding='utf-8').read())
    assert 'arrival-order' not in [c['ref'] for c in request['cards']]  # no diagram for a card that is not written
    assert submit(diagram, diagram_answer(request)) == 0
    done = advance()
    assert done['status'] == 'applied' and len(done['produced']) == 3
    assert [m['slug'] for m in done['source_check']['merged']] == ['arrival-order']


def test_host_mode_gives_up_a_check_that_keeps_failing_only_after_two_failures(tmp_path, capsys):
    # V1 C6: a refused answer never reached the failure count, so only `sb job reset` (all chunk runs lost) was left
    root, job_id, check, submit, advance = host_until_check(tmp_path)

    def skip():
        return cli.main(['--vault', str(root), 'job', 'skip-source-check', job_id, '--reason', 'answers refused'])
    assert submit(check, {'cards': [], 'new_topics': []}) != 0
    capsys.readouterr()
    assert skip() != 0 and 'after 2 rejected answers or given-up runs' in capsys.readouterr().err
    assert submit(check, {'cards': [{'slug': 'nope', 'decision': 'keep'}], 'new_topics': []}) != 0
    assert jobs.load_run(root, job_id, check['run_id'])['rejected_submits'] == 2
    assert skip() == 0
    assert jobs.load_run(root, job_id, check['run_id'])['status'] == 'abandoned'
    [diagram] = advance()['handoffs']  # the check is not handed out again
    assert diagram['step'] == 'diagram'
    assert submit(diagram, diagram_answer(packet_in(open(diagram['packet_path'], encoding='utf-8').read()))) == 0
    done = advance()
    assert done['status'] == 'applied' and len(done['produced']) == 4
    assert done['source_check']['status'] == 'skipped' and 'answers refused' in done['source_check']['reason']
    assert skip() != 0  # nothing left to skip


def partial_parent(tmp_path, pending):
    """The two-chunk source ingested with the cards in ``pending`` still rejected after the repair round.

    Returns (root, job id, the parent's result, the producer's whole answer).
    """
    root = build_ingest_vault(tmp_path / 'vault')
    settings(chunk_chars=1000)
    (root / '00_Inbox' / SOURCE_NAME).write_text(SOURCE, encoding='utf-8')
    whole = produce()

    def parent_producer(task):
        answer = whole(task)
        if '.r' in task.chunk:  # the repair round offers only the cards the review sent back
            answer['cards'] = [c for c in answer['cards'] if c['slug'] in pending]
            answer['coverage'] = [r for r in answer['coverage'] if set(r.get('cards') or []) & pending]
        return answer

    def parent_reviewer(task):
        if is_source_check_task(task):
            return keep_all(task.packet)
        out = review(task)
        for row in out['cards']:
            if row['slug'] in pending:
                row.update(decision='revise', reason='say it more exactly')
        return out
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    parent = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=with_diagrams(parent_producer)),
                            reviewer=FakeExecutor('claude-test', responder=parent_reviewer))
    assert parent['status'] == 'partial' and {p['slug'] for p in parent['pending']} == pending
    return root, job['job_id'], parent, whole


def retry_pending(root, job_id, parent, whole, check, by_slug=False):
    """Retry ``job_id``: the producer offers the pending cards again (links to the written lantern card by id, or
    ``by_slug``), the chunk reviewer accepts them as distinct, and ``check(packet)`` answers the source check.

    Returns (the child's result, the source check tasks seen).
    """
    pending = {p['slug'] for p in parent['pending']}
    lantern = 'lantern-queue' if by_slug else next(p['id'] for p in parent['produced'] if p['slug'] == 'lantern-queue')
    child = ingest.retry(root, job_id)

    def child_producer(task):
        answer = whole(task)
        answer['cards'] = [{**c, 'relations': [{**r, 'target': lantern} for r in c.get('relations') or []
                                               if r['target'] == 'lantern-queue']}
                           for c in answer['cards'] if c['slug'] in pending]
        answer['coverage'] = [r for r in answer['coverage'] if not r.get('cards') or set(r['cards']) & pending]
        answer['new_topics'] = []
        return answer
    seen = []

    def child_reviewer(task):
        if is_source_check_task(task):
            seen.append(task)
            return check(task.packet)
        out = review(task)
        for row in out['cards']:
            row['overlap'] = 'distinct'
        return out
    done = ingest.advance(root, child['job_id'], producer=FakeExecutor('gpt-test', responder=with_diagrams(child_producer)),
                          reviewer=FakeExecutor('claude-test', responder=child_reviewer))
    return done, seen


def test_a_carried_link_to_a_card_the_retry_merges_goes_to_the_card_it_repeats(tmp_path):
    # V1: a retry's merged_into was untested (a mutant that ignored it survived)
    root, job_id, parent, whole = partial_parent(tmp_path, {'queue-order', 'arrival-order'})
    cost = next(p['id'] for p in parent['produced'] if p['slug'] == 'lit-marker-cost')
    assert parent['skipped_relations'] == [{'from': cost, 'type': 'depends_on', 'target_slug': 'arrival-order',
                                            'reason': 'target card was not written'}]
    done, _ = retry_pending(root, job_id, parent, whole, lambda packet: keep_all(packet, **{'arrival-order': {
        'decision': 'same', 'with': 'queue-order', 'reason': 'both say the order is kept'}}))
    assert done['status'] == 'applied', done.get('pending')
    order = next(p['id'] for p in done['produced'] if p['slug'] == 'queue-order')
    assert done['restored_relations'] == [{'from': cost, 'type': 'depends_on', 'target_slug': 'arrival-order',
                                           'target': order, 'redirected_from': 'arrival-order'}]
    assert {'type': 'depends_on', 'target': order} in read_snapshot(root).cards[cost].fm['relations']


# --- a retry's cards against the cards its source wrote (the real-vault ingest: a retry wrote, as confirmed, a card
# whose points three cards of the parent already held; the chunk reviewer had sent it to the source check, which
# by the retry no longer saw those cards) ------------------------------------------------------------------------
@pytest.mark.parametrize('by_slug', [False, True])
def test_a_single_chunk_retry_is_checked_against_the_cards_its_source_wrote(tmp_path, monkeypatch, by_slug):
    monkeypatch.setattr(source_check, 'NEAR_WRITTEN', 1)  # the closest written card, and the ones the card links
    root, job_id, parent, whole = partial_parent(tmp_path, {'arrival-order'})
    ids = {p['slug']: p['id'] for p in parent['produced']}
    before = ingest._card_digest(read_snapshot(root).cards[ids['queue-order']])[1]
    done, seen = retry_pending(root, job_id, parent, whole, keep_all, by_slug=by_slug)
    assert len(jobs.read_input(root, done['job_id'])['chunks']) == 1  # only the chunk with the pending card
    [task] = seen
    [card] = task.packet['cards']
    assert card['slug'] == 'arrival-order'
    near = {e['id']: e for e in card['near_written']}
    assert list(near) == [ids['queue-order'], ids['lantern-queue']]  # the closest, then the one it links
    assert near[ids['queue-order']]['propositions'] == before and before
    assert near[ids['queue-order']]['essence'] and near[ids['queue-order']]['title'] == CARDS['queue-order']['title']
    # kept: written as reviewed, and the carried link from the parent's cost card reaches it
    assert done['status'] == 'applied', done.get('pending')
    arrival = next(p['id'] for p in done['produced'] if p['slug'] == 'arrival-order')
    assert read_snapshot(root).cards[arrival].fm['status'] == 'confirmed'
    assert done['needs_review'] == [] and done['source_check']['same_as_written'] == []
    assert [r['target'] for r in done['restored_relations']] == [arrival]


def test_a_retry_card_that_repeats_a_written_card_is_written_next_to_it_as_needs_review(tmp_path):
    root, job_id, parent, whole = partial_parent(tmp_path, {'arrival-order'})
    order = next(p['id'] for p in parent['produced'] if p['slug'] == 'queue-order')
    before = read_snapshot(root).cards[order]
    reason = 'both say items keep arrival order; this one adds the lit markers'
    done, _ = retry_pending(root, job_id, parent, whole, lambda packet: keep_all(packet, **{'arrival-order': {
        'decision': 'same', 'with': order, 'reason': reason,
        'links': [{'target': order, 'type': 'similar_to', 'note': 'the same order rule'}]}}))
    assert done['status'] == 'applied', done.get('pending')
    arrival = next(p['id'] for p in done['produced'] if p['slug'] == 'arrival-order')
    after = read_snapshot(root)
    assert after.cards[arrival].fm['status'] == 'needs-review'
    assert {'type': 'similar_to', 'target': order} in [{k: r[k] for k in ('type', 'target')}
                                                         for r in after.cards[arrival].fm['relations']]
    assert done['needs_review'] == [{'id': arrival, 'title': ARRIVAL['title'], 'overlap_with': order, 'reason': reason}]
    assert done['source_check']['same_as_written'] == [{'slug': 'arrival-order', 'title': ARRIVAL['title'],
                                                        'with': order, 'reason': reason, 'id': arrival}]
    assert done['source_check']['merged'] == []
    # the written card is not changed: nothing carried to it, only the back link of the new relation
    assert ingest._card_digest(after.cards[order]) == ingest._card_digest(before)
    assert after.cards[order].fm['sources'] == before.fm['sources']
    assert [r['target'] for r in done['restored_relations']] == [arrival]  # kept, not merged away


def test_needs_a_check_for_two_chunks_or_for_any_accepted_card_of_a_retry_whose_source_wrote_cards():
    one, two, none = [{'accepted': {'a': {}}}], [{'accepted': {'a': {}}}, {'accepted': {'b': {}}}], [{'accepted': {}}]
    assert source_check.needed(two) and source_check.needed(two, written=True)
    assert not source_check.needed(one) and source_check.needed(one, written=True)
    assert not source_check.needed(none, written=True)


# --- the answer's form ------------------------------------------------------------------------------
def frozen_card(slug, *targets, isolated=False, domain='d', near=(), written=None):
    card = {'slug': slug, 'chunk': 'c01', 'title': slug, 'domain': domain, 'pillar': 'p', 'topic': 't',
            'propositions': [f'{slug} one', f'{slug} two'],
            'flashcards': [{'q': f'{slug} q{i}?', 'a': f'{slug} a{i}'} for i in (1, 2)],
            'relations': [{'type': 'similar_to', 'target': t} for t in targets], 'linked_from': [],
            'isolated': isolated, 'near_existing': [{'id': i, 'title': i, 'domain': 'other'} for i in near]}
    if written is not None:  # a retry: the closest cards earlier jobs of the source wrote
        card['near_written'] = [{'id': i, 'slug': i[3:], 'title': i, 'domain': 'd', 'essence': i,
                                 'propositions': [f'{i} one']} for i in written]
    return card


FROZEN = {'cards': [frozen_card('a', 'b'), frozen_card('b'), frozen_card('c', 'a', near=['kc_old'])],
          'new_topics': [{'id': f'T{i}', 'domain': 'd', 'pillar': 'p', 'name': f'n{i}'} for i in (1, 2)]}


def answer(cards=None, topics=None):
    rows = {s: {'slug': s, 'decision': 'keep'} for s in ('a', 'b', 'c')}
    rows.update(cards or {})
    trows = {t: {'id': t, 'decision': 'keep'} for t in ('T1', 'T2')}
    trows.update(topics or {})
    return {'cards': list(rows.values()), 'new_topics': list(trows.values())}


def same(slug, into, **fields):
    return {slug: {'slug': slug, 'decision': 'same', 'with': into, 'reason': 'r', **fields}}


def links(slug, *rows):
    return {slug: {'slug': slug, 'decision': 'keep', 'links': [dict(zip(('target', 'type'), r)) for r in rows]}}


@pytest.mark.parametrize('cards, topics, message', [
    ({**same('a', 'b'), **same('b', 'c')}, None, 'name c instead'),
    ({'a': {'slug': 'a', 'decision': 'keep', 'with': 'b'}}, None, '"keep" takes no "with" or "carry"'),
    (same('a', 'a'), None, 'another slug'),
    ({'a': {'slug': 'a', 'decision': 'same', 'with': 'b'}}, None, 'what the two share'),
    ({'z': {'slug': 'z', 'decision': 'keep'}}, None, "'z' is not in the packet"),
    (None, {'T1': {'id': 'T1', 'decision': 'same', 'with': 'T2', 'reason': 'r'},
            'T2': {'id': 'T2', 'decision': 'same', 'with': 'T1', 'reason': 'r'}}, 'name the topic that stays'),
    (same('a', 'b', carry={'propositions': ['a one, reworded']}), None, 'copied exactly'),
    (same('a', 'b', carry={'flashcards': [{'q': 'a q1?', 'a': 'another answer'}]}), None, 'copied exactly'),
    (same('a', 'b', carry={'replace': [{'proposition': 'P3', 'by': 'a one'}]}), None, 'P1–P2'),
    (same('a', 'b', carry={'replace': [{'proposition': 'P1', 'by': 'a one, reworded'}]}), None, 'copied exactly'),
    (same('a', 'b', carry={'replace': [{'proposition': 'P1', 'by': 'a one'}], 'propositions': ['a one']}), None,
     'not also in carry.propositions'),
    (same('a', 'b', carry={'replace': [{'proposition': 'P1', 'by': 'a one'}]})
     | same('c', 'b', carry={'replace': [{'proposition': 'P1', 'by': 'c one'}]}), None, 'replaced twice'),
    (same('a', 'b', links=[{'target': 'c', 'type': 'similar_to'}]), None, 'put its links on b'),
    ({**same('a', 'b'), **links('c', ('a', 'similar_to'))}, None, 'link b'),
    (links('b', ('a', 'similar_to')), None, 'linked already'),
    (links('b', ('kc_elsewhere', 'similar_to')), None, 'near_existing ids'),
    (links('b', ('c', 'tool_for')), None, "'tool_for' is not open"),
])
def test_the_answer_must_settle_every_card_without_chains(cards, topics, message):
    def closed(kind, domain, target):
        return None if kind == 'similar_to' else f'{kind!r} is not open to domain {domain}'
    with pytest.raises(ValueError, match=message):
        source_check.validate_answer(FROZEN, answer(cards, topics), closed=closed)


RETRY = {**FROZEN, 'cards': [frozen_card('a', 'b', written=[]), frozen_card('b', 'w2', written=['kc_w2']),  # by slug
                             frozen_card('c', 'a', near=['kc_old'], written=['kc_w'])]}


@pytest.mark.parametrize('cards, message', [
    (same('c', 'kc_old', links=[{'target': 'kc_old', 'type': 'similar_to'}]), 'or one of its near_written ids'),
    (same('a', 'kc_w'), 'another slug of the packet(?! or)'),
    (same('c', 'kc_w', carry={'propositions': ['c one']}, links=[{'target': 'kc_w', 'type': 'similar_to'}]),
     'give no "carry"'),
    (same('c', 'kc_w'), 'link it in "links"'),
    ({**same('b', 'kc_w2', links=[{'target': 'kc_w2', 'type': 'similar_to'}])}, 'linked already'),
])
def test_a_retry_card_may_repeat_only_a_written_card_listed_for_it_and_must_link_it_without_carrying(cards, message):
    with pytest.raises(ValueError, match=message):
        source_check.validate_answer(RETRY, answer(cards))


def test_a_retry_card_that_repeats_a_written_card_it_links_or_links_now_is_valid_and_keeps_its_links():
    for cards in (same('b', 'kc_w2'), same('c', 'kc_w', links=[{'target': 'kc_w', 'type': 'similar_to'}]),
                  links('c', ('kc_w', 'similar_to')), {**same('b', 'kc_w2'), **links('c', ('b', 'similar_to'))}):
        assert source_check.validate_answer(RETRY, answer(cards))
    settled = [{'chunk': 'c01', 'accepted': {s: {'title': s, 'domain': 'd', 'pillar': 'p', 'topic': 't',
                                                 'relations': [{'type': 'similar_to', 'target': 'kc_w2'}] if s == 'b'
                                                 else [], 'propositions': [f'{s} one']} for s in 'abc'},
                'topics': {}}]
    out, report = source_check.apply(settled, RETRY, answer({**same('b', 'kc_w2'), **links('c', ('b', 'similar_to'))}))
    kept = out[0]['accepted']
    assert set(kept) == {'a', 'b', 'c'} and kept['b']['_overlap_with'] == 'kc_w2' and kept['b']['_overlap_reason'] == 'r'
    assert kept['b']['propositions'] == ['b one'] and kept['c']['relations'] == [{'type': 'similar_to', 'target': 'b'}]
    assert report['merged'] == [] and report['merged_into'] == {}
    assert report['same_as_written'] == [{'slug': 'b', 'title': 'b', 'with': 'kc_w2', 'reason': 'r'}]


def test_a_card_merged_into_may_hold_twice_the_flashcards_of_a_new_card():
    big = {**frozen_card('b'), 'flashcards': [{'q': f'b q{i}?', 'a': f'b a{i}'} for i in range(1, 7)]}
    frozen = {**FROZEN, 'cards': [FROZEN['cards'][0], big, FROZEN['cards'][2]]}
    two = {'flashcards': [{'q': 'a q1?', 'a': 'a a1'}, {'q': 'a q2?', 'a': 'a a2'}]}
    assert source_check.validate_answer(frozen, answer(same('a', 'b', carry=two)))  # 6 + 2 = 8
    with pytest.raises(ValueError, match='b would have 2 propositions and 9 flashcards'):
        source_check.validate_answer(frozen, answer({**same('a', 'b', carry=two), **same(
            'c', 'b', carry={'flashcards': [{'q': 'c q1?', 'a': 'c a1'}]})}))


def test_every_card_needs_a_decision_and_an_isolated_one_a_link_or_a_reason():
    partial = answer()
    partial['cards'] = partial['cards'][:2]
    with pytest.raises(ValueError, match='missing: c'):
        source_check.validate_answer(FROZEN, partial)
    assert source_check.validate_answer(FROZEN, answer()) is not None
    frozen = {**FROZEN, 'cards': FROZEN['cards'] + [frozen_card('d', isolated=True, near=['kc_old'])]}

    def with_d(row=None, **cards):
        out = answer(cards)
        out['cards'].append({'slug': 'd', 'decision': 'keep', **(row or {})})
        return out
    with pytest.raises(ValueError, match='d has no relation'):
        source_check.validate_answer(frozen, with_d())
    for settled in (with_d({'alone': 'a fact of its own'}),  # a reason, a link of its own, or another card's link
                    with_d({'links': [{'target': 'kc_old', 'type': 'similar_to'}]}),
                    with_d(**links('c', ('d', 'similar_to')))):
        assert source_check.validate_answer(frozen, settled) is settled


def test_a_merge_carries_what_the_card_adds_and_a_moved_link_never_makes_a_pair_point_both_ways():
    def card(slug, *targets):
        return {'slug': slug, 'title': slug, 'domain': 'd', 'pillar': 'p', 'topic': 't', 'essence': 'e',
                'propositions': [f'{slug} one'] + ([f'{slug} two, with an example'] if slug == 'b' else []),
                'flashcards': [{'q': f'{slug}?', 'a': slug}],
                'source_quotes': [f'{slug} quote'], 'relations': [{'type': 'similar_to', 'target': t} for t in targets]}
    settled = [{'chunk': 'c01', 'accepted': {'a': card('a'), 'c': card('c', 'a')}, 'topics': {}},
               {'chunk': 'c02', 'accepted': {'b': card('b', 'c', 'a')}, 'topics': {}}]
    frozen = source_check.frozen_input(settled, {'filename': 'f'}, relation_views={})
    reply = answer(same('b', 'a', carry={'propositions': ['b one'], 'flashcards': [{'q': 'b?', 'a': 'b'}],
                                         'replace': [{'proposition': 'P1', 'by': 'b two, with an example'}]}))
    reply['new_topics'] = []
    assert source_check.validate_answer(frozen, reply) is reply
    out, report = source_check.apply(settled, frozen, reply)
    kept = {s: c for o in out for s, c in o['accepted'].items()}
    assert set(kept) == {'a', 'c'}
    assert kept['a']['relations'] == []  # b -> c would be a -> c while c -> a exists; b -> a would be a -> a
    assert kept['c']['relations'] == [{'type': 'similar_to', 'target': 'a'}]
    assert kept['a']['propositions'] == ['b two, with an example', 'b one'] and len(kept['a']['flashcards']) == 2
    assert kept['a']['_carried'] == [{'locator': 'b quote', 'propositions': [1, 2], 'flashcards': [2]}]
    assert report['merged'][0]['replaced'] == 1
    assert kept['a']['source_quotes'] == ['a quote', 'b quote']
    assert report['moved_relations'] == [] and report['merged_into'] == {'b': 'a'}
    assert settled[0]['accepted']['a']['propositions'] == ['a one']  # nothing changed in place


def test_a_check_of_other_cards_than_the_chunks_now_accept_is_refused():
    def card(slug):
        return {'slug': slug, 'title': slug, 'domain': 'd', 'pillar': 'p', 'topic': 't', 'relations': []}
    settled = [{'chunk': 'c01', 'accepted': {'a': card('a')}, 'topics': {}},
               {'chunk': 'c02', 'accepted': {'b': card('b')}, 'topics': {}}]
    frozen = source_check.frozen_input(settled, {'filename': 'f'}, relation_views={})
    settled[1]['accepted'] = {'c': card('c')}
    with pytest.raises(ValueError, match='different set of cards'):
        source_check.apply(settled, frozen, {'cards': [{'slug': s, 'decision': 'keep'} for s in 'ab'],
                                             'new_topics': []})


def test_links_to_a_merged_card_follow_it_and_a_link_to_the_card_itself_becomes_text():
    mapping = {'Old': 'Kept'}
    assert source_check._retitle('see [[Old]], [[Old|that card]] and [[Old#P2]]', mapping, 'Other') == (
        'see [[Kept]], [[Kept|that card]] and [[Kept#P2]]', 3)
    assert source_check._retitle('unlike [[Old]] or [[Old|the twin]]', mapping, 'Kept') == (
        'unlike Kept or the twin', 2)
    card = {'title': 'Kept', 'essence': 'e', 'propositions': ['[[Other]] stays'], 'confusions': ['vs [[Old]]'],
            'flashcards': [{'q': 'Why [[Old]]?', 'a': 'a'}], 'relations': [{'type': 'x', 'target': 't', 'note': '[[Old]]'}]}
    assert source_check._retitle_card(card, mapping) == 3
    assert card['confusions'] == ['vs Kept'] and card['propositions'] == ['[[Other]] stays']


def test_a_carried_question_the_kept_card_already_asks_word_for_word_is_skipped():
    # V1 on 83de981: a mutant that kept such a repeat survived, as no test carried one
    def card(slug, flashcards):
        return {'slug': slug, 'title': slug, 'domain': 'd', 'pillar': 'p', 'topic': 't', 'essence': 'e',
                'propositions': [f'{slug} one'], 'flashcards': flashcards, 'source_quotes': [f'{slug} quote'],
                'relations': []}
    asked = [{'q': 'What does x hold?', 'a': 'one'}]
    again = [{'q': 'What does x hold ?', 'a': 'the number one'}, {'q': 'Why is y kept?', 'a': 'y'}]
    settled = [{'chunk': 'c01', 'accepted': {'a': card('a', asked)}, 'topics': {}},
               {'chunk': 'c02', 'accepted': {'b': card('b', again)}, 'topics': {}}]
    frozen = source_check.frozen_input(settled, {'filename': 'f'}, relation_views={})
    reply = {'cards': [{'slug': 'a', 'decision': 'keep', 'alone': 'a fact of its own'},
                       {'slug': 'b', 'decision': 'same', 'with': 'a', 'reason': 'r', 'carry': {'flashcards': again}}],
             'new_topics': []}
    assert source_check.validate_answer(frozen, reply) is reply
    out, report = source_check.apply(settled, frozen, reply)
    kept = out[0]['accepted']['a']
    assert [f['q'] for f in kept['flashcards']] == ['What does x hold?', 'Why is y kept?']
    assert report['merged'][0]['carried'] == {'propositions': 0, 'flashcards': 1}
    assert report['merged'][0]['repeats_skipped'] == 1
    assert kept['_carried'] == [{'locator': 'b quote', 'propositions': [], 'flashcards': [2]}]

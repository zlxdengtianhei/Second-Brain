"""A card that names another card of its source by ``[[title]]`` is reviewed with that card's propositions at hand.

An example may take its premises from another chunk of the source when the card names the card of that chunk that
states them; but the review packet showed the other chunks' cards by title and essence only, so the reviewer could
not check what the named card is cited for. Now the cards of other chunks that a reviewed card names come with their
propositions; the others stay as they were (title, domain, essence), so the packet grows only by the cards named. A
split card's packets (its writing and its review) list the other chunks' cards the same way: the rule they are
given points at them.

A card may name a card of the vault by ``[[title]]`` too, in a confusion say; that card is a source of it (decision
D-33). It was not in the review packet unless the search found it, and a reviewer sent a right card back for citing
a card it could not see. Now the vault cards a card names in its text come whole, as the cards it names in a
relation do, and the check of a review's notes gets them too. The cards and texts are invented.
"""
from __future__ import annotations

import copy

from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest, split
from tests.ingest_helpers import with_diagrams
from tests.test_invariants import two_chunk_job, two_chunk_producer, two_chunk_reviewer

PLAIN = {'slug', 'title', 'domain', 'essence', 'chunk'}


def test_the_review_packet_carries_the_propositions_of_the_cards_its_cards_name(tmp_path):
    root, job_id = two_chunk_job(tmp_path)
    named = []

    def produce(task):
        answer = copy.deepcopy(two_chunk_producer(task))
        others = task.packet.get('other_chunks_cards') or []
        if others and answer['cards'] and not split.is_slot(task.chunk):  # the second chunk names a card of the first
            named.append(others[0]['title'])
            answer['cards'][0]['propositions'][0] += f' (as [[{others[0]["title"]}]] says)'
        return answer
    seen, reviews = [], []
    plain = two_chunk_reviewer(seen)

    def review(task):
        if 'candidate' in task.packet and task.packet.get('other_chunks_cards'):
            reviews.append(task)
        return plain(task)
    ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=with_diagrams(produce)),
                   reviewer=FakeExecutor('claude-test', responder=review))
    assert named and reviews
    shown = {c['title']: c for t in reviews for c in t.packet['other_chunks_cards']}
    assert shown[named[0]]['propositions']                                   # named: with its propositions
    assert all(set(c) == PLAIN for title, c in shown.items() if title not in named)  # the others as before


def test_named_titles_reads_links_with_an_alias_or_a_heading():
    card = {'essence': 'See [[Lantern queue|the queue]] and [[Arrival order#Rule]].', 'propositions': ['[[ Lit ]]'],
            'flashcards': [{'q': 'Which?', 'a': '[[Marker cost]]'}]}
    assert ingest.named_titles([card]) == {'Lantern queue', 'Arrival order', 'Lit', 'Marker cost'}


def test_a_split_cards_packets_list_the_other_chunks_cards(tmp_path):
    from tests import test_patch_set as tp
    root, job_id, _ = tp.job(tmp_path, n_props=11)
    packets = []

    def review(task):
        if split.is_slot(task.chunk.split('.')[0]):
            packets.append(task.packet)
        return tp.reviewer()(task)

    def produce(task):
        if split.is_slot(task.chunk.split('.')[0]):
            packets.append(task.packet)
        return tp.producer()(task)
    tp.run(root, job_id, review, produce)
    assert len(packets) >= 2 and all('other_chunks_cards' in p for p in packets)
    assert any(p.get('other_chunks_cards') for p in packets)


def test_a_vault_card_a_card_names_by_title_comes_whole_in_its_review_packet(tmp_path):
    from tests.ingest_helpers import OTHER_CARD, first_candidate, first_review
    from tests.test_review_rules import run
    answer = copy.deepcopy(first_candidate())
    cost = next(c for c in answer['cards'] if c['slug'] == 'lit-marker-cost')
    cost['confusions'] = list(cost.get('confusions') or []) + ['Not a matter of size, unlike [[Pebble sizes]].']
    others = {c['slug'] for c in answer['cards']} - {'lit-marker-cost'}
    _, _, _, seen = run(tmp_path, [answer], [first_review()], max_repair_rounds=0)
    packet = seen['reviewer'][0].packet
    assert OTHER_CARD in packet['candidate_cards']
    assert 'Pebbles come in sizes' in str(packet['candidate_cards'][OTHER_CARD])  # whole
    [row] = [r for r in packet['candidates']['lit-marker-cost'] if r['id'] == OTHER_CARD]
    assert 'named' in row['via']
    # the cards that do not name it may still meet it by search (a vault of three cards), never as named
    assert all('named' not in r['via'] for s in others for r in packet['candidates'].get(s) or []
               if r['id'] == OTHER_CARD)


def test_the_check_of_a_note_gets_the_vault_cards_the_card_names(tmp_path):
    from tests.ingest_helpers import OTHER_CARD, first_candidate, first_review
    from tests.test_note_check import answer as check_answer, noted
    from tests.test_review_rules import run
    candidate = copy.deepcopy(first_candidate())
    cost = next(c for c in candidate['cards'] if c['slug'] == 'lit-marker-cost')
    cost['confusions'] = list(cost.get('confusions') or []) + ['Not a matter of size, unlike [[Pebble sizes]].']
    _, _, _, seen = run(tmp_path, [candidate], [noted(), check_answer('no')], max_repair_rounds=0)
    check = seen['reviewer'][1].packet
    assert seen['reviewer'][1].chunk == 'nc-c01' and list(check['named_cards']) == [OTHER_CARD]
    assert 'Pebbles come in sizes' in str(check['named_cards'][OTHER_CARD])

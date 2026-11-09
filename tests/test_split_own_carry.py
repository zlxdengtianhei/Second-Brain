"""Each split slot rewrites only what its own card adds; the reviewer maps each proposition (decision D-22).

H7 v2: when several cards extend one existing card and their patch would make it too long, every split slot was
given the whole patch. Two slots then wrote the same narrower card, and what the other cards added was lost
(ch8-dg-new sp02, sp04; SB6-dg-new sp01, sp02). And a split card said two propositions of the existing card again
in other words (SB1-dg-new). Now a slot gets what its own card (and any card of the source merged into it) adds;
a card the existing card already says all of gets no slot; two narrower cards of one card that say the same
point are asked again (D-21: never cut); and the split reviewer says, per proposition, which proposition of the
existing card says it: one that does sends the card back to be written again.
"""
from __future__ import annotations

import copy

from secondbrain import jobs
from secondbrain.pipelines import split
from secondbrain.textnorm import near_same, same_text
from secondbrain.vault import read_snapshot
from tests.helpers import EXISTING
from tests.ingest_helpers import CARDS, keep_all, with_diagrams
from tests.test_invariants import ARRIVAL, LEVELS, two_chunk_producer
from tests.test_patch_set import SPLIT_CARD, job, reviewer, run

LANTERN, LIT = CARDS['lantern-queue'], CARDS['lit-marker-cost']
LEVELS3 = {**LEVELS, 'lit-marker-cost': {'overlap': 'extends', 'with': [EXISTING]},
           'arrival-order': {'overlap': 'extends', 'with': [EXISTING]}}
LIT_SPLIT = {'slug': 'marker-memory-cost', 'title': 'What lit markers cost in memory', 'kind': 'mechanism',
             'essence': 'Each lit marker costs one word of memory.',
             'propositions': ['Each marker costs one word of memory; 800 items carry 100 markers.'],
             'intuition': None, 'confusions': [],
             'flashcards': [{'q': 'What does one marker cost?', 'a': 'One word of memory.'},
                            {'q': 'How many markers do 800 items carry?', 'a': '100.'}],
             'source_quotes': [LIT['source_quotes'][1]],
             'relations': [{'type': 'part_of', 'target': EXISTING, 'note': 'one case of routing readers'}]}


def producing(split_answers):
    """The two-chunk producer, with arrival-order linking the existing card; split slots answered in turn."""
    seen = []

    def respond(task):
        base = task.chunk.split('.')[0]
        if split.is_slot(base):
            seen.append(task)
            return split_answers(task, seen)
        out = copy.deepcopy(two_chunk_producer(task))  # the fixture cards are shared
        for c in out.get('cards') or []:
            if c['slug'] in ('arrival-order', 'lit-marker-cost') and EXISTING not in {r['target'] for r in c['relations']}:
                c['relations'] = [*c['relations'], {'type': 'part_of', 'target': EXISTING, 'note': 'n'}]
        return out
    return with_diagrams(respond), seen


def check(packet):
    """One patch for card_exist from three cards: arrival-order's one proposition the card already says (P1), and
    its two review cards the card asks already (Q1, Q2)."""
    return keep_all(packet, _patches={EXISTING: {
        'propositions': LANTERN['propositions'] + LIT['propositions'],
        'flashcards': LANTERN['flashcards'] + LIT['flashcards'],
        'already': [{'text': ARRIVAL['propositions'][0], 'as': 'P1'}]
        + [{'text': f['q'], 'as': f'Q{i}'} for i, f in enumerate(ARRIVAL['flashcards'], 1)]}})


def by_slot(task, seen):
    item = task.packet['card']['slug']
    return {'card': SPLIT_CARD if item == 'lantern-queue' else LIT_SPLIT}


def test_each_split_slot_gets_only_what_its_own_card_adds_and_a_covered_card_none(tmp_path):
    root, job_id, before = job(tmp_path, n_props=11)  # 11 + 4 > 12: the patch goes to split slots
    produce, seen = producing(by_slot)
    done = run(root, job_id, reviewer(levels=LEVELS3, check=check), produce)
    assert done['status'] == 'applied', done.get('pending')
    plan = {i['slug']: i for i in jobs.load(root, job_id)['split_plan']}
    assert sorted(plan) == ['lantern-queue', 'lit-marker-cost']             # arrival-order: no slot
    assert plan['lantern-queue']['carry'] == {'propositions': LANTERN['propositions'],
                                              'flashcards': LANTERN['flashcards']}
    assert plan['lit-marker-cost']['carry'] == {'propositions': LIT['propositions'], 'flashcards': LIT['flashcards']}
    assert [t.packet['carry'] for t in seen] == [plan[s]['carry'] for s in ('lantern-queue', 'lit-marker-cost')]
    assert done['patch_set'] == {'lantern-queue': 'split', 'lit-marker-cost': 'split', 'arrival-order': 'covered'}
    assert done['outcomes']['arrival-order'] == 'drop'
    titles = {c.title for c in read_snapshot(root).cards.values()}
    assert {SPLIT_CARD['title'], LIT_SPLIT['title']} <= titles and ARRIVAL['title'] not in titles


def test_two_narrower_cards_of_one_card_that_say_the_same_point_are_asked_again(tmp_path):
    root, job_id, before = job(tmp_path, n_props=11)

    def answers(task, seen):
        if task.packet['card']['slug'] == 'lantern-queue':
            return {'card': SPLIT_CARD}
        if len(seen) == 2:  # the second slot first says sp01's point again
            return {'card': {**LIT_SPLIT, 'propositions': [*LIT_SPLIT['propositions'], SPLIT_CARD['propositions'][0]]}}
        return {'card': LIT_SPLIT}
    produce, seen = producing(answers)
    done = run(root, job_id, reviewer(levels=LEVELS3, check=check), produce)
    assert done['status'] == 'applied', done.get('pending')
    state = jobs.load(root, job_id)
    error = state['chunks']['sp02']['last_validation_error']
    assert f"{SPLIT_CARD['propositions'][0]!r} says what the narrower card {SPLIT_CARD['slug']} of {EXISTING}" in error
    made = next(c for c in read_snapshot(root).cards.values() if c.title == LIT_SPLIT['title'])
    assert SPLIT_CARD['propositions'][0] not in made.body                    # written once, by sp01's card


def test_two_narrower_cards_with_near_matching_propositions_are_asked_again(tmp_path):
    root, job_id, before = job(tmp_path, n_props=11)
    first = '自由泳脸在水里时要一直慢慢呼气／转头出水换气时只用吸气'
    second = '自由泳换气时，脸在水里要一直慢慢呼气／转头露出水面时只用吸气'
    assert not same_text(first, second) and near_same(first, second)

    def answers(task, seen):
        if task.packet['card']['slug'] == 'lantern-queue':
            return {'card': {**SPLIT_CARD, 'propositions': [first]}}
        if len(seen) == 2:
            return {'card': {**LIT_SPLIT, 'propositions': [second]}}
        return {'card': LIT_SPLIT}

    produce, seen = producing(answers)
    done = run(root, job_id, reviewer(levels=LEVELS3, check=check), produce)
    assert done['status'] == 'applied', done.get('pending')
    assert [t.chunk for t in seen] == ['sp01', 'sp02', 'sp02']  # an invalid answer is retried in the same slot
    error = jobs.load(root, job_id)['chunks']['sp02']['last_validation_error']
    assert first in error and second in error and SPLIT_CARD['slug'] in error


def test_a_split_card_whose_proposition_the_existing_card_says_goes_back_to_be_rewritten(tmp_path):
    root, job_id, before = job(tmp_path, n_props=11)
    reviews = []

    def split_review(packet):
        reviews.append(packet)
        n = len(packet['card']['propositions'])
        hit = [{'proposition': 'P1', 'existing': 'P2'}] if len(reviews) == 1 else []
        return {'decision': 'accept', 'restates': False, 'reason': 'faithful',
                'matches': hit + [{'proposition': f'P{i}', 'existing': None} for i in range(1 + len(hit), n + 1)]}
    produce, seen = producing(lambda task, s: {'card': SPLIT_CARD})
    done = run(root, job_id, reviewer(split_review=split_review), produce)
    assert done['status'] == 'applied', done.get('pending')
    assert [t.chunk for t in seen] == ['sp01', 'sp01.r1']                      # sent back once, rewritten
    first = jobs.artifact(root, jobs.load(root, job_id), 'review', 'sp01')
    assert first['decision'] == 'revise' and first['turned']['matches'] == [['P1', 'P2']]
    assert 'P1 says what the existing card P2 says' in seen[1].packet['review_findings']['reason']
    assert done['split_restated'] == [{'chunk': 'c01', 'slot': 'sp01', 'round': 0, 'slug': 'lantern-queue',
                                       'with': EXISTING, 'matches': [['P1', 'P2']]}]
    assert done['notes_turned'] == []


def test_a_split_review_must_map_every_proposition():
    card = {'propositions': ['a', 'b']}
    ok = {'decision': 'accept', 'restates': False, 'reason': 'r',
          'matches': [{'proposition': 'P1', 'existing': None}, {'proposition': 'P2', 'existing': None}]}
    assert split.validate_review(copy.deepcopy(ok), card=card, existing=3)['decision'] == 'accept'
    for bad, says in (({**ok, 'matches': ok['matches'][:1]}, 'matches must name each proposition'),
                      ({**ok, 'matches': [{'proposition': 'P1', 'existing': 'P9'}, ok['matches'][1]]},
                       "'P9' for P1 must be a proposition of existing_card")):
        try:
            split.validate_review(bad, card=card, existing=3)
        except ValueError as exc:
            assert says in str(exc)
        else:
            raise AssertionError('accepted')

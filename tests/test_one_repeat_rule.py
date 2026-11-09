"""What the program may leave out by itself is decided in one place, and checked before a card is written.

The rule (decision D-21): two propositions in the same words once spaces are put right, or two review cards whose
question and answer both are, become one. Anything else the comparison finds the same goes back to the merge check.
The fourth verdict found the patch path still left out a review card whose question was the same words as another's
while its answer was not (K4'''): ``_patch_fits`` compared questions only. Now every place that leaves an item out
asks ``source_check.repeat``, and before a card that took a patch or a carry is written the program checks that
every item of it is in the card (``source_check.lost``); a fault in the rule stops the job instead of losing an item.

The source is the invented two-chunk source of tests/test_invariants.py.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from secondbrain import cards as cardfmt
from secondbrain.pipelines import ingest, source_check
from secondbrain.store import Conflict
from secondbrain.vault import read_snapshot
from tests.helpers import EXISTING
from tests.ingest_helpers import CARDS, keep_all, with_diagrams
from tests.test_invariants import LEVELS, two_chunk_producer
from tests.test_patch_set import card_of, job, reviewer, run, unchanged

Q = 'What order does a lantern queue keep?'
A1, A2 = 'Arrival order.', 'Arrival order, except that a lit marker may be read first.'


def every_flashcard(root):
    return [(f['q'], f['a']) for c in read_snapshot(root).cards.values() for f in cardfmt.flashcards_of(c.body) or []]


def same_question_producer(task):
    """lantern-queue asks Q with answer A1; queue-order, which extends the existing card too, asks Q with A2."""
    out = copy.deepcopy(two_chunk_producer(task))
    for c in out.get('cards') or []:
        if c['slug'] == 'lantern-queue':
            c['flashcards'] = [{'q': Q, 'a': A1}] + list(c['flashcards'])[1:]
        if c['slug'] == 'queue-order':
            c['flashcards'] = [{'q': Q, 'a': A2}] + list(c['flashcards'])[1:]
            c['relations'] = [{'type': 'similar_to', 'target': EXISTING, 'note': 'n'}]
    return out


BOTH_EXTEND = {**LEVELS, 'queue-order': {'overlap': 'extends', 'with': [EXISTING]}}


# --- P-F1 F1: the patch path, the whole ingest -------------------------------------------------------------------
def test_f1_a_patch_asking_one_question_with_two_answers_goes_back_and_neither_answer_is_lost(tmp_path):
    root, job_id, _ = job(tmp_path)
    checks = []
    done = run(root, job_id, reviewer(checks, levels=BOTH_EXTEND), with_diagrams(same_question_producer))
    assert len(checks) > 1  # sent back to the merge check, naming both answers
    refusals = [r['message'] for r in done['source_check_refusals']]
    assert refusals and all(A1 in m and A2 in m and 'differs' in m for m in refusals)
    assert (Q, A1) in every_flashcard(root) and (Q, A2) in every_flashcard(root)


def test_f1_the_merge_check_may_keep_both_answers_with_differs(tmp_path):
    root, job_id, before = job(tmp_path)
    checks = []

    def check(p):
        [group] = p['patch_set']
        flashcards = [f for c in group['from'] for f in c['flashcards']]
        props = [x for c in group['from'] for x in c['propositions']]
        return keep_all(p, _patches={EXISTING: {
            'propositions': props, 'flashcards': flashcards,
            'differs': [{'text': Q, 'from': Q, 'reason': 'the second answer adds the lit-marker exception'}]}})
    done = run(root, job_id, reviewer(checks, check=check, levels=BOTH_EXTEND), with_diagrams(same_question_producer))
    assert done['status'] == 'applied' and len(checks) == 1
    old = cardfmt.flashcards_of(card_of(root, EXISTING).body)
    assert {'q': Q, 'a': A1} in old and {'q': Q, 'a': A2} in old
    [merged] = done['merged_into_existing']
    assert merged['flashcards'] == 4


# --- P-F1 F3: the program's part alone ---------------------------------------------------------------------------
OLD = SimpleNamespace(body='## 本质\n已有卡。\n\n## 命题\n- P1. Old fact one.\n\n## 直觉与易混对比\n- 直觉：路灯。\n\n'
                           '## 相关\n暂无。\n\n## 复习卡\nOld Q?::Old A.\n')


def test_f3_patch_fits_keeps_two_review_cards_whose_answers_differ():
    problem, add, add_f = ingest._patch_fits(OLD, {'propositions': [], 'flashcards': [{'q': Q, 'a': A1},
                                                                                       {'q': Q, 'a': A2}]})
    assert problem is None and add == [] and add_f == [{'q': Q, 'a': A1}, {'q': Q, 'a': A2}]
    # against the card's own review card: another answer stays, the same pair is a repeat
    assert ingest._patch_fits(OLD, {'flashcards': [{'q': 'Old Q?', 'a': 'New A.'}]})[2] == [{'q': 'Old Q?',
                                                                                               'a': 'New A.'}]
    assert ingest._patch_fits(OLD, {'flashcards': [{'q': 'Old  Q?', 'a': 'Old A. '}]})[2] == []
    # within the patch: the same pair, spaces apart, is added once
    assert ingest._patch_fits(OLD, {'flashcards': [{'q': Q, 'a': A1}, {'q': f' {Q}', 'a': A1}]})[2] == [{'q': Q, 'a': A1}]


def test_repeat_is_the_one_rule_a_proposition_by_its_words_a_review_card_by_question_and_answer():
    assert source_check.repeat('Items keep  arrival order.', ' Items keep arrival order.')
    assert not source_check.repeat('Items keep arrival order.', 'Items keep arrival order')  # a mark: asked again
    assert source_check.repeat({'q': Q, 'a': A1}, {'q': f'{Q} ', 'a': A1})
    assert not source_check.repeat({'q': Q, 'a': A1}, {'q': Q, 'a': A2})
    assert not source_check.repeat(Q, {'q': Q, 'a': A1})


# --- P-F1 F2: a patch against the existing card's own question ---------------------------------------------------
def old_question_producer(task):
    out = copy.deepcopy(two_chunk_producer(task))
    for c in out.get('cards') or []:
        if c['slug'] == 'lantern-queue':
            c['flashcards'] = [{'q': 'Old question 1?', 'a': 'A different answer about lanterns.'}] + \
                list(c['flashcards'])[1:]
    return out


def test_f2_a_patch_asking_the_cards_question_with_another_answer_names_both_and_may_use_differs(tmp_path):
    root, job_id, _ = job(tmp_path)
    checks = []

    def check(p):
        answer = keep_all(p)
        if len(checks) > 1:  # asked again: the merge check says why both answers are needed
            answer['patches'][0]['differs'] = [{'text': 'Old question 1?', 'from': 'Q1',
                                                'reason': 'the new answer is about lanterns'}]
        return answer
    done = run(root, job_id, reviewer(checks, check=check), with_diagrams(old_question_producer))
    [refusal] = [r['message'] for r in done['source_check_refusals']]
    assert "'Old answer 1.' and 'A different answer about lanterns.'" in refusal and 'differs' in refusal
    assert done['status'] == 'applied' and done['patch_set'] == {'lantern-queue': 'patched'}
    old = cardfmt.flashcards_of(card_of(root, EXISTING).body)
    assert old[0] == {'q': 'Old question 1?', 'a': 'Old answer 1.'}
    assert {'q': 'Old question 1?', 'a': 'A different answer about lanterns.'} in old[2:]


def test_a_patch_review_card_that_is_the_cards_own_word_for_word_still_goes_back(tmp_path):
    root, job_id, _ = job(tmp_path)

    def produce(task):
        out = copy.deepcopy(two_chunk_producer(task))
        for c in out.get('cards') or []:
            if c['slug'] == 'lantern-queue':
                c['flashcards'] = [{'q': 'Old question 1?', 'a': 'Old answer 1.'}] + list(c['flashcards'])[1:]
        return out
    done = run(root, job_id, reviewer([]), with_diagrams(produce))
    assert any('answer and all: leave it out' in r['message'] for r in done['source_check_refusals'])


# --- the check before writing: a fault in the one rule stops the job, nothing is lost ---------------------------
def test_with_the_rule_broken_to_always_same_the_check_stops_the_write_of_a_patch(tmp_path, monkeypatch):
    root, job_id, before = job(tmp_path)
    cards = set(read_snapshot(root).cards)
    monkeypatch.setattr(source_check, 'repeat', lambda a, b: True)
    with pytest.raises(ingest.IngestError, match=r"card_exist would not hold what the merge check's patch gives it: "
                                                 r"proposition 'Items stay in arrival order\.'"):
        run(root, job_id, reviewer([]))
    assert unchanged(root, before) and set(read_snapshot(root).cards) == cards  # nothing was written


def test_with_the_rule_broken_to_always_same_the_check_stops_a_carry_into_a_card_of_the_source(tmp_path,
                                                                                                monkeypatch):
    root, job_id, _ = job(tmp_path)
    arrival = CARDS['queue-order']

    def check(p):
        mine = next(c for c in p['cards'] if c['slug'] == 'arrival-order')
        return keep_all(p, **{'arrival-order': {'overlap': 'extends', 'with': 'lit-marker-cost', 'reason': 'r',
                                                'carry': {'propositions': list(mine['propositions']),
                                                          'flashcards': list(mine['flashcards'])}}})
    assert arrival['propositions']
    cards = set(read_snapshot(root).cards)
    monkeypatch.setattr(source_check, 'repeat', lambda a, b: True)
    with pytest.raises(Conflict, match=r'arrival-order carries into lit-marker-cost what lit-marker-cost would not '
                                       r'hold once merged: proposition'):
        run(root, job_id, reviewer([], check=check))
    assert set(read_snapshot(root).cards) == cards  # nothing was written


def test_lost_names_each_item_a_card_does_not_hold():
    props, flashcards = ['A.', 'B  is b.'], [{'q': Q, 'a': A1}]
    assert source_check.lost(props, flashcards, {'propositions': ['B is b.'], 'flashcards': [{'q': Q, 'a': A1}],
                                                 'replace': [{'proposition': 'P1', 'by': 'A.'}]}) == []
    assert source_check.lost(props, flashcards, {'propositions': ['C.'], 'flashcards': [{'q': Q, 'a': A2}],
                                                 'replace': [{'proposition': 'P1', 'by': 'A!'}]}) == [
        "proposition 'C.'", "proposition 'A!'", f'review card {Q!r} ({A2!r})']

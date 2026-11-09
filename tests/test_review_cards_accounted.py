"""Every review card of a card that goes into another is accounted for, as every proposition is.

The fifth verdict found that the merge check had to account for each proposition of a card that goes into another
(carried, used in a replacement, or named in ``already``) but not for its review cards: an answer that carried every
proposition and left a review card out was taken, the job was ``applied``, and the review card was nowhere, in the
vault or in the job result (probe p8, on a patch and on a carry into a card of the source; in earlier real runs 137 of
645 review cards of patch-set cards were neither carried nor close to any question). Now each review card is
carried, swapped in (a ``flashcard_replace`` that asks its question), or named in ``already`` with the review card of
the target that asks it (``{"text": <its question>, "as": "Q<n>"}``); an answer that leaves one out goes back. With the
check before writing (every item of a patch is in the card), no item of a card is left behind without a word.

The source is the invented two-chunk source of tests/test_invariants.py.
"""
from __future__ import annotations

import pytest

from secondbrain import cards as cardfmt
from secondbrain.pipelines import source_check
from secondbrain.vault import read_snapshot
from tests.helpers import EXISTING
from tests.ingest_helpers import CARDS, keep_all
from tests.test_patch_set import card_of, job, reviewer, run

LANTERN, LIT = CARDS['lantern-queue'], CARDS['lit-marker-cost']
DROPPED = LANTERN['flashcards'][1]  # 'How does a reader skip ahead?'


def vault_pairs(root):
    return [(f['q'], f['a']) for c in read_snapshot(root).cards.values() for f in cardfmt.flashcards_of(c.body) or []]


def frozen_packet(tmp_path):
    root, job_id, _ = job(tmp_path)
    checks = []
    run(root, job_id, reviewer(checks))
    return checks[0].packet


def leaves_one_out(packet):
    """The probe's answer: every proposition of lantern-queue, and only its first review card."""
    return keep_all(packet, _patches={EXISTING: {'propositions': list(LANTERN['propositions']),
                                                 'flashcards': [LANTERN['flashcards'][0]]}})


# --- a patch into an existing card -------------------------------------------------------------------------------
def test_a_patch_that_leaves_a_review_card_out_goes_back_and_the_card_gets_it(tmp_path):
    root, job_id, _ = job(tmp_path)
    checks = []

    def check(packet):  # first the probe's answer, then one that carries every review card
        return leaves_one_out(packet) if len(checks) == 1 else keep_all(packet)
    done = run(root, job_id, reviewer(checks, check=check))
    assert done['status'] == 'applied', done.get('pending')
    [refusal] = done['source_check_refusals']
    assert f"patches[0] leaves out the review card {DROPPED['q']!r}" in refusal['message']
    assert (DROPPED['q'], DROPPED['a']) in vault_pairs(root)


def test_a_review_card_the_existing_card_asks_already_is_named_not_carried(tmp_path):
    frozen = frozen_packet(tmp_path)
    named = {'propositions': list(LANTERN['propositions']), 'flashcards': [LANTERN['flashcards'][0]]}
    for already, problem in (([{'text': DROPPED['q'], 'as': 'Q2'}], None),
                             ([{'text': DROPPED['q'], 'as': 'Q9'}], r'as must name the review card of card_exist '
                                                                    r'that asks it \(Q1–Q2\)'),
                             ([{'text': 'Something else?', 'as': 'Q1'}], r'must be the question of one of the review '
                                                                         r'cards it would carry'),
                             ([], r'leaves out the review card')):
        answer = keep_all(frozen, _patches={EXISTING: {**named, 'already': already}})
        if problem is None:
            assert source_check.validate_answer(frozen, answer) is answer
        else:
            with pytest.raises(source_check.Refused, match=problem):
                source_check.validate_answer(frozen, answer)


def test_a_review_card_swapped_in_is_accounted_for(tmp_path):
    frozen = frozen_packet(tmp_path)
    swapped_in = {'flashcard': 'Q2', 'q': DROPPED['q'], 'a': 'By jumping to a lit marker (Old answer 2).'}
    answer = keep_all(frozen, _patches={EXISTING: {'propositions': list(LANTERN['propositions']),
                                                   'flashcards': [LANTERN['flashcards'][0]],
                                                   'flashcard_replace': [swapped_in]}})
    assert source_check.validate_answer(frozen, answer) is answer


# --- a carry into a card of the source -----------------------------------------------------------------------------
def test_a_carry_that_leaves_a_review_card_out_goes_back_and_the_card_gets_it(tmp_path):
    root, job_id, _ = job(tmp_path)
    levels = {'lantern-queue': {'overlap': 'distinct'}, 'queue-order': {'overlap': 'same', 'with': [EXISTING]}}
    checks, dropped = [], LIT['flashcards'][2]  # 'What does one marker cost?'

    def check(packet):
        lit = next(c for c in packet['cards'] if c['slug'] == 'lit-marker-cost')
        flashcards = list(lit['flashcards'][:2]) if len(checks) == 1 else list(lit['flashcards'])
        return keep_all(packet, **{'lit-marker-cost': {'overlap': 'extends', 'with': ['arrival-order'], 'reason': 'adds',
                                                       'carry': {'propositions': list(lit['propositions']),
                                                                 'flashcards': flashcards}}})
    done = run(root, job_id, reviewer(checks, check=check, levels=levels))
    assert done['status'] == 'applied', done.get('pending')
    [refusal] = done['source_check_refusals']
    assert f"cards: lit-marker-cost.carry leaves out the review card {dropped['q']!r}" in refusal['message']
    assert (dropped['q'], dropped['a']) in vault_pairs(root)
    assert done['outcomes']['lit-marker-cost'] == 'merge_carry'

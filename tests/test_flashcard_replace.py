"""A review card of the card a patch or carry goes into may be swapped for a fuller or clearer one (decision D-26).

Before, an existing card's review cards were never changed: the merge check could add review cards to it, not
improve one. Now a patch (``patches[]``) or a carry into a card of the source may hold ``flashcard_replace``:
``{"flashcard": "Q<n>", "q": ..., "a": ...}`` swaps that card's review card n for the new pair. It follows the rules
of a proposition's ``replace``: each review card once, a ``Q<n>`` the card has, the new pair never one the card (or
the patch) holds already, a close one sent back unless ``differs`` keeps it, no more review cards than before; the
check before writing (``source_check.lost``) looks for the new pair; the job result keeps the words before and after.

The review schedule is kept by card, not by question, so a card whose question changes keeps its progress.

The source is the invented two-chunk source of tests/test_invariants.py; the existing card has two review cards,
``Old question 1?`` and ``Old question 2?``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from secondbrain import cards as cardfmt
from secondbrain import review_registry as rr
from secondbrain.pipelines import ingest, source_check
from secondbrain.store import Conflict
from secondbrain.vault import read_snapshot
from tests.helpers import EXISTING
from tests.ingest_helpers import CARDS, keep_all
from tests.test_invariants import ARRIVAL
from tests.test_patch_set import card_of, job, reviewer, run, unchanged

LANTERN = CARDS['lantern-queue']
MARKERS = CARDS['lit-marker-cost']
NEW = {'q': 'Which way does a reader take past old items?',
       'a': 'Old answer 1, or a jump ahead to the next lit marker.'}
SWAP = {'flashcard': 'Q1', **NEW}


def with_swap(packet, *swaps, **extra):
    """The default patch for the existing card (every proposition and review card of lantern-queue) with ``swaps``."""
    return keep_all(packet, _patches={EXISTING: {'propositions': list(LANTERN['propositions']),
                                                 'flashcards': list(LANTERN['flashcards']),
                                                 'flashcard_replace': list(swaps), **extra}})


def frozen_packet(tmp_path):
    root, job_id, _ = job(tmp_path)
    checks = []
    run(root, job_id, reviewer(checks))
    return checks[0].packet


# --- the patch into an existing card --------------------------------------------------------------------------------
def test_a_patch_swaps_a_review_card_of_the_existing_card_and_the_result_keeps_both_wordings(tmp_path):
    root, job_id, before = job(tmp_path)
    done = run(root, job_id, reviewer(check=lambda p: with_swap(p, SWAP)))
    assert done['status'] == 'applied', done.get('pending')
    flash = cardfmt.flashcards_of(card_of(root, EXISTING).body)
    assert flash == [NEW, {'q': 'Old question 2?', 'a': 'Old answer 2.'}] + LANTERN['flashcards']  # no card more
    assert '\n' + NEW['q'] + '::' + NEW['a'] + '\n' in card_of(root, EXISTING).body
    [merged] = done['merged_into_existing']
    assert merged['replaced'] == ['Q1'] and merged['flashcards'] == 2
    assert merged['replacements'] == [{'flashcard': 'Q1', 'before': {'q': 'Old question 1?', 'a': 'Old answer 1.'},
                                       'after': NEW}]
    assert done['source_check']['status'] == 'checked' and done['replacements_not_made'] == []
    assert cardfmt.propositions_of(card_of(root, EXISTING).body)[2:] == LANTERN['propositions']


def test_a_proposition_swap_is_listed_with_its_words_before_and_after_as_a_review_card_swap_is(tmp_path):
    root, job_id, _ = job(tmp_path)
    fuller = LANTERN['propositions'][0]
    done = run(root, job_id, reviewer(check=lambda p: with_swap(
        p, SWAP, propositions=LANTERN['propositions'][1:], replace=[{'proposition': 'P2', 'by': fuller}])))
    assert done['status'] == 'applied', done.get('pending')
    [merged] = done['merged_into_existing']
    assert merged['replaced'] == ['P2', 'Q1']
    assert merged['replacements'][0] == {'proposition': 'P2', 'before': 'Old proposition 2 about routing readers.',
                                         'after': fuller}
    assert merged['replacements'][1]['flashcard'] == 'Q1'


@pytest.mark.parametrize('swaps, extra, problem', [
    ([SWAP, {**SWAP, 'q': 'Which other way is there past old items?'}], {}, r'card_exist Q1 is replaced twice'),
    ([{**SWAP, 'flashcard': 'Q3'}], {}, r'flashcard must name one of the review cards of card_exist \(Q1–Q2\)'),
    ([{**SWAP, 'flashcard': 'P1'}], {}, r'flashcard must name one of the review cards of card_exist \(Q1–Q2\)'),
    ([{**SWAP, 'q': 'Old question 2?', 'a': 'Old answer 2.'}], {},
     r'the new pair is card_exist Q2, question and answer: the card would hold it twice'),
    ([{**SWAP, 'q': 'Old question 2?'}], {},
     r"'Old question 2\?' asks what card_exist Q2 asks, but their answers differ"),
    ([{**SWAP, 'q': 'Old question 1?', 'a': 'Old answer 1.'}], {}, r'is card_exist Q1 as it stands: leave it out'),
    ([{**SWAP, 'q': 'Which way? :: Old'}], {}, r'must not contain "::"'),
    ([{**SWAP, 'a': ' '}], {}, r'give the new question and answer in q and a'),
    ([{**SWAP, 'q': LANTERN['flashcards'][0]['q']}], {},
     r"'What order does a lantern queue keep\?' asks what a review card it carries too asks, but their answers "
     r"differ \('Arrival order\.' and"),
    ([SWAP, {**SWAP, 'flashcard': 'Q2'}], {}, r'the new pair is another new pair of this patch'),
])
def test_a_review_card_swap_out_of_form_or_said_twice_is_refused(tmp_path, swaps, extra, problem):
    frozen = frozen_packet(tmp_path)
    source_check.validate_answer(frozen, with_swap(frozen, SWAP))  # the swap itself passes
    with pytest.raises(source_check.Refused, match=problem):
        source_check.validate_answer(frozen, with_swap(frozen, *swaps, **extra))


def test_a_close_question_goes_back_unless_differs_says_why_it_asks_something_else(tmp_path):
    frozen = frozen_packet(tmp_path)
    close = {**SWAP, 'q': 'Old question 2, put again?'}
    assert source_check.near_same(close['q'], 'Old question 2?') or source_check.same_text(close['q'], 'Old question 2?')
    with pytest.raises(source_check.Refused, match=r"may ask again what card_exist Q2 asks|looks the same as card_exist Q2"):
        source_check.validate_answer(frozen, with_swap(frozen, close))
    kept = with_swap(frozen, close, differs=[{'text': close['q'], 'from': 'Q2', 'reason': 'it asks about the cut'}])
    assert source_check.validate_answer(frozen, kept) is kept


def test_a_swap_only_patch_is_not_empty_and_adds_no_review_card(tmp_path):
    """The card says both of lantern-queue's points and asks its second question; its first question goes in by a
    swap of Q1 (the fuller answer keeps the old one's fact). Every item is accounted for, and nothing is added."""
    root, job_id, _ = job(tmp_path)
    path = next((root / '10_Knowledge').rglob(f'{EXISTING}.md'))
    second = LANTERN['flashcards'][1]
    path.write_bytes(path.read_bytes().replace(b'Old proposition 1 about routing readers.', LANTERN['propositions'][0]
                                               .encode()).replace(b'Old proposition 2 about routing readers.',
                                                                  LANTERN['propositions'][1].encode())
                     .replace(b'Old question 2?::Old answer 2.', f"{second['q']}::{second['a']}".encode()))
    swap_in = {'flashcard': 'Q1', 'q': LANTERN['flashcards'][0]['q'], 'a': 'Arrival order (Old answer 1).'}

    def check(p):
        return keep_all(p, _patches={EXISTING: {'flashcard_replace': [swap_in], 'already': [
            {'text': LANTERN['propositions'][0], 'as': 'P1'}, {'text': LANTERN['propositions'][1], 'as': 'P2'},
            {'text': second['q'], 'as': 'Q2'}]}})
    done = run(root, job_id, reviewer(check=check))
    assert done['status'] == 'applied', done.get('pending')
    assert cardfmt.flashcards_of(card_of(root, EXISTING).body) == [{k: swap_in[k] for k in ('q', 'a')}, second]
    assert done['merged_into_existing'][0]['replaced'] == ['Q1']


def test_a_patch_split_into_narrower_cards_makes_no_swap_and_lists_it(tmp_path):
    """Past 12 propositions the existing card takes no patch (it stays as it is): the swap is listed, not made."""
    root, job_id, before = job(tmp_path, n_props=11)
    done = run(root, job_id, reviewer(check=lambda p: with_swap(p, SWAP)))
    assert done['status'] == 'applied', done.get('pending')
    assert done['patch_set'] == {'lantern-queue': 'split'}
    assert unchanged(root, before)
    [row] = done['replacements_not_made']
    assert row['into'] == EXISTING and row['flashcard'] == 'Q1' and row['q'] == NEW['q'] and 'narrower' in row['reason']


# --- the check before writing sees the new pair -----------------------------------------------------------------------
def test_lost_looks_for_the_new_pair_of_a_swap():
    old, new = {'q': 'Q old?', 'a': 'A old.'}, {'q': 'Q new?', 'a': 'A new.'}
    patch = {'flashcard_replace': [{'flashcard': 'Q1', **new}]}
    assert source_check.lost([], [new], patch) == []
    assert source_check.lost([], [old], patch) == ["review card 'Q new?' ('A new.'), which replaces Q1"]


def test_with_the_swap_dropped_on_the_way_to_the_card_the_check_stops_the_write(tmp_path, monkeypatch):
    """The break experiment: the card file is written without the swap. The check before writing stops the job."""
    root, job_id, before = job(tmp_path)
    cards = set(read_snapshot(root).cards)
    real = cardfmt.patch

    def drops_the_swap(raw, **kw):
        return real(raw, **{**kw, 'replace_flashcards': None})
    monkeypatch.setattr(cardfmt, 'patch', drops_the_swap)
    with pytest.raises(ingest.IngestError, match=r"card_exist would not hold what the merge check's patch gives it: "
                                                 r"review card 'Which way does a reader take past old items\?'"):
        run(root, job_id, reviewer(check=lambda p: with_swap(p, SWAP)))
    assert unchanged(root, before) and set(read_snapshot(root).cards) == cards  # nothing was written


# --- a carry into a card of the source ------------------------------------------------------------------------------
MARKER_SWAP = {'flashcard': 'Q1', 'q': 'How often does a writer light a marker?',
               'a': 'Every eight items, whatever order the items arrived in.'}


def carry_check(*swaps):
    def check(p):
        return keep_all(p, **{'arrival-order': {'overlap': 'extends', 'with': 'lit-marker-cost', 'reason': 'r',
                                                'carry': {'propositions': list(ARRIVAL['propositions']),
                                                          'flashcards': list(ARRIVAL['flashcards']),
                                                          'flashcard_replace': list(swaps)}}})
    return check


def test_a_carry_swaps_a_review_card_of_the_card_of_the_source_it_goes_into(tmp_path):
    root, job_id, _ = job(tmp_path)
    done = run(root, job_id, reviewer(check=carry_check(MARKER_SWAP)))
    assert done['status'] == 'applied', done.get('pending')
    written = {c.title: c for c in read_snapshot(root).cards.values()}[MARKERS['title']]
    new = {k: MARKER_SWAP[k] for k in ('q', 'a')}
    assert cardfmt.flashcards_of(written.body) == [new] + MARKERS['flashcards'][1:] + ARRIVAL['flashcards']
    [merged] = done['source_check']['merged']
    assert merged['slug'] == 'arrival-order' and merged['replaced'] == 1
    assert merged['replacements'] == [{'flashcard': 'Q1', 'before': MARKERS['flashcards'][0], 'after': new}]


def test_a_carry_swap_said_twice_or_into_a_card_of_the_patch_set_is_refused(tmp_path):
    frozen = frozen_packet(tmp_path)
    source_check.validate_answer(frozen, carry_check(MARKER_SWAP)(frozen))
    twice = carry_check(MARKER_SWAP, {**MARKER_SWAP, 'q': 'How often is a lit marker placed?'})(frozen)
    with pytest.raises(source_check.Refused, match=r'lit-marker-cost Q1 is replaced twice'):
        source_check.validate_answer(frozen, twice)
    again = carry_check({**MARKER_SWAP, 'q': MARKERS['flashcards'][1]['q'], 'a': MARKERS['flashcards'][1]['a']})(frozen)
    with pytest.raises(source_check.Refused, match=r'the new pair is lit-marker-cost Q2, question and answer'):
        source_check.validate_answer(frozen, again)
    into_patch_set = keep_all(frozen, **{'arrival-order': {
        'overlap': 'extends', 'with': 'lantern-queue', 'reason': 'r',
        'carry': {'propositions': list(ARRIVAL['propositions']), 'flashcard_replace': [SWAP]}}})
    with pytest.raises(source_check.Refused, match=r'lantern-queue goes into the existing card card_exist; replace one '
                                                   r'of its review cards in the patch for it'):
        source_check.validate_answer(frozen, into_patch_set)


def test_with_the_swap_dropped_in_a_carry_the_check_stops_the_write(tmp_path, monkeypatch):
    root, job_id, _ = job(tmp_path)
    cards = set(read_snapshot(root).cards)
    monkeypatch.setattr(source_check, 'swap_flashcards', lambda flashcards, swaps: (list(flashcards), []))
    with pytest.raises(Conflict, match=r"arrival-order carries into lit-marker-cost what lit-marker-cost would not "
                                       r"hold once merged: review card 'How often does a writer light a marker\?'"):
        run(root, job_id, reviewer(check=carry_check(MARKER_SWAP)))
    assert set(read_snapshot(root).cards) == cards  # nothing was written


# --- the review schedule is kept by card --------------------------------------------------------------------------
def test_a_card_whose_question_changed_keeps_its_review_schedule(tmp_path):
    """One review before the swap and one after: replayed, the card's state goes on from the first (reps, due), the
    same as if the question had never changed. Only the per-question hash in the event differs."""
    before = '## 复习卡\nOld question 1?::Old answer 1.\nOld question 2?::Old answer 2.\n'
    after = before.replace('Old question 1?::Old answer 1.', f'{NEW["q"]}::{NEW["a"]}')
    [old_item, _] = rr.extract_review_items(before)
    [new_item, _] = rr.extract_review_items(after)
    assert old_item.q != new_item.q  # the question's hash changed
    t0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)

    def replay(first, second):
        logs = tmp_path / ('kept' if first.q == second.q else 'swapped')
        rr.append_event(logs, rr.make_event('card_exist', device='cli-1', now=t0, items=[{'q': first.q, 'rating': 3}]))
        mid = rr.load(logs).states['card_exist']
        rr.append_event(logs, rr.make_event('card_exist', device='cli-1', now=mid.due + timedelta(hours=1),
                                            items=[{'q': second.q, 'rating': 3}]))
        return mid, rr.load(logs).states['card_exist']
    mid, swapped = replay(old_item, new_item)
    _, kept = replay(old_item, old_item)
    assert mid.reps == 1 and swapped.reps == 2 and swapped.due > mid.due
    assert swapped.as_dict() == kept.as_dict()


def test_a_swap_of_a_review_card_the_card_no_longer_has_is_refused_never_put_elsewhere(tmp_path):
    """The merge check saw the card with two review cards; if it has fewer when the patch is written, the swap is
    refused by name (number 0 or past the end never lands on another review card), and the card takes no patch."""
    root, _, _ = job(tmp_path)
    old = card_of(root, EXISTING)
    for ref in ('Q3', 'Q0', 'P1'):
        with pytest.raises(ValueError, match=r'a replaced review card must be one of Q1-Q2'):
            source_check.swap_flashcards(cardfmt.flashcards_of(old.body), [{**SWAP, 'flashcard': ref}])
    problem, add, add_f = ingest._patch_fits(old, {'flashcards': [NEW], 'flashcard_replace': [{**SWAP, 'flashcard': 'Q3'}]})
    assert 'a replaced review card must be one of Q1-Q2' in problem and add == add_f == []

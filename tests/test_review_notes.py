"""A reviewer's notes have a kind, and an accept with a note of a blocking kind goes back (decision D-18 3).

H8 and H4: reviewers wrote the real problem in a note and still accepted the card, and the wrong card was written.
Now each note is `fact`, `unreadable`, `quote`, `not_a_unit` or `minor`; the program turns an accept with a note of
one of the first four kinds into a revise, the note its reason, which spends a content repair round; when the rounds
are used up the card is listed for the owner. Only `minor` notes leave the decision as it is.
"""
from __future__ import annotations

import copy
import re

import pytest

from secondbrain import jobs, review_notes
from secondbrain.export import pipeline as export
from secondbrain.pipelines import split
from secondbrain.runner import prompt
from tests.ingest_helpers import first_candidate, first_review, repair_candidate, repair_review
from tests.test_review_rules import run, with_rows, written

WRONG = {'category': 'fact', 'text': 'P2 says markers are deleted; the chunk says dimmed'}


def accepted_with(*notes, review=None):
    """``review`` (the first review) with marker-dimming accepted, carrying ``notes``."""
    return with_rows(review or first_review(), **{'marker-dimming': {
        'decision': 'accept', 'overlap': 'distinct', 'reason': 'faithful', 'notes': list(notes)}})


def again(*notes):
    """A repair-round review that accepts marker-dimming with ``notes``."""
    return {'cards': [{'slug': 'marker-dimming', 'decision': 'accept', 'overlap': 'distinct', 'reason': 'fixed',
                       'notes': list(notes)}],
            'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'covered with earlier round'}}


def test_an_accepted_card_with_a_fact_note_goes_back_and_spends_a_repair_round(tmp_path):
    root, job_id, done, seen = run(tmp_path, [first_candidate(), repair_candidate()],
                                   [accepted_with(WRONG), repair_review()])
    assert done['status'] == 'applied', done.get('pending')
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'c01.r1']           # a content repair round was spent
    [finding] = seen['reviewer'][1].packet['previous_findings']['cards']      # the note is the reason it went back
    assert finding['slug'] == 'marker-dimming' and finding['blocking'] == 'fact'
    assert finding['reason'] == f"the review noted a fact problem: {WRONG['text']}"
    assert done['notes_turned'] == [{'chunk': 'c01', 'slot': 'c01', 'round': 0, 'slug': 'marker-dimming',
                                     'category': 'fact', 'note': WRONG['text']}]
    stored = jobs.artifact(root, jobs.load(root, job_id), 'review', 'c01')
    row = next(r for r in stored['cards'] if r['slug'] == 'marker-dimming')
    assert row['decision'] == 'revise' and row['turned'] == {'from': 'accept', 'reason': 'faithful', 'note': WRONG}
    entries = jobs.load(root, job_id)['decisions']                            # the decision record keeps the turn
    first = next(e for e in entries if e.get('value') == 'verdict' and e.get('object') == 'marker-dimming')
    assert first['data']['decision'] == 'revise' and first['data']['turned']['note'] == WRONG
    assert written(root, repair_candidate()['cards'][0]['title'])             # the fixed card is written


@pytest.mark.parametrize('category', ['unreadable', 'quote', 'not_a_unit'])
def test_every_blocking_kind_sends_an_accepted_card_back(tmp_path, category):
    note = {'category': category, 'text': 'what is wrong, and where'}
    root, job_id, done, seen = run(tmp_path, [first_candidate(), repair_candidate()],
                                   [accepted_with(note), repair_review()])
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'c01.r1']
    assert [t['category'] for t in done['notes_turned']] == [category]


def test_when_the_rounds_are_used_up_a_card_still_noted_wrong_is_listed_for_the_owner(tmp_path):
    root, job_id, done, seen = run(tmp_path, [first_candidate(), repair_candidate(), repair_candidate()],
                                   [accepted_with(WRONG), again(WRONG), again(WRONG)])
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'c01.r1', 'c01.r2']  # max_repair_rounds = 2
    assert done['status'] == 'partial'
    [pending] = [p for p in done['pending'] if p.get('slug') == 'marker-dimming']
    assert pending['reason'] == f"the review noted a fact problem: {WRONG['text']}"
    assert [(t['slot'], t['round']) for t in done['notes_turned']] == [('c01', 0), ('c01.r1', 1), ('c01.r2', 2)]
    with pytest.raises(StopIteration):
        written(root, repair_candidate()['cards'][0]['title'])


def test_a_card_with_only_minor_notes_is_accepted_and_its_notes_are_kept_for_the_owner(tmp_path):
    minor = {'category': 'minor', 'text': 'the essence could be shorter'}
    review = with_rows(first_review(), **{'lantern-queue': {'notes': [minor]}})
    root, job_id, done, seen = run(tmp_path, [first_candidate(), repair_candidate()], [review, repair_review()])
    assert done['status'] == 'applied' and done['notes_turned'] == []
    assert {'chunk': 'c01', 'slot': 'c01', 'slug': 'lantern-queue', 'category': 'minor',
            'note': minor['text']} in done['review_notes']
    assert jobs.load(root, job_id)['review_notes'] == done['review_notes']  # what `sb job show` prints
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'c01.r1']           # the round is marker-dimming's


def test_notes_in_the_old_form_are_refused_by_name_and_asked_again(tmp_path):
    old = with_rows(first_review(), **{'lantern-queue': {'notes': ['the essence could be shorter']}})
    root, job_id, done, seen = run(tmp_path, [first_candidate(), repair_candidate()],
                                   [old, first_review(), repair_review()])
    assert done['status'] == 'applied', done.get('pending')
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'c01', 'c01.r1']    # a format retry, not a round
    assert 'cards[0].notes[0] must be an object, not str' in seen['reviewer'][1].prompt
    for bad, says in (([{'category': 'serious', 'text': 't'}], 'notes[0].category must be one of'),
                      ([{'text': 't'}], 'notes[0].category must be one of'),
                      ([{'category': 'fact', 'text': ' '}], 'notes[0].text must say'),
                      ('a note', 'notes must be a list')):
        with pytest.raises(ValueError, match=re.escape(says)):
            from secondbrain.pipelines import ingest
            state, packet = jobs.load(root, job_id), jobs.read_input(root, job_id)
            ingest.validate_submission(packet, with_rows(first_review(), **{'lantern-queue': {'notes': bad}}),
                                       role='reviewer', candidate=first_candidate(), root=root, chunk='c01',
                                       state=state)


# --- the split reviewer and the export reviewer take the same rule -------------------------------------------------
def test_a_split_review_that_accepts_with_a_blocking_note_is_a_revise():
    note = {'category': 'quote', 'text': 'the second source quote is not in the chunk'}
    out = split.validate_review({'decision': 'accept', 'restates': False, 'reason': 'ok', 'notes': [note]})
    assert out['decision'] == 'revise' and out['blocking'] == 'quote' and out['turned']['note'] == note
    minor = {'decision': 'accept', 'restates': False, 'reason': 'ok', 'notes': [{'category': 'minor', 'text': 't'}]}
    assert split.validate_review(copy.deepcopy(minor)) == minor
    with pytest.raises(ValueError, match=r'notes\[0\] must be an object, not str'):
        split.validate_review({'decision': 'accept', 'restates': False, 'reason': 'ok', 'notes': ['t']})
    with pytest.raises(ValueError, match=r'notes\[0\].category must be one of'):
        split.validate_review({'decision': 'accept', 'restates': False, 'reason': 'ok', 'notes': [{'text': 't'}]})


def test_a_split_card_accepted_with_a_fact_note_each_round_stays_pending(tmp_path):
    from tests.test_patch_set import job, producer, reviewer, run as run_job, unchanged
    root, job_id, before = job(tmp_path, n_props=11)
    seen = []
    done = run_job(root, job_id, reviewer(split_review=lambda p: {
        'decision': 'accept', 'restates': False, 'reason': 'ok', 'notes': [WRONG]}), producer(seen=seen))
    assert [t.chunk for t in seen] == ['sp01', 'sp01.r1', 'sp01.r2']
    assert done['status'] == 'partial' and [p['slug'] for p in done['pending']] == ['lantern-queue']
    assert [(t['slot'], t['category']) for t in done['notes_turned']] == [
        ('sp01', 'fact'), ('sp01.r1', 'fact'), ('sp01.r2', 'fact')]
    assert unchanged(root, before)


def test_an_export_paragraph_accepted_with_a_blocking_note_goes_back():
    note = {'category': 'fact', 'text': 'the paragraph says 30%; the card says 20%'}
    out = export._validate_review({}, {'items': [
        {'id': 'p1.1', 'decision': 'accept', 'reason': 'ok', 'notes': [note]},
        {'id': 'p1.2', 'decision': 'accept', 'reason': 'ok', 'notes': [{'category': 'minor', 'text': 'long'}]}]},
        ['p1.1', 'p1.2'])
    assert [x['decision'] for x in out['items']] == ['revise', 'accept']
    assert out['items'][0]['reason'] == f"the review noted a fact problem: {note['text']}"
    with pytest.raises(ValueError, match=r'items\[0\].notes\[0\] must be an object, not str'):
        export._validate_review({}, {'items': [{'id': 'p1.1', 'decision': 'accept', 'reason': 'ok',
                                                'notes': ['long']}]}, ['p1.1'])
    with pytest.raises(ValueError, match=r'item p1.1.notes\[0\].category must be one of'):
        export._validate_review({}, {'items': [{'id': 'p1.1', 'decision': 'accept', 'reason': 'ok',
                                                'notes': [{'category': 'style', 'text': 'long'}]}]}, ['p1.1'])


def test_every_reviewer_prompt_that_asks_for_notes_has_the_one_definition():
    """R-07: one definition, the same words in each prompt."""
    for name in ('review_cards.md', 'review_split.md', 'export_review.md', 'export_smooth_review.md'):
        text = prompt(name)
        assert review_notes.definition() in text and review_notes.PLACEHOLDER not in text, name
    assert all(f'`{k}`' in review_notes.definition() for k in review_notes.CATEGORIES)
    assert 'minor' in prompt('review_focused.md') and review_notes.BLOCKING == ('fact', 'unreadable', 'quote',
                                                                               'not_a_unit')

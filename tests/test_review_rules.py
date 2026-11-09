"""The review rules of commit 6: two budgets, mechanical fixes (``amend``) and the focused repair-round review.

These tests run with the defaults: two repair rounds, two format retries per slot, focused review and amend on.
"""
from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from secondbrain import config, decisions, jobs
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import amend as amends
from secondbrain.pipelines import ingest
from secondbrain.vault import read_snapshot
from tests.helpers import EXISTING, PILLAR, TOPIC
from tests.ingest_helpers import (CARDS, SOURCE_NAME, build_ingest_vault, first_candidate, first_review,
                                  repair_candidate, repair_review, scripted)


def run(tmp_path, producer_outputs, reviewer_outputs, **review_fields):
    root = build_ingest_vault(tmp_path / 'vault')
    if review_fields:
        current = config.current()
        config.activate(replace(current, review=replace(current.review, **review_fields)))
    produce, respond, seen = scripted(producer_outputs, reviewer_outputs)
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    done = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=respond))
    return root, job['job_id'], done, seen


def with_rows(review, **rows):
    """``review`` with the card rows of ``rows`` (slug -> fields) changed."""
    out = copy.deepcopy(review)
    for row in out['cards']:
        row.update(rows.get(row['slug'], {}))
    return out


def written(root, title):
    return next(c for c in read_snapshot(root).cards.values() if c.title == title)


def still_wrong(reason='still says deleted'):
    return {'cards': [{'slug': 'marker-dimming', 'decision': 'revise', 'reason': reason,
                       'fixed': [{'finding': 'dimmed, not deleted', 'fixed': False}]}],
            'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'covered with earlier round'}}


# --- two budgets ----------------------------------------------------------------------------------------------------
def test_two_repair_rounds_by_default_each_judged_on_the_earlier_findings(tmp_path):
    root, job_id, done, seen = run(tmp_path, [first_candidate(), repair_candidate(), repair_candidate()],
                                   [first_review(), still_wrong(), repair_review()])
    assert done['status'] == 'applied', done.get('pending')
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'c01.r1', 'c01.r2']
    findings = seen['reviewer'][1].packet['previous_findings']
    assert [c['slug'] for c in findings['cards']] == ['marker-dimming']
    assert seen['reviewer'][2].packet['previous_findings']['cards'][0]['reason'] == 'still says deleted'
    assert 'This is a repair round' in seen['reviewer'][1].prompt and 'This is a repair round' not in \
        seen['reviewer'][0].prompt
    frozen = jobs.read_input(root, job_id)['settings']['review']
    assert (frozen['max_repair_rounds'], frozen['max_invalid_retries'], frozen['focused_review'], frozen['amend']) == \
        (2, 2, True, True)


@pytest.mark.parametrize('rounds', [1, 2, 3])
def test_the_number_of_repair_rounds_is_the_setting(tmp_path, rounds):
    root, job_id, done, seen = run(tmp_path, [first_candidate()] + [repair_candidate()] * rounds,
                                   [first_review()] + [still_wrong()] * rounds, max_repair_rounds=rounds)
    assert len(seen['reviewer']) == rounds + 1 and done['status'] == 'partial'
    assert [p['slug'] for p in done['pending']] == ['marker-dimming']


def test_an_answer_that_fails_the_checks_in_a_repair_round_is_asked_again_without_spending_a_round(tmp_path):
    one = repair_candidate()['cards'][0]  # one review question: an error the program does not repair
    bad = {**repair_candidate(), 'cards': [{**one, 'flashcards': one['flashcards'][:1]}]}
    root, job_id, done, seen = run(tmp_path, [first_candidate(), bad, bad, repair_candidate()],
                                   [first_review(), repair_review()], max_repair_rounds=1)
    assert done['status'] == 'applied', done.get('pending')
    assert [t.chunk for t in seen['producer']] == ['c01', 'c01.r1', 'c01.r1', 'c01.r1']
    assert jobs.load(root, job_id)['chunks']['c01.r1']['invalid_outputs'] == 2


def test_a_review_that_keeps_failing_the_checks_leaves_its_cards_pending(tmp_path):
    bad = with_rows(first_review(), **{COST: {'amend': [{'op': 'rewrite'}]}})
    root, job_id, done, seen = run(tmp_path, [first_candidate()], [bad] * 3)
    assert len(seen['reviewer']) == 3 and done['status'] == 'failed'
    assert {p['slug'] for p in done['error']['pending']} == set(CARDS)
    assert 'the review of c01 failed validation 3 times' in done['error']['pending'][0]['reason']


def test_the_budgets_are_checked():
    with pytest.raises(ValueError, match='max_invalid_retries'):
        config.ReviewPolicy(max_invalid_retries=-1)
    with pytest.raises(ValueError, match='focused_review and review.amend'):
        config.ReviewPolicy(amend='yes')


# --- mechanical fixes ---------------------------------------------------------------------------------------------
COST = 'lit-marker-cost'


def accept_with(fixes, **extra):
    """The first review with lit-marker-cost accepted with ``fixes``; marker-dimming accepted too (no repair)."""
    return with_rows(first_review(), **{COST: {'amend': fixes, **extra},
                                        'marker-dimming': {'decision': 'accept', 'overlap': 'distinct'}})


def test_a_relation_added_by_the_reviewer_is_written_listed_and_recorded_as_its_step(tmp_path):
    fix = {'op': 'add_relation', 'type': 'similar_to', 'target': 'marker-dimming', 'note': 'both are about markers'}
    root, job_id, done, _ = run(tmp_path, [first_candidate()], [accept_with([fix])])
    assert done['status'] == 'applied', done.get('pending')
    card = written(root, CARDS[COST]['title'])
    dimming = written(root, CARDS['marker-dimming']['title'])
    assert {'type': 'similar_to', 'target': dimming.id} in [{k: r[k] for k in ('type', 'target')}
                                                            for r in card.fm['relations']]
    [row] = done['amended']
    assert row['slug'] == COST and row['op'] == 'add_relation' and row['before'] is None
    assert row['after']['target'] == 'marker-dimming'
    chain = decisions.chain(done['decision_record'], 'relations', COST)
    assert [(e['role'], e['op']) for e in chain][:2] == [('producer', 'create'), ('reviewer', 'add_relation')]
    assert decisions.current(done['decision_record'], 'verdict', COST)['data']['amend'] == [fix]


def test_a_relation_retyped_or_removed_by_the_reviewer(tmp_path):
    fixes = [{'op': 'retype_relation', 'target': 'lantern-queue', 'type': 'part_of'},
             {'op': 'remove_relation', 'target': EXISTING}]
    root, job_id, done, _ = run(tmp_path, [first_candidate()], [accept_with(fixes)])
    card = written(root, CARDS[COST]['title'])
    queue = written(root, CARDS['lantern-queue']['title'])
    pairs = [(r['type'], r['target']) for r in card.fm['relations']]
    assert ('part_of', queue.id) in pairs and EXISTING not in {t for _, t in pairs}
    assert [r['op'] for r in done['amended']] == ['retype_relation', 'remove_relation']
    assert done['amended'][0]['before']['type'] == 'depends_on' and done['amended'][1]['after'] is None


def test_a_card_refiled_by_the_reviewer_is_filed_there_and_its_place_is_the_reviewers(tmp_path):
    fix = {'op': 'refile', 'pillar': PILLAR, 'topic': TOPIC}
    root, job_id, done, _ = run(tmp_path, [first_candidate()], [accept_with([fix])])
    assert done['status'] == 'applied', done.get('pending')
    assert written(root, CARDS[COST]['title']).fm['topic'] == TOPIC
    chain = decisions.chain(done['decision_record'], 'classification', COST)
    assert [(e['role'], e['op']) for e in chain] == [('producer', 'propose'), ('reviewer', 'refile')]


def test_a_review_card_dropped_by_the_reviewer_is_not_written(tmp_path):
    q = CARDS[COST]['flashcards'][2]['q']
    root, job_id, done, _ = run(tmp_path, [first_candidate()], [accept_with([{'op': 'drop_flashcard', 'q': q}])])
    assert q not in written(root, CARDS[COST]['title']).body
    assert done['amended'][0]['before']['q'] == q


@pytest.mark.parametrize('fixes, problem', [
    ([{'op': 'add_relation', 'type': 'similar_to', 'target': 'nowhere'}], 'not a card it can link'),
    ([{'op': 'add_relation', 'type': 'similar_to', 'target': 'lantern-queue'}], 'links lantern-queue already'),
    ([{'op': 'add_relation', 'type': 'contrasts_with', 'target': 'marker-dimming'}], 'the relation contrasts_with'),
    ([{'op': 'retype_relation', 'target': 'marker-dimming', 'type': 'part_of'}], 'no relation to marker-dimming'),
    ([{'op': 'refile', 'pillar': PILLAR, 'topic': 'Nowhere'}], 'is not a curated topic'),
    ([{'op': 'drop_flashcard', 'q': CARDS[COST]['flashcards'][0]['q']},
      {'op': 'drop_flashcard', 'q': CARDS[COST]['flashcards'][1]['q']}], 'fewer than 2 review cards'),
])
def test_a_fix_that_is_not_legal_sends_the_card_back_as_a_revise(tmp_path, fixes, problem):
    fixed = {**CARDS[COST], 'topic': TOPIC, 'relations': [], 'reason': 'PRODUCER-REASON-9 fixed'}
    rows = [{'source_quote': fixed['source_quotes'][0], 'disposition': 'card', 'cards': [COST], 'reason': 'r'}]
    accepted = {'cards': [{'slug': COST, 'decision': 'accept', 'overlap': 'distinct', 'reason': 'now right'}],
                'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'covered'}}
    root, job_id, done, seen = run(tmp_path, [first_candidate(), {'cards': [fixed], 'new_topics': [],
                                                                  'coverage': rows}],
                                   [accept_with(fixes), accepted])
    stored = jobs.artifact(root, jobs.load(root, job_id), 'review', 'c01')
    row = next(r for r in stored['cards'] if r['slug'] == COST)
    assert row['decision'] == 'revise' and problem in row['reason'] and row['amend_refused']['amend'] == fixes
    assert 'overlap' not in row
    assert [t.chunk for t in seen['producer']] == ['c01', 'c01.r1']  # an ordinary repair round
    assert done['status'] == 'applied', done.get('pending')


def test_an_extends_card_may_get_its_link_by_a_fix_but_not_lose_it():
    review = with_rows(first_review(), **{'queue-order': {
        'overlap': 'extends', 'with': [EXISTING],
        'amend': [{'op': 'add_relation', 'type': 'part_of', 'target': EXISTING}]}})
    candidate = first_candidate()
    assert ingest.validate_review(candidate, review, {EXISTING}, {'attention'})
    card = {**CARDS['lantern-queue'], 'relations': [{'type': 'similar_to', 'target': EXISTING}]}
    why = amends.problems(card, [{'op': 'remove_relation', 'target': EXISTING}], closed=lambda *a: None,
                          legal=lambda *a: True, domain_of=lambda t: 'd', keep_link=EXISTING)
    assert why == [f'amend[0] (remove_relation): it extends {EXISTING} and keeps linking it']


@pytest.mark.parametrize('rows, problem', [
    ({COST: {'amend': [{'op': 'add_relation', 'target': 'x'}]}}, 'needs type'),
    ({COST: {'amend': [{'op': 'rewrite', 'text': 'x'}]}}, 'op must be one of'),
    ({'marker-dimming': {'amend': [{'op': 'drop_flashcard', 'q': 'x'}]}}, 'comes only with accept'),
])
def test_a_fix_out_of_form_is_refused_at_submit(tmp_path, rows, problem):
    root = build_ingest_vault(tmp_path / 'vault')
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    packet, state = jobs.read_input(root, job['job_id']), jobs.load(root, job['job_id'])
    with pytest.raises(ingest.IngestError, match=problem):
        ingest._made_amends(root, packet, state, 'c01', first_candidate(), with_rows(first_review(), **rows), {})


def test_with_amend_off_a_fix_is_refused_and_the_prompt_does_not_offer_it(tmp_path):
    fix = [{'op': 'drop_flashcard', 'q': CARDS[COST]['flashcards'][2]['q']}]
    root, job_id, done, seen = run(tmp_path, [first_candidate()],
                                   [accept_with(fix), accept_with([])], amend=False)
    assert 'Mechanical fixes' not in seen['reviewer'][0].prompt
    assert '[review] amend is off' in jobs.load(root, job_id)['chunks']['c01']['last_review_error']
    assert 'rejected by the validator' in seen['reviewer'][1].prompt and done['status'] == 'applied'


def test_the_reviewers_fixes_are_shown_to_the_merge_check():
    from secondbrain.pipelines import source_check
    card = {**CARDS[COST], '_amended': [{'op': 'refile', 'before': {}, 'after': {}}]}
    settled = [{'chunk': 'c01', 'accepted': {COST: card}, 'topics': {},
                'notes': [{'slug': COST, 'category': 'minor', 'note': 'the essence is long'}]}]
    frozen = source_check.frozen_input(settled, {'filename': 'f'}, relation_views={})
    assert frozen['cards'][0]['amended'][0]['op'] == 'refile'
    assert frozen['cards'][0]['review_notes'] == ['[minor] the essence is long']


# --- focused review -------------------------------------------------------------------------------------------------
def test_a_focused_review_sends_a_card_back_only_for_an_unfixed_finding_or_a_blocking_problem(tmp_path):
    loose = {'cards': [{'slug': 'marker-dimming', 'decision': 'revise', 'reason': 'the essence could be shorter'}],
             'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}
    noted = {'cards': [{'slug': 'marker-dimming', 'decision': 'accept', 'overlap': 'distinct', 'reason': 'fixed',
                        'fixed': [{'finding': 'dimmed, not deleted', 'fixed': True}],
                        'notes': [{'category': 'minor', 'text': 'the essence could be shorter'}]}],
             'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}
    root, job_id, done, seen = run(tmp_path, [first_candidate(), repair_candidate()], [first_review(), loose, noted])
    assert done['status'] == 'applied', done.get('pending')
    error = jobs.load(root, job_id)['chunks']['c01.r1']['last_review_error']
    assert 'revised only for an earlier finding that is not fixed' in error
    assert done['review_notes'] == [{'chunk': 'c01', 'slot': 'c01.r1', 'slug': 'marker-dimming',
                                     'category': 'minor', 'note': 'the essence could be shorter'}]
    assert jobs.load(root, job_id)['review_notes'] == done['review_notes']  # what `sb job show` prints


def test_a_blocking_problem_sends_a_card_back_in_a_focused_review(tmp_path):
    blocking = {'cards': [{'slug': 'marker-dimming', 'decision': 'revise', 'reason': 'P2 contradicts the chunk',
                           'fixed': [{'finding': 'dimmed, not deleted', 'fixed': True}], 'blocking': 'fact'}],
                'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}
    root, job_id, done, seen = run(tmp_path, [first_candidate(), repair_candidate(), repair_candidate()],
                                   [first_review(), blocking, repair_review()])
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'c01.r1', 'c01.r2'] and done['status'] == 'applied'


def test_with_focused_review_off_a_repair_round_is_reviewed_in_full(tmp_path):
    loose = {'cards': [{'slug': 'marker-dimming', 'decision': 'revise', 'reason': 'the essence could be shorter'}],
             'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}
    root, job_id, done, seen = run(tmp_path, [first_candidate(), repair_candidate(), repair_candidate()],
                                   [first_review(), loose, repair_review()], focused_review=False)
    assert 'previous_findings' not in seen['reviewer'][1].packet
    assert 'This is a repair round' not in seen['reviewer'][1].prompt
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'c01.r1', 'c01.r2'] and done['status'] == 'applied'


def test_p8_a_relation_is_made_by_steps_in_order_producer_reviewer_fix_merge_check_program(tmp_path):
    from tests.ingest_helpers import keep_all, with_diagrams
    from tests.test_invariants import LEVELS, two_chunk_job, two_chunk_producer
    from secondbrain.pipelines import source_check
    root, job_id = two_chunk_job(tmp_path)

    def reviewer(task):
        if source_check.is_slot(task.chunk):
            return keep_all(task.packet, **{'arrival-order': {'overlap': 'distinct', 'links': [
                {'target': 'filler-note', 'type': 'similar_to', 'note': 'both say what a queue keeps'}]}})
        rows = []
        for c in task.packet['candidate']['cards']:
            row = {'slug': c['slug'], 'decision': 'accept', 'reason': 'ok', **LEVELS.get(c['slug'], {'overlap': 'distinct'})}
            if c['slug'] == 'arrival-order':
                row['amend'] = [{'op': 'retype_relation', 'target': 'lantern-queue', 'type': 'similar_to'}]
            rows.append(row)
        return {'cards': rows, 'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=with_diagrams(two_chunk_producer)),
                          reviewer=FakeExecutor('claude-test', responder=reviewer))
    assert done['status'] == 'applied', done.get('pending')
    ops = [(e['role'], e['op']) for e in decisions.chain(done['decision_record'], 'relations', 'arrival-order')]
    assert ops == [('producer', 'create'), ('reviewer', 'retype_relation'), ('merge_check', 'add')]
    arrival = next(c for c in read_snapshot(root).cards.values() if c.title == 'Lantern queues keep arrival order')
    assert [r['type'] for r in arrival.fm['relations']][:2] == ['similar_to', 'similar_to']
    # the record refuses the same fix after the merge check's step: the order is fixed
    with pytest.raises(decisions.DecisionError, match='cannot come after a merge_check step'):
        decisions.step(list(done['decision_record']), 'relations', 'arrival-order', 'remove_relation',
                       role='reviewer', slot='c03', data={'target': 'filler-note'})

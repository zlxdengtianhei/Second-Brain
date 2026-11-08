"""Fourth verdict, not blocking: items 1, 2, 5 and 7 (item 3 is in tests/test_one_repeat_rule.py, F2).

1  The last answer of the merge check holds back a carry into a card of the source too, not only a patch: the card
   is written as it is (a possible repeat, for the owner) and the rest of the answer lands; a card written because
   the merge check was given up says so, not "no patch".
2  A run that ended in a transport failure is not an answer: it does not bring the last answer nearer.
5  When the program writes a card of the patch set next to its existing card, the decision record says so too.
7  The executor's ``max_retries`` is a whole number from 0 to 10.
"""
from __future__ import annotations

import copy

import pytest

from secondbrain import cards as cardfmt
from secondbrain import config, overlap
from secondbrain.executors.base import ExecutorFailure, Result
from secondbrain.pipelines import source_check
from tests.helpers import EXISTING
from tests.ingest_helpers import keep_all, with_diagrams
from tests.test_invariants import ARRIVAL, two_chunk_job, two_chunk_producer
from tests.test_patch_by_patch import producer_with, three_patches
from tests.test_patch_set import LANTERN, card_of, job, reviewer, run, sb_existing

OLD, NEW = 'Each marker costs 1 KB of memory.', 'Each marker costs 1 Kb of memory.'


def kb_producer(task):
    """lit-marker-cost says OLD; arrival-order, which the merge check finds extends it, says NEW (case apart)."""
    out = copy.deepcopy(two_chunk_producer(task))
    for c in out.get('cards') or []:
        if c['slug'] == 'lit-marker-cost':
            c['propositions'] = list(c['propositions']) + [OLD]
        if c['slug'] == 'arrival-order':
            c['propositions'] = list(c['propositions']) + [NEW]
    return out


def insist(p):
    """Every answer carries NEW into lit-marker-cost, which the program sends back each time."""
    return keep_all(p, **{'arrival-order': {'overlap': 'extends', 'with': 'lit-marker-cost', 'reason': 'r',
                                            'carry': {'propositions': list(ARRIVAL['propositions']) + [NEW]}}})


# --- 1 ------------------------------------------------------------------------------------------------------------
def test_p_d20c_the_last_answer_holds_back_a_carry_and_the_patch_of_another_card_still_goes_in(tmp_path):
    root, job_id, _ = job(tmp_path)
    checks = []
    done = run(root, job_id, reviewer(checks, check=insist), with_diagrams(kb_producer))
    assert len(checks) == 3 and done['status'] == 'applied'
    assert done['source_check']['status'] == 'checked'  # not skipped: the rest of the last answer landed
    assert done['patch_set'] == {'lantern-queue': 'patched'}
    assert cardfmt.propositions_of(card_of(root, EXISTING).body)[2:] == LANTERN['propositions']
    [row] = [n for n in done['needs_review'] if n['title'] == ARRIVAL['title']]
    lit = next(p['id'] for p in done['produced'] if p['slug'] == 'lit-marker-cost')
    assert row['with'] == lit and "carry of it into lit-marker-cost did not pass the program checks" in \
        row['not_merged'] and NEW in row['not_merged']
    [held] = done['patches_held']
    assert held['carry'] is True and held['cards'] == ['arrival-order'] and held['target'] == lit
    arrival = next(p['id'] for p in done['produced'] if p['slug'] == 'arrival-order')
    assert NEW in cardfmt.propositions_of(card_of(root, arrival).body)  # nothing of it lost
    assert overlap.outcome(done['decision_record'], 'arrival-order', checked=True)['outcome'] == 'write'


def test_salvage_alone_keeps_a_held_carry_card_as_a_card_of_its_own():
    frozen = {'cards': [{'slug': 'a', 'chunk': 'c01', 'title': 'A', 'domain': 'd', 'pillar': 'p', 'topic': 't',
                         'propositions': [OLD], 'flashcards': [], 'relations': [], 'isolated': True, 'candidates': []},
                        {'slug': 'b', 'chunk': 'c02', 'title': 'B', 'domain': 'd', 'pillar': 'p', 'topic': 't',
                         'propositions': [NEW], 'flashcards': [], 'relations': [], 'isolated': True, 'candidates': []}],
              'new_topics': [], 'patch_set': []}
    answer = {'cards': [{'slug': 'a', 'overlap': 'distinct', 'alone': 'x'},
                        {'slug': 'b', 'overlap': 'extends', 'with': ['a'], 'reason': 'r',
                         'carry': {'propositions': [NEW]}}], 'new_topics': []}
    with pytest.raises(source_check.Refused):
        source_check.validate_answer(frozen, answer)
    kept = source_check.salvage(frozen, answer)
    assert kept['held'] == [] and [h['slug'] for h in kept['held_carries']] == ['b']
    assert kept['held_carries'][0]['into'] == 'a' and NEW in kept['held_carries'][0]['problems'][0]
    assert next(r for r in kept['cards'] if r['slug'] == 'b')['overlap'] == 'distinct'


def test_a_card_written_because_the_merge_check_was_given_up_says_so(tmp_path):
    root, job_id, _ = job(tmp_path)
    done = run(root, job_id, reviewer(check=lambda p: {'cards': 'none', 'new_topics': []}))
    assert done['source_check']['status'] == 'skipped' and done['patch_set'] == {'lantern-queue': 'written'}
    [row] = [n for n in done['needs_review'] if n['title'] == LANTERN['title']]
    assert row['not_merged'].startswith(f'the merge check was given up, so nothing went into {EXISTING}: ')
    assert 'failed validation 3 times' in row['not_merged']


# --- 2 ------------------------------------------------------------------------------------------------------------
def test_p_tr_transport_failures_of_the_merge_check_do_not_count_toward_its_last_answer(tmp_path):
    root, job_id, before, produce, levels = three_patches(tmp_path)
    calls, answers = [], []
    inner = reviewer(answers, levels=levels)

    def respond(task):
        if source_check.is_slot(task.chunk):
            calls.append(task.chunk)
            if len(calls) <= 2:
                return Result(status='failed', attestation='test',
                              error={'code': 'HTTP_ERROR', 'message': 'HTTP 502: bad gateway', 'retryable': True,
                                     'http_status': 502})
        return inner(task)
    stops = 0
    for _ in range(4):
        try:
            done = run(root, job_id, respond, producer_with(produce))
            break
        except ExecutorFailure:
            stops += 1
    assert stops == 2 and len(answers) == 3  # the first real answer was not taken as the last one
    assert len(done['source_check_refusals']) == 2 and [h['target'] for h in done['patches_held']] == ['card_c']


# --- 5 ------------------------------------------------------------------------------------------------------------
def test_with_merging_off_the_decision_record_says_the_card_is_written_as_the_result_does(tmp_path):
    root, job_id = two_chunk_job(tmp_path, merge_into_existing=False)
    sb_existing(root)
    done = run(root, job_id, reviewer())
    assert done['outcomes']['lantern-queue'] == 'write'
    found = overlap.outcome(done['decision_record'], 'lantern-queue', checked=True)
    assert found['outcome'] == 'write' and found['by'] == 'program'
    for slug, went in done['outcomes'].items():
        assert overlap.outcome(done['decision_record'], slug, checked=True)['outcome'] == went


# --- 7 ------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('value', [-1, 11, 100, True, 2.5, '2'])
def test_the_executor_retries_are_a_whole_number_from_0_to_10(value):
    with pytest.raises(ValueError, match='max_retries must be a whole number from 0 to 10'):
        config.EndpointConfig(kind='openai', base_url='https://example.invalid', model='m', max_retries=value)


@pytest.mark.parametrize('value', [0, 2, 10])
def test_the_executor_retries_in_range_are_kept(value):
    assert config.EndpointConfig(kind='openai', base_url='https://example.invalid', model='m',
                                 max_retries=value).max_retries == value

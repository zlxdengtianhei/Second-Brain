"""A program finding that two texts are the same is a reason to ask again, never to leave one out (decision D-21).

The third verdict (K4'') found two places that left a carried proposition out when the text comparison called it
the same as one the card has: the merge of two cards of one source, and inside one patch. The comparison's table
leaves some marks out, and some marks change what a text says (a case, a superscript, a factorial, `!=`, a comma
between digits). Now only the same words, spaces put right, are left out by the program; anything else the
comparison finds the same goes back to the merge check, which may keep it by saying in `differs` why it is
another point. The pairs are those of the judge's probe P-K4d.
"""
from __future__ import annotations

import copy

import pytest

from secondbrain import cards as cardfmt
from secondbrain.pipelines import ingest, source_check
from secondbrain.textnorm import same_text
from secondbrain.vault import read_snapshot
from tests.ingest_helpers import keep_all, with_diagrams
from tests.test_invariants import ARRIVAL, two_chunk_producer
from tests.test_patch_set import job, reviewer, run

PAIRS = [  # (kind, what the kept card says, what the other card carries)
    ('case', 'Each marker costs 1 KB of memory.', 'Each marker costs 1 Kb of memory.'),
    ('NFKC superscript', 'A full ring holds 10³ items.', 'A full ring holds 103 items.'),
    ('factorial after a bracket', 'A ring of n lanterns has (n-1) rotations.',
     'A ring of n lanterns has (n-1)! rotations.'),
    ('! after a space', 'Lighting stops when x = 0.', 'Lighting stops when x != 0.'),
    ('comma and space between digits', 'Markers 1, 2 and 3 are lit first.', 'Markers 12 and 3 are lit first.'),
    ('CJK enumeration comma', '第1、2个标记先亮。', '第12个标记先亮。'),
]


def everywhere(root):
    return [p for c in read_snapshot(root).cards.values() for p in cardfmt.propositions_of(c.body) or []]


@pytest.mark.parametrize('kind, old, new', PAIRS, ids=[p[0] for p in PAIRS])
def test_a_same_source_merge_asks_again_and_keeps_the_new_point(tmp_path, kind, old, new):
    """Group A: arrival-order extends lit-marker-cost and carries a proposition the comparison finds the same as one
    of lit-marker-cost's. Before: left out, no refusal. Now: sent back naming both; kept with `differs`."""
    assert same_text(old, new)
    root, job_id, _ = job(tmp_path)
    checks = []

    def produce(task):
        out = copy.deepcopy(two_chunk_producer(task))  # the fixture cards are shared
        for c in out.get('cards') or []:
            if c['slug'] == 'lit-marker-cost':
                c['propositions'] = list(c['propositions']) + [old]
            if c['slug'] == 'arrival-order':
                c['propositions'] = list(c['propositions']) + [new]
        return out

    def check(packet):
        carry = {'propositions': list(ARRIVAL['propositions']) + [new]}
        if len(checks) > 1:  # the second answer says why it is another point
            n = 1 + next(i for i, c in enumerate(packet['cards']) if c['slug'] == 'lit-marker-cost')
            props = next(c for c in packet['cards'] if c['slug'] == 'lit-marker-cost')['propositions']
            carry['differs'] = [{'text': new, 'from': f'P{props.index(old) + 1}', 'reason': f'{kind} changes it'}]
            assert n
        return keep_all(packet, **{'arrival-order': {'overlap': 'extends', 'with': 'lit-marker-cost', 'reason': 'r',
                                                     'carry': carry}})
    done = run(root, job_id, reviewer(checks, check=check), with_diagrams(produce))
    assert done['status'] == 'applied', done.get('pending')
    [refusal] = done['source_check_refusals']
    assert repr(new) in refusal['message'] and repr(old) in refusal['message']
    props = everywhere(root)
    assert new in props and old in props


def test_a_same_source_merge_still_leaves_out_the_same_words_by_itself(tmp_path):
    root, job_id, _ = job(tmp_path)
    checks = []
    again = ARRIVAL['propositions'][0] + '  '  # the same words, a space more

    def produce(task):
        out = copy.deepcopy(two_chunk_producer(task))
        for c in out.get('cards') or []:
            if c['slug'] == 'lit-marker-cost':
                c['propositions'] = list(c['propositions']) + [again]
        return out

    def check(packet):
        return keep_all(packet, **{'arrival-order': {'overlap': 'extends', 'with': 'lit-marker-cost', 'reason': 'r',
                                                     'carry': {'propositions': list(ARRIVAL['propositions'])}}})
    done = run(root, job_id, reviewer(checks, check=check), with_diagrams(produce))
    assert len(checks) == 1 and done['source_check_refusals'] == []
    [merged] = done['source_check']['merged']
    assert merged['repeats_skipped'] == 1


# --- group C: inside one patch ----------------------------------------------------------------------------------
OLD = {'id': 'kc_old', 'title': 'Lanterns', 'propositions': ['An old point.'], 'flashcards': []}


def frozen_with(*props):
    card = {'slug': 'new-card', 'chunk': 'c01', 'title': 'n', 'essence': 'e', 'propositions': list(props),
            'flashcards': []}
    return {'cards': [], 'new_topics': [], 'patch_set': [{'target': 'kc_old', 'card': OLD, 'from': [card]}]}


@pytest.mark.parametrize('kind, a, b', PAIRS, ids=[p[0] for p in PAIRS])
def test_two_propositions_of_one_patch_that_only_look_the_same_go_back_and_neither_is_dropped(kind, a, b):
    frozen = frozen_with(a, b)
    answer = {'cards': [], 'new_topics': [], 'patches': [{'target': 'kc_old', 'propositions': [a, b]}]}
    with pytest.raises(ValueError, match='which it carries too'):
        source_check.validate_answer(frozen, answer)
    answer['patches'][0]['differs'] = [{'text': b, 'from': a, 'reason': f'{kind} changes it'}]
    assert source_check.validate_answer(frozen, answer)
    old = cardfmt_card(['An old point.'])
    assert ingest._patch_fits(old, {'propositions': [a, b]})[1] == [a, b]               # neither left out


@pytest.mark.parametrize('kind, a, b', PAIRS, ids=[p[0] for p in PAIRS])
def test_a_proposition_that_only_looks_like_a_replacement_is_not_dropped(kind, a, b):
    frozen = frozen_with(a, b)
    answer = {'cards': [], 'new_topics': [],
              'patches': [{'target': 'kc_old', 'propositions': [b], 'replace': [{'proposition': 'P1', 'by': a}]}]}
    with pytest.raises(ValueError, match='which it carries too'):
        source_check.validate_answer(frozen, answer)
    old = cardfmt_card(['An old point.'])
    assert ingest._patch_fits(old, {'propositions': [b], 'replace': [{'proposition': 'P1', 'by': a}]})[1] == [b]


def cardfmt_card(props):
    from types import SimpleNamespace
    body = ('## 本质\n已有卡。\n\n## 命题\n' + '\n'.join(f'- P{i}. {p}' for i, p in enumerate(props, 1))
            + '\n\n## 相关\n暂无。\n\n## 复习卡\nOld question?::Old answer.\n')
    return SimpleNamespace(body=body)


# --- group B: a patch to an existing card ------------------------------------------------------------------------
def test_a_patch_item_the_comparison_finds_the_same_as_the_card_stays_with_differs():
    for kind, old, new in PAIRS:
        frozen = {'cards': [], 'new_topics': [], 'patch_set': [{'target': 'kc_old', 'from': [
            {'slug': 'n', 'propositions': [new], 'flashcards': []}],
            'card': {'propositions': [old], 'flashcards': []}}]}
        answer = {'cards': [], 'new_topics': [], 'patches': [{'target': 'kc_old', 'propositions': [new]}]}
        with pytest.raises(ValueError, match='looks the same as kc_old P1'):
            source_check.validate_answer(frozen, answer)
        answer['patches'][0]['differs'] = [{'text': new, 'from': 'P1', 'reason': f'{kind} changes it'}]
        assert source_check.validate_answer(frozen, answer), kind

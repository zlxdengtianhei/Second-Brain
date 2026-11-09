"""The program's fixed checks of a card's text are questions for its reviewer, never a verdict of the program's own.

Two checks were measured on cards blind judges had judged (others marked cards at the base rate and were left out):
a phrase that points at the source instead of saying the thing, and a one-letter symbol given a value or used in a
product that the card never explains. A hit is listed in the review packet (``program_questions``); the reviewer
answers each question of a card yes or no with a reason (``answers``); a yes sends an accepted card back as
``unreadable``, a no leaves it as it is, and a review that leaves a question unanswered is refused and asked again.
The texts are invented; the patterns are Chinese, the language they were measured on.
"""
from __future__ import annotations

import copy

import pytest

from secondbrain import jobs
from secondbrain.pipelines import cardlint
from tests.ingest_helpers import first_candidate, first_review, repair_candidate, repair_review
from tests.test_review_rules import run, with_rows


def card(*propositions, **fields):
    return {'slug': 'x', 'essence': '一句话。', 'propositions': list(propositions), 'flashcards': [], **fields}


@pytest.mark.parametrize('text', ['原文说两种索引等价。', '按教材的分法，检索分两段。', '表中第二行是召回率。',
                                  '例中 A 先被选中。', '第 3 章讲过这个阈值。', '本节只讲稀疏向量。', '那张卡写了公式。'])
def test_a_phrase_that_points_at_the_source_is_asked_about(text):
    assert cardlint.pointers(card(text))


@pytest.mark.parametrize('text, other', [
    ('索引只存 id，原文另存。', None),                       # 原文 as a word of the subject
    ('上下文窗口决定能看多远。', None),                      # 上下文 is not 下文
    ('表中第二行是召回率。', '| 方法 | 召回 |\n|---|---|\n| A | 0.9 |'),  # the card has the table
    ('例中 A 先被选中。', '例：三个候选 A、B、C，按分数排。'),    # the card sets the example up
    ('教材 08 的练习。', None),                              # a numbered textbook item
    ('词表中没有这个词。', None),                            # 词表 is a word
])
def test_a_phrase_that_does_not_point_at_the_source_is_not(text, other):
    assert cardlint.pointers(card(text, *([other] if other else []))) == []


def test_a_symbol_given_a_value_and_never_explained_is_asked_about():
    assert cardlint.symbols(card('设置 M=16 时召回更高。')) == ['M']
    assert cardlint.symbols(card('代价 N×D，很大。')) == ['D', 'N']


@pytest.mark.parametrize('text', ['参数 M=16 时召回更高。', 'M 是每层的连边数，M=16 时召回更高。',
                                  '三个点 A=0、B=3、C=6 排在一条线上。', 'f(x)=2 时取最大。', '有 N 个向量，N=1000。'])
def test_a_symbol_the_card_explains_or_that_is_no_symbol_is_not(text):
    assert cardlint.symbols(card(text)) == []


def test_the_questions_are_numbered_per_card_and_name_what_was_found():
    asked = cardlint.questions(card('原文说两种索引等价。', '设置 M=16 时召回更高。'))
    assert [(q['id'], q['check']) for q in asked] == [('K1', 'points_at_source'), ('K2', 'symbol')]
    assert '原文说两种索引等价' in asked[0]['question'] and 'M' in asked[1]['question']


# --- in a job ---------------------------------------------------------------------------------------------------------
def pointed():
    answer = copy.deepcopy(first_candidate())
    cost = next(c for c in answer['cards'] if c['slug'] == 'lit-marker-cost')
    cost['propositions'][0] += '（原文说这样最省）'
    return answer


def test_the_review_packet_lists_the_questions_and_a_yes_sends_the_card_back(tmp_path):
    yes = {'answers': [{'id': 'K1', 'answer': 'yes', 'reason': 'the card leans on the source for why'}]}
    root, job_id, done, seen = run(tmp_path, [pointed(), repair_candidate()],
                                   [with_rows(first_review(), **{'lit-marker-cost': yes}), repair_review()])
    [asked] = seen['reviewer'][0].packet['program_questions']['lit-marker-cost']
    assert asked['id'] == 'K1' and asked['check'] == 'points_at_source'
    review = jobs.artifact(root, jobs.load(root, job_id), 'review', 'c01')
    row = next(r for r in review['cards'] if r['slug'] == 'lit-marker-cost')
    assert (row['decision'], row['blocking'], row['turned']['question']) == ('revise', 'unreadable', 'K1')
    assert any(t['slug'] == 'lit-marker-cost' and t['category'] == 'unreadable' for t in done['notes_turned'])


def test_a_no_leaves_the_card_accepted(tmp_path):
    no = {'answers': [{'id': 'K1', 'answer': 'no', 'reason': 'it says why itself'}]}
    root, job_id, done, _ = run(tmp_path, [pointed(), repair_candidate()],
                                [with_rows(first_review(), **{'lit-marker-cost': no}), repair_review()])
    assert done['status'] == 'applied', done.get('pending')
    review = jobs.artifact(root, jobs.load(root, job_id), 'review', 'c01')
    assert next(r for r in review['cards'] if r['slug'] == 'lit-marker-cost')['decision'] == 'accept'


def test_a_review_that_leaves_a_question_unanswered_is_asked_again(tmp_path):
    no = {'answers': [{'id': 'K1', 'answer': 'no', 'reason': 'it says why itself'}]}
    root, job_id, done, seen = run(tmp_path, [pointed(), repair_candidate()],
                                   [first_review(), with_rows(first_review(), **{'lit-marker-cost': no}),
                                    repair_review()])
    assert done['status'] == 'applied', done.get('pending')
    first = jobs.load_run(root, job_id, seen['reviewer'][0].run_id)
    assert any("answers must answer each of the program's questions on this card once (K1)" in r
               for r in first.get('rejected') or [])

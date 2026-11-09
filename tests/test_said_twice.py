"""A patch to an existing card does not say a point twice (H7 ④, decision D-14).

The merge check compares what it carries with the existing card: a proposition the card holds goes into ``replace``
(when it says more) or ``already``; a question the card asks is not carried again. The program checks each
carried proposition and question against the card: the same words always go back to the merge check; a close
wording (a conservative near match) goes back unless the answer says in ``differs`` why it is another point.
Every answer sent back is in the job result. The shapes are those of the H7 judge's said_twice.csv, on invented
cards.
"""
from __future__ import annotations

import pytest

from secondbrain import cards as cardfmt
from secondbrain.pipelines import source_check
from tests.helpers import EXISTING
from tests.ingest_helpers import CARDS, keep_all
from tests.test_patch_set import card_of, job, reviewer, run

LANTERN = CARDS['lantern-queue']


# --- one job: a carried proposition in other words, and a carried question asked again ----------------------------
def existing_with(root, props, flashcards):
    """The existing card in sb format with these propositions and review cards."""
    path = next((root / '10_Knowledge').rglob(f'{EXISTING}.md'))
    head = path.read_bytes().decode('utf-8').split('\n---\n', 1)[0] + '\n---\n'
    body = ('## 本质\n已有卡正文。\n\n## 命题\n' + '\n'.join(f'- P{i}. {p}' for i, p in enumerate(props, 1))
            + '\n\n## 相关\n暂无。\n\n## 复习卡\n' + '\n'.join(f'{q}::{a}' for q, a in flashcards) + '\n')
    path.write_bytes((head + body).encode('utf-8'))


def test_a_patch_that_says_a_point_of_the_card_again_is_sent_back_and_the_card_says_it_once(tmp_path):
    root, job_id, _ = job(tmp_path)
    existing_with(root, ['Every item stays in the order it arrived in.', 'Markers cost one word each.'],
                  [('In what order does the queue keep its items?', 'Arrival order.')])
    checks = []

    def check(packet):
        if len(checks) == 1:  # everything of the card that extends it, as before
            return keep_all(packet)
        return keep_all(packet, _patches={EXISTING: {
            'propositions': [LANTERN['propositions'][1]], 'flashcards': [LANTERN['flashcards'][1]],
            'already': [{'text': LANTERN['propositions'][0], 'as': 'P1'},
                        {'text': LANTERN['flashcards'][0]['q'], 'as': 'Q1'}]}})
    done = run(root, job_id, reviewer(checks, check=check))
    assert done['status'] == 'applied', done.get('pending')
    body = card_of(root, EXISTING).body
    assert cardfmt.propositions_of(body) == ['Every item stays in the order it arrived in.',
                                             'Markers cost one word each.', LANTERN['propositions'][1]]
    assert [f['q'] for f in cardfmt.flashcards_of(body)] == ['In what order does the queue keep its items?',
                                                             LANTERN['flashcards'][1]['q']]
    # each already row is in the result, for a judge to check that P1 says it (second verdict, not blocking 2)
    assert done['merged_into_existing'][0]['already'] == [{'text': LANTERN['propositions'][0], 'as': 'P1'},
                                                          {'text': LANTERN['flashcards'][0]['q'], 'as': 'Q1'}]
    [refusal] = done['source_check_refusals']
    assert refusal['slot'] == 'xc01'
    assert f"{LANTERN['propositions'][0]!r} may say again what {EXISTING} P1 says" in refusal['message']
    assert f"{LANTERN['flashcards'][0]['q']!r} may ask again what {EXISTING} Q1 asks" in refusal['message']


# --- the shapes, against one invented existing card -----------------------------------------------------------------
OLD = {'id': 'kc_old', 'title': '灯笼队列',
       'propositions': ['灯笼队列按到达顺序保存每一项。', '写入者每八项点亮一个标记。', '限位螺丝调好后在各挡位检查有无异响。'],
       'flashcards': [{'q': '王车易位时王走几格？', 'a': '两格。'}, {'q': '这种队列按什么顺序保存？', 'a': '到达顺序。'}]}


def frozen_with(*from_props, from_flashcards=()):
    card = {'slug': 'new-card', 'chunk': 'c01', 'title': '新卡', 'essence': 'e', 'propositions': list(from_props),
            'flashcards': [{'q': q, 'a': a} for q, a in from_flashcards]}
    return {'cards': [], 'new_topics': [], 'patch_set': [{'target': 'kc_old', 'card': OLD, 'from': [card]}]}


def check(frozen, **patch):
    answer = {'cards': [], 'new_topics': [], 'patches': [{'target': 'kc_old', **patch}]}
    return source_check.validate_answer(frozen, answer)


@pytest.mark.parametrize('carried, says', [
    ('每一项都按到达顺序存进灯笼队列。', 'may say again what kc_old P1 says'),          # the same point in other words
    ('灯笼队列按到达顺序保存每一项，读者可以跳到亮着的标记。', 'may say again what kc_old P1 says'),  # P1 and more
    ('灯笼队列按到达顺序保存每一项', 'looks the same as kc_old P1'),                      # all but a full stop
    ('灯笼队列按到达顺序保存每一项。 ', 'says what kc_old P1 says word for word'),        # the same words
])
def test_a_carried_proposition_the_card_holds_goes_back(carried, says):
    with pytest.raises(ValueError, match=says):
        check(frozen_with(carried), propositions=[carried])


def test_a_proposition_that_says_more_goes_into_replace_and_one_it_holds_into_already():
    fuller = '灯笼队列按到达顺序保存每一项，读者可以跳到亮着的标记。'
    assert check(frozen_with(fuller), replace=[{'proposition': 'P1', 'by': fuller}])
    same = '每一项都按到达顺序存进灯笼队列。'
    assert check(frozen_with(same, '队列满了以后最早的一项被挤出。'), propositions=['队列满了以后最早的一项被挤出。'],
                 already=[{'text': same, 'as': 'P1'}])


def test_a_replacement_that_says_another_proposition_of_the_card_again_goes_back():
    by = '写入者每八项点亮一个标记；限位螺丝调好后在各挡位检查有无异响。'
    with pytest.raises(ValueError, match='may say again what kc_old P3 says'):
        check(frozen_with(by), replace=[{'proposition': 'P2', 'by': by}])


@pytest.mark.parametrize('question, says', [
    ('易位时王走几格、车怎么走？', 'may ask again what kc_old Q1 asks'),     # asked again with more in it
    ('灯笼队列按什么顺序保存？', 'may ask again what kc_old Q2 asks'),       # the same question, "this" named
    ('王车易位时，王走几格?', 'looks the same as kc_old Q1'),               # the same question, other punctuation
    ('王车易位时王走几格？', 'asks what kc_old Q1 asks'),                    # the same words
])
def test_a_carried_question_the_card_asks_goes_back(question, says):
    with pytest.raises(ValueError, match=says):
        check(frozen_with('新的一点。', from_flashcards=[(question, '答。')]), propositions=['新的一点。'],
              flashcards=[{'q': question, 'a': '答。'}])


def test_a_near_match_that_is_another_point_stays_when_the_answer_says_why_an_exact_one_never_does():
    question = '易位时王走几格、车怎么走？'
    frozen = frozen_with('新的一点。', from_flashcards=[(question, '答。')])
    patch = {'propositions': ['新的一点。'], 'flashcards': [{'q': question, 'a': '答。'}]}
    assert check(frozen, **patch, differs=[{'text': question, 'from': 'Q1', 'reason': 'it asks about the rook too'}])
    with pytest.raises(ValueError, match='differs\\[0\\].from must name'):
        check(frozen, **patch, differs=[{'text': question, 'from': 'Q9', 'reason': 'r'}])
    exact = '王车易位时王走几格？'
    frozen = frozen_with('新的一点。', from_flashcards=[(exact, '两格。')])  # Q1, answer and all: never
    with pytest.raises(ValueError, match='is kc_old Q1, answer and all: leave it out'):
        check(frozen, propositions=['新的一点。'], flashcards=[{'q': exact, 'a': '两格。'}],
              differs=[{'text': exact, 'from': 'Q1', 'reason': 'r'}])
    # D-25: Q1's words with another answer go back naming both answers, and stay when differs says why
    frozen = frozen_with('新的一点。', from_flashcards=[(exact, '答。')])
    with pytest.raises(ValueError, match="asks what kc_old Q1 asks, but their answers differ \\('两格。' and '答。'\\)"):
        check(frozen, propositions=['新的一点。'], flashcards=[{'q': exact, 'a': '答。'}])
    assert check(frozen, propositions=['新的一点。'], flashcards=[{'q': exact, 'a': '答。'}],
                 differs=[{'text': exact, 'from': 'Q1', 'reason': 'both answers are needed'}])
    # D-21: one the comparison finds the same, but not word for word, stays when differs says why
    marks = '王车易位时，王走几格?'
    frozen = frozen_with('新的一点。', from_flashcards=[(marks, '答。')])
    assert check(frozen, propositions=['新的一点。'], flashcards=[{'q': marks, 'a': '答。'}],
                 differs=[{'text': marks, 'from': 'Q1', 'reason': 'it asks about castling on the other side'}])


@pytest.mark.parametrize('proposition, question', [
    ('队列满了以后最早的一项被挤出。', '队列满了会怎样？'),
    ('标记熄灭后读者不再跟随它。', '熄灭的标记还会被跟随吗？'),
    ('Markers are dimmed, not deleted, when the queue is compacted.', 'What happens to markers on compaction?'),
])
def test_a_new_point_and_a_new_question_on_the_same_card_pass(proposition, question):
    assert check(frozen_with(proposition, from_flashcards=[(question, '答。')]), propositions=[proposition],
                 flashcards=[{'q': question, 'a': '答。'}])

"""A patch to an existing card changes only its propositions, review cards, sources and relations, and a card that
would pass 12 propositions or 8 review cards goes to a split slot (design sections 2.1 and 5.3)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from secondbrain import cards as cardfmt
from secondbrain.pipelines import ingest
from secondbrain.vault import parse_note

RAW = ('---\nid: demo_old\ntitle: Old card\ndomain: d\n---\n## 本质\n已有卡。\n\n## 命题\n- P1. One.\n- P2. Two.\n\n'
       '## 直觉与易混对比\n- 直觉：路灯。\n\n## 相关\n暂无。\n\n## 复习卡\nQ1?::A1.\n').encode('utf-8')


def gate(out: bytes):
    fm, body = parse_note(RAW)
    cardfmt._check_patch(RAW, out, fm, body, False, False)


def test_a_patch_that_only_adds_passes_the_gate():
    out = cardfmt.patch(RAW, add_props=['Three.'], add_flashcards=[{'q': 'Q2?', 'a': 'A2.'}],
                        taken={'propositions': ['Three.'], 'flashcards': [{'q': 'Q2?', 'a': 'A2.'}]})
    assert b'- P3. Three.' in out and b'Q2?::A2.' in out
    gate(out)
    # what patch() wrote, read on its own (whether or not its own gate ran): every other byte is as it was
    before, after = parse_note(RAW), parse_note(out)
    assert before[0] == after[0]
    kept = {'命题', '复习卡'}
    assert {k: v for k, v in cardfmt.sections(before[1]).items() if k not in kept} == \
        {k: v for k, v in cardfmt.sections(after[1]).items() if k not in kept}
    assert out.startswith(RAW.split('- P2. Two.'.encode())[0])


@pytest.mark.parametrize('before, after, problem', [
    ('已有卡。', '已有卡，改了。', 'another section'),                 # the essence
    ('- 直觉：路灯。', '- 直觉：街灯。', 'another section'),            # intuition and confusions
    ('Q1?::A1.', 'Q1?::A1, changed.', 'a review card the card has'),   # an old review card
    ('title: Old card', 'title: New card', 'would change title'),     # front matter
])
def test_a_patch_that_changes_anything_else_is_refused(before, after, problem):
    with pytest.raises(cardfmt.PatchRefused, match=problem):
        gate(RAW.replace(before.encode('utf-8'), after.encode('utf-8')))


def card_with(props: int, flashcards: int):
    lines = '\n'.join(f'- P{i}. Proposition {i}.' for i in range(1, props + 1))
    qa = '\n'.join(f'Question {i}?::Answer {i}.' for i in range(1, flashcards + 1))
    return SimpleNamespace(body=f'## 本质\nx\n\n## 命题\n{lines}\n\n## 相关\n暂无。\n\n## 复习卡\n{qa}\n')


@pytest.mark.parametrize('props, flashcards, add_props, add_flashcards, fits', [
    (10, 2, 2, 0, True), (10, 2, 3, 0, False),     # 12 propositions at most
    (2, 7, 0, 1, True), (2, 7, 0, 2, False),       # 8 review cards at most
])
def test_a_patch_past_twelve_propositions_or_eight_review_cards_does_not_fit(props, flashcards, add_props,
                                                                             add_flashcards, fits):
    patch = {'propositions': [f'New proposition {i}.' for i in range(add_props)],
             'flashcards': [{'q': f'New question {i}?', 'a': 'A.'} for i in range(add_flashcards)]}
    problem, _, _ = ingest._patch_fits(card_with(props, flashcards), patch)
    assert (problem is None) is fits and (fits or problem == 'too_large')

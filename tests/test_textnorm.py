"""`same_text`: the same words whatever the spaces, punctuation, case and character forms; a symbol is a word."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from secondbrain.pipelines import ingest
from secondbrain.textnorm import plain, same_text


@pytest.mark.parametrize('a, b', [
    ('A writer lights a marker, every 8 items.', 'a writer lights a marker every 8 items'),  # case, spaces, commas
    ('ＡＢＣ　１２３', 'abc123'),                                                             # full width (NFKC)
    ('If a < b, swap them.', 'if a<b swap them'),                                              # spaces only
    ('“Quoted” — text…', 'quoted text'),                                                       # quotes and dashes
    ('字面相同：一样。', '字面相同一样'),                                                       # CJK punctuation
])
def test_spaces_punctuation_case_and_width_do_not_count(a, b):
    assert same_text(a, b)


@pytest.mark.parametrize('a, b', [
    ('If a < b, swap them.', 'If a > b, swap them.'),
    ('$x^2$ grows', '$x_2$ grows'),
    ('p ≤ q', 'p ≥ q'),
    ('C++', 'C#'),
    ('a+b', 'a-b'),
    ('a → b', 'a ← b'),
    ('Cost in $', 'Cost in €'),
    ('काम', 'कम'),                 # a vowel sign (combining mark) is part of the word
    ('A writer lights', 'A reader lights'),
])
def test_a_symbol_or_a_mark_makes_two_texts_different(a, b):
    assert not same_text(a, b)


def test_plain_drops_only_spaces_and_punctuation():
    assert plain(' A<b, (c)!\tD\n') == 'a<bcd'


OLD = SimpleNamespace(body='## 本质\n已有卡。\n\n## 命题\n- P1. If a < b, swap them.\n- P2. Keep the order.\n\n'
                           '## 直觉与易混对比\n- 直觉：路灯。\n\n## 相关\n暂无。\n\n## 复习卡\nWhen is a < b swapped?::Always.\n')


def test_a_patch_that_differs_from_the_card_only_in_a_symbol_is_not_dropped():
    patch = {'propositions': ['If a > b, swap them.', 'A new fact.', 'if a<b swap them'],
             'flashcards': [{'q': 'When is a > b swapped?', 'a': 'Never.'}]}
    problem, props, flashcards = ingest._patch_fits(OLD, patch)
    assert problem is None
    assert props == ['If a > b, swap them.', 'A new fact.']   # the third says P1 again, word for word
    assert [f['q'] for f in flashcards] == ['When is a > b swapped?']

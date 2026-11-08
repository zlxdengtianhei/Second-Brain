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
    ('“Quoted” — text…', 'quoted text'),                                                       # quotes, dash, ellipsis
    ('"Quoted" -- no.', '"quoted" -- no'),                                                     # the same ASCII marks
    ('字面相同：一样。', '字面相同一样'),                                                       # CJK sentence marks
    ('「引号」『书名』，顿号、分号；问号？叹号！', '引号书名顿号分号问号叹号'),                     # CJK quotes and marks
    ('Wait... what?!', 'wait what'),                                                           # three dots
    ('破折号——两个', '破折号两个'),                                                             # a double em dash
    ('Use $a + b$ here.', 'use $a  +  b$ here'),                                               # spaces inside a formula
])
def test_spaces_sentence_marks_quotes_case_and_width_do_not_count(a, b):
    assert same_text(a, b)


@pytest.mark.parametrize('a, b', [
    # decision D-15: only a closed table of sentence marks, quotes, ellipses and dashes is left out
    ('5%', '5'), ('C#', 'C'), ('-5', '5'), ('1/2', '12'), ('(a+b)c', 'a+bc'), ('a*b', 'ab'), ('x_1', 'x1'),
    ('a\\b', 'ab'), ('$n!$', '$n$'),
    # the judge's pairs (impl_judge2/probes/pk4.py) and the first verdict's
    ('The minimum is -5 degrees.', 'The minimum is 5 degrees.'), ('Use 1/2 of the tokens.', 'Use 12 of the tokens.'),
    ('Loss rises 5%.', 'Loss rises 5.'), ('Cost is 3*n.', 'Cost is 3n.'), ('C# is used.', 'C is used.'),
    ('$a-b$', '$ab$'), ('x−y', 'xy'), ('If a < b, swap them.', 'If a > b, swap them.'), ('$x^2$ grows', '$x_2$ grows'),
    ('$x_2$', '$x2$'), ('p ≤ q', 'p ≥ q'), ('C++', 'C#'), ('a+b', 'a-b'), ('a → b', 'a ← b'),
    ('Cost in $', 'Cost in €'), ('काम', 'कम'), ('A writer lights', 'A reader lights'),
    ('a@b', 'ab'), ('a&b', 'ab'), ('a~b', 'ab'), ('a=b', 'ab'), ('a|b', 'ab'), ('[a]', 'a'), ('《书》', '书'),
    ('3.5 points', '35 points'), ('ratio 3:2', 'ratio 32'),            # a mark between digits is part of the number
    ('$a, b$', '$a b$'), ('`f(x);`', '`f(x)`'), ('$X$', '$x$'),        # a formula or code is compared as written
])
def test_any_other_mark_makes_two_texts_different(a, b):
    assert not same_text(a, b)


def test_plain_drops_only_spaces_and_the_closed_table():
    assert plain(' A<b, (c)!\tD\n') == 'a<b(c)d'
    assert plain('Use $a  +\n b$ “here”…') == 'use$a + b$here'


OLD = SimpleNamespace(body='## 本质\n已有卡。\n\n## 命题\n- P1. If a < b, swap them.\n- P2. Keep the order.\n\n'
                           '## 直觉与易混对比\n- 直觉：路灯。\n\n## 相关\n暂无。\n\n## 复习卡\nWhen is a < b swapped?::Always.\n')


def test_pk4b_a_patch_that_differs_from_the_card_only_in_a_symbol_is_not_dropped():
    # the second verdict's probe (impl_judge2/probes/pk4b.py): all three go in
    old = SimpleNamespace(body='## 本质\n已有卡。\n\n## 命题\n- P1. Recall rises 5 points.\n- P2. Written in C.\n'
                               '- P3. The cost is 3n.\n\n## 直觉与易混对比\n- 直觉：路灯。\n\n## 相关\n暂无。\n\n'
                               '## 复习卡\nQ?::A.\n')
    patch = {'propositions': ['Recall rises 5% points.', 'Written in C#.', 'The cost is 3*n.', 'A new fact.'],
             'flashcards': []}
    assert ingest._patch_fits(old, patch) == (None, patch['propositions'], [])


def test_a_patch_that_differs_from_the_card_only_in_a_symbol_is_not_dropped():
    patch = {'propositions': ['If a > b, swap them.', 'A new fact.', 'if a<b swap them'],
             'flashcards': [{'q': 'When is a > b swapped?', 'a': 'Never.'}]}
    problem, props, flashcards = ingest._patch_fits(OLD, patch)
    assert problem is None
    assert props == ['If a > b, swap them.', 'A new fact.']   # the third says P1 again, word for word
    assert [f['q'] for f in flashcards] == ['When is a > b swapped?']


# --- D-15 ④: an English ! or ' right after a digit or a one-letter Latin name is part of it (n!, f', A') ----------
@pytest.mark.parametrize('a, b', [
    ('Factor is n!', 'Factor is n'), ("f'(x) is positive", 'f(x) is positive'),
    ("A' is the complement", 'A is the complement'), ('5! orders', '5 orders'), ("x' = 2x", 'x = 2x'),
])
def test_a_factorial_or_a_prime_is_part_of_its_name(a, b):
    assert not same_text(a, b)


@pytest.mark.parametrize('a, b', [
    ('Done!', 'Done'), ('“term”', 'term'), ('完成！', '完成'), ('Done！', 'Done'),
    ("Don't stop.", 'dont stop'), ("'term'", 'term'), ("It's fine!", 'its fine'),
])
def test_a_sentence_end_a_quote_or_an_apostrophe_in_a_word_still_does_not_count(a, b):
    assert same_text(a, b)

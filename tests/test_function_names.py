"""A function name in a formula stays a function name when the program adds its backslashes.

The formula pass gives a bare macro name its backslash (``alpha`` -> ``\\alpha``). ``sim`` is in that table for the
relation ``\\sim`` (``X \\sim p``), so a review card's answer written ``sim(v_q, v₊) > sim(v_q, v₋)`` (the similarity
function) went into the vault as ``\\sim(v_q, v₊) > \\sim(v_q, v₋)``: two tildes. The same happened to ``approx(``,
and ``sigmoid`` and ``relu`` became ``\\sigmoid`` and ``\\relu``, which MathJax does not know. Now a bare name of a
relation or operator symbol directly followed by ``(`` is written ``\\operatorname{name}``, and ``sigmoid`` and ``relu``
are operator names like ``softmax``; a symbol written with its backslash, or with no ``(`` right after it, stays.
"""
from __future__ import annotations

import pytest

from secondbrain.export import checks
from secondbrain.rendering import SYMBOL_FUNCTIONS, normalize_math

#: the review card's answer (an invented chunk's wording of the same shape: a similarity function on two pairs)
ANSWER = '相对关系：sim(v_q, v₊) > sim(v_q, v₋)，不要求绝对值。'

CHANGED = [
    (ANSWER, r'相对关系：$\operatorname{sim}(v_q, v₊) > \operatorname{sim}(v_q, v₋)$，不要求绝对值。'),
    ('S(q,d)=sum_i max_j sim(q_i,d_j)', r'$S(q,d)=\sum_i \max_j \operatorname{sim}(q_i,d_j)$'),
    ('z_i = approx(x_i)', r'$z_i = \operatorname{approx}(x_i)$'),
    ('门控 g_t = sigmoid(W_g x_t)', r'门控 $g_t = \operatorname{sigmoid}(W_g x_t)$'),
    ('h_t = relu(W_h x_t)', r'$h_t = \operatorname{relu}(W_h x_t)$'),
]

#: the symbols stay symbols
SYMBOLS = [
    (r'X \sim p', r'$X \sim p$'),
    ('x_t sim p', r'$x_t \sim p$'),
    ('a_i approx b_i', r'$a_i \approx b_i$'),
    (r'$x_t \sim p(x)$', r'$x_t \sim p(x)$'),
    (r'$\mathbb{E}_{X\sim p}[f(X)]$', r'$\mathbb{E}_{X\sim p}[f(X)]$'),
]


@pytest.mark.parametrize('line, expected', CHANGED + SYMBOLS)
def test_a_function_name_becomes_an_operator_name_and_a_symbol_stays_a_symbol(line, expected):
    out, _ = normalize_math(line)
    assert out == expected
    assert normalize_math(out) == (out, 0)  # a second pass changes nothing


def test_no_symbol_name_before_a_parenthesis_is_given_its_backslash():
    for name in SYMBOL_FUNCTIONS:
        out, _ = normalize_math(f'y_i = {name}(x_i)')
        assert f'\\operatorname{{{name}}}(x_i)' in out and f'\\{name}(' not in out, (name, out)


def real_checker():
    checker = checks.NodeMathJax.from_env()
    return checker if checker is not None and checks.preflight(checker) is None else None


@pytest.mark.skipif(real_checker() is None,
                    reason='needs node and mathjax-full (SB_MATHJAX_NODE_PATH=<folder>/node_modules)')
def test_mathjax_reads_every_changed_formula():
    formulas = [part for _, out in CHANGED for part in out.split('$')[1::2]]
    assert real_checker().check([(f, False) for f in formulas]) == [None] * len(formulas)


#: a binary operator before a group multiplies by it (or composes, adds): it stays the operator, `sim(` does not
OPERATORS = [
    ('a cdot(b)', 'a cdot(b)'),  # not read as a formula: left as written
    ('w_i times(x_i + b_i)', r'$w_i \times(x_i + b_i)$'),
    ('S = sum_i w_i times(x_i + b_i)', r'$S = \sum_i w_i \times(x_i + b_i)$'),
    ('y_i = a_i cdot(b_i)', r'$y_i = a_i \cdot(b_i)$'),
    ('h = sum_i f_i circ(g_i)', r'$h = \sum_i f_i \circ(g_i)$'),
    ('c_i = a_i oplus(b_i)', r'$c_i = a_i \oplus(b_i)$'),
    ('z_i = a_i otimes(b_i)', r'$z_i = a_i \otimes(b_i)$'),
    ('p_i = bullet(x_i)', r'$p_i = \bullet(x_i)$'),
    ('x_t sim(p)', r'$x_t \operatorname{sim}(p)$'),
]


@pytest.mark.parametrize('line, expected', OPERATORS)
def test_a_binary_operator_before_a_group_stays_the_operator(line, expected):
    out, _ = normalize_math(line)
    assert out == expected
    assert normalize_math(out) == (out, 0)

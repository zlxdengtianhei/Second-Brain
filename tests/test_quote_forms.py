"""A quote is taken when, once its marks are set to one form, it is in the chunk once; it then becomes the chunk's text.

Models copy a passage and write its quotation marks, dashes or punctuation in another form (a straight double quote
as a curly one or as a corner bracket, a full-width colon for a colon), or drop a list mark, a backtick or a `$`:
the quote was refused, and a model that kept doing it used its slot's budget up and its chunk was not written. Now
the quote and the chunk are compared in one form (:data:`secondbrain.pipelines.answerfix.DOUBLE_QUOTES` and the rest),
and a quote that is in the chunk exactly once in that form becomes the chunk's own text, recorded with the text
before and after. There is no fuzzy matching and no threshold: a quote that is there twice in that form, or not at
all, is refused as before. The texts are invented.
"""
from __future__ import annotations

import pytest

from secondbrain.pipelines import answerfix
from tests.test_answer_repair import CATALOG, card

CHUNK = ('她答"好的"，随后挂了电话。\n'
         '规则：A=1、B=2——编号从 1 开始。\n'
         '- 第一步：读入 `query` 向量\n'
         '- 第二步：按 $\\\\cos$ 排序\n'
         'The writer\'s marker – the one lit last – stays lit.')


def snapped(quote, text=CHUNK):
    out, records = answerfix.repair({'cards': [card('x', source_quotes=[quote])]},
                                    answerfix.Context(text=text, catalog=CATALOG, prefix='kc_'))
    return out['cards'][0]['source_quotes'][0], records


@pytest.mark.parametrize('quote, slice_', [
    ('她答“好的”，随后挂了电话', '她答"好的"，随后挂了电话'),                # curly double quotes
    ('她答「好的」，随后挂了电话', '她答"好的"，随后挂了电话'),                # corner brackets
    ('她答＂好的＂，随后挂了电话', '她答"好的"，随后挂了电话'),                # full-width quote
    ('规则:A=1、B=2——编号从 1 开始', '规则：A=1、B=2——编号从 1 开始'),      # a colon for a full-width one
    ('规则：A=1、B=2--编号从 1 开始', '规则：A=1、B=2——编号从 1 开始'),      # hyphens for a dash
    ('第一步：读入 query 向量', '第一步：读入 `query` 向量'),                # a backtick dropped
    ('第一步：读入 `query` 向量\n第二步：按 \\cos 排序',                      # a list mark, `$`, a doubled backslash
     '第一步：读入 `query` 向量\n- 第二步：按 $\\\\cos$ 排序'),
    ("The writer’s marker — the one lit last — stays lit", "The writer's marker – the one lit last – stays lit"),
])
def test_a_quote_in_another_form_of_its_marks_becomes_the_chunks_text(quote, slice_):
    out, [record] = snapped(quote)
    assert out == slice_ and slice_ in CHUNK
    assert record == {'rule': 'quote', 'card': 'x', 'before': quote, 'after': slice_}


def test_two_passages_that_differ_only_in_their_quotation_marks_are_not_taken_for_one():
    text = '她答"好的"。后来她答「好的」。'
    out, records = snapped('她答“好的”', text)
    assert out == '她答“好的”' and records == []


def test_single_and_double_quotation_marks_are_two_kinds():
    """A chunk that writes a word in single quotes only: a quote that puts it in double quotes is not its text."""
    text = "她答'好的'，随后挂了电话。"
    out, records = snapped('她答“好的”，随后挂了电话', text)
    assert out == '她答“好的”，随后挂了电话' and records == []
    out, [record] = snapped('她答‘好的’，随后挂了电话', text)  # curly single quotes are single quotes
    assert out == "她答'好的'，随后挂了电话" and record['after'] == out


def test_a_sentence_the_chunk_has_twice_is_not_taken():
    text = '规则：先读"甲"，再读"乙"。中间隔了一段话。规则：先读"甲"，再读"乙"。'
    out, records = snapped('规则：先读“甲”，再读“乙”', text)
    assert out == '规则：先读“甲”，再读“乙”' and records == []


def test_a_quote_the_chunk_does_not_have_in_any_form_is_left_to_the_check():
    out, records = snapped('她答“不行”，随后挂了电话')
    assert out == '她答“不行”，随后挂了电话' and records == []


def test_in_a_job_such_an_answer_is_taken_with_its_repairs_recorded(tmp_path):
    """A quote with backticks the chunk does not have, an overlap hint naming a card of this answer, and a coverage
    row naming a card of another chunk next to its own: the answer is taken, each repair recorded."""
    import copy

    from secondbrain import jobs
    from secondbrain.executors import FakeExecutor
    from secondbrain.pipelines import ingest
    from tests.ingest_helpers import first_candidate
    from tests.test_answer_repair import accept_all, lantern_job
    root, job_id, _ = lantern_job(tmp_path)
    answer = copy.deepcopy(first_candidate())
    first = answer['cards'][0]
    quote = first['source_quotes'][0]
    first['source_quotes'] = [quote.replace('lantern queue', '`lantern queue`', 1)]
    first['overlap_hint'] = {'overlap': 'extends', 'with': [answer['cards'][1]['slug']]}
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=lambda t: copy.deepcopy(answer)),
                          reviewer=FakeExecutor('claude-test', responder=accept_all))
    assert done['status'] == 'applied', done.get('pending')
    state = jobs.load(root, job_id)
    taken = jobs.artifact(root, state, 'candidate', 'c01')
    assert taken['cards'][0]['source_quotes'] == [quote] and 'overlap_hint' not in taken['cards'][0]
    assert {r['rule'] for r in taken['program_repairs']} >= {'quote', 'overlap_hint'}

"""The name repairs rewrite a card id or a renamed title once, inside its bounds, and the guard's form is stable.

The seventh review found the edges of the name repairs: a card id written as a link target (``[[kc_x]]``) became four
brackets; an id followed by more of an identifier (``kc_x_size``) was cut; of two renamed titles where one begins the
other (``A/B`` and ``A/B#C``), a link to the longer one went to the shorter; and the guard's written form was not
stable when a title itself holds an id (``Notes on kc_lantern``), so a lawful answer was taken for a changed text.
Now one scan finds links and ids together: an id inside a link is part of the link, a link whose target is an id
becomes a link to the title, an id stands only between non-word characters, and renamed titles are tried longest
first; what a scan writes is not scanned again. The cards and titles are invented.
"""
from __future__ import annotations

import copy

import pytest

from secondbrain import jobs
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import answerfix, ingest
from tests.ingest_helpers import first_candidate, scripted
from tests.test_answer_repair import CATALOG, TEXT, accept_all, card, lantern_job


def ctx(**kw):
    base = dict(text=TEXT, catalog=CATALOG, prefix='kc_', title_of={'kc_old-card': 'Old card'}, targets={'kc_old-card'})
    return answerfix.Context(**{**base, **kw})


def text_of(out, slug, field):
    return next(c for c in out['cards'] if c['slug'] == slug)[field]


CASES = {
    # a title that holds an id is not rewritten again once it is written in
    'a title holding an id': (
        {'cards': [card('lantern', title='Lantern queue'), card('notes', title='Notes on kc_lantern'),
                   card('order', propositions=['kc_notes explains it.'])]}, {},
        ('order', 'propositions', ['[[Notes on kc_lantern]] explains it.'])),
    'a library title holding an id': (
        {'cards': [card('order', propositions=['kc_old-card keeps order.'])]},
        {'title_of': {'kc_old-card': 'Old card vs kc_lantern', 'kc_lantern': 'Lantern queue'}},
        ('order', 'propositions', ['[[Old card vs kc_lantern]] keeps order.'])),
    'an id as a link target': (
        {'cards': [card('lantern', title='Lantern queue'),
                   card('order', propositions=['See [[kc_lantern]] and [[kc_lantern|the queue]].'])]}, {},
        ('order', 'propositions', ['See [[Lantern queue]] and [[Lantern queue|the queue]].'])),
    'an id that begins a longer identifier': (
        {'cards': [card('lantern', title='Lantern queue'),
                   card('order', propositions=['The field kc_lantern_size counts items; xkc_lantern too.'])]}, {},
        ('order', 'propositions', ['The field kc_lantern_size counts items; xkc_lantern too.'])),
    'renamed titles where one begins the other': (
        {'cards': [card('ab', title='A/B'), card('abc', title='A/B#C'),
                   card('order', essence='See [[A/B#C]] and [[A/B]] and [[A/B#Usage]].')]}, {},
        ('order', 'essence', 'See [[A／B＃C]] and [[A／B]] and [[A／B#Usage]].')),
}


@pytest.mark.parametrize('name', sorted(CASES))
def test_each_name_is_rewritten_once_within_its_bounds_and_the_guard_takes_it(name):
    answer, fields, (slug, field, expected) = CASES[name]
    out, _ = answerfix.repair(copy.deepcopy(answer), ctx(**fields))  # no RepairFault
    assert text_of(out, slug, field) == expected
    assert answerfix.repair(out, ctx(**fields))[1] == []  # a second pass has nothing left to do


@pytest.mark.parametrize('name', sorted(CASES))
def test_the_guards_written_form_is_stable(name):
    answer, fields, _ = CASES[name]
    canon = answerfix._canonical(answer, ctx(**fields))
    for c in answer['cards']:
        for value in [c['essence'], *c['propositions']]:
            assert canon(canon(value)) == canon(value)


def test_in_a_job_an_id_written_as_a_link_target_becomes_a_link_to_the_title(tmp_path):
    root, job_id, prefix = lantern_job(tmp_path)
    answer = copy.deepcopy(first_candidate())
    queue = next(c for c in answer['cards'] if c['slug'] == 'lantern-queue')
    title = next(c for c in answer['cards'] if c['slug'] == 'lit-marker-cost')['title']
    queue['confusions'] = [f'Unlike [[{prefix}lit-marker-cost]], it says nothing of memory.']
    produce, review, _ = scripted([answer], [accept_all])
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=review))
    assert done['status'] == 'applied'
    cand = jobs.artifact(root, jobs.load(root, job_id), 'candidate', 'c01')
    assert next(c for c in cand['cards'] if c['slug'] == 'lantern-queue')['confusions'] == \
        [f'Unlike [[{title}]], it says nothing of memory.']

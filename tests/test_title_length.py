"""A card's title is limited by the bytes its file name takes, not only by its characters.

A card is written as ``{title}.md``, and a file name holds 255 bytes on the common file systems. The title was
limited to 120 characters, so a Chinese title of 100 characters (300 bytes) passed the checks and then could not be
written: the job stopped at the write, every card of it waiting for the owner. Now a title may take at most
``cards.MAX_TITLE_BYTES`` bytes in UTF-8 (200: 66 Chinese or Japanese characters) besides the 120 characters, and a
longer one is the answer's error: the card is asked for again, shorter. The titles are invented.
"""
from __future__ import annotations

import copy

import pytest

from secondbrain import cards, jobs
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest
from secondbrain.service import Service
from tests.ingest_helpers import first_candidate, scripted
from tests.test_answer_repair import accept_all, lantern_job

ZH, JA = '灯笼队列按到达顺序保留请求', 'ランタンの待ち行列'


@pytest.mark.parametrize('title, taken', [
    ((ZH * 6)[:66], True), ((ZH * 6)[:67], False),                    # Chinese: 198 and 201 bytes
    ((JA * 8)[:66], True), ((JA * 8)[:67], False),                    # Japanese: the same
    (('Lantern queue order ' * 7)[:119] + 's', True),                  # English: 120 characters
    (('Lantern queue order ' * 7)[:120] + 's', False),                 # English: 121 characters
])
def test_a_title_is_taken_up_to_the_limit_and_refused_past_it(title, taken):
    problem = cards.title_problem(title)
    assert (problem is None) is taken, (len(title), len(title.encode()), problem)


def test_the_limit_counts_bytes_for_a_title_of_mixed_scripts():
    assert cards.title_problem('灯' * 60 + 'a' * 20) is None          # 200 bytes
    assert 'more than 200' in cards.title_problem('灯' * 60 + 'a' * 21)  # 201 bytes
    assert cards.MAX_TITLE_BYTES + len('.md') < 255


def test_in_a_job_a_card_with_too_long_a_title_is_asked_for_again_shorter(tmp_path):
    root, job_id, _ = lantern_job(tmp_path)
    answer = copy.deepcopy(first_candidate())
    queue = next(c for c in answer['cards'] if c['slug'] == 'lantern-queue')
    queue['title'] = (ZH * 8)[:100]                                    # 300 bytes: no file can bear it
    asked = []

    def again(task):
        asked.append(task.packet['fix']['problems'])
        card = copy.deepcopy(task.packet['fix']['cards'][0])
        card['title'] = '灯笼队列'
        return {'cards': [card]}
    produce, review, _ = scripted([answer, again], [accept_all])
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=review))
    assert done['status'] == 'applied', done.get('pending')
    [problems] = asked
    assert 'title takes 300 bytes as a file name' in problems['lantern-queue'][0]
    assert not [i for i in Service(root).owner_items() if i.get('kind') == 'program_error']
    assert list(root.rglob('灯笼队列.md'))
    assert ingest._refusals(jobs.load(root, job_id)['chunks']['c01'], ingest.ANSWER_ROLES[0]) == 1

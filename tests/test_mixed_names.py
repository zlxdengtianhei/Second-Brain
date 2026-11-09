"""A card that names another card both by ``[[title]]`` and by its id is repaired, not taken for a changed text.

The repair guard compared a card's knowledge text before and after the repairs by turning every ``[[title]]`` of the
repaired text back into the id it was repaired from. A ``[[title]]`` the answer already had was turned back too, so a
card that named one card by title in one place and by id in another (an answer the repairs should fix) stopped the
job: the API job failed after its retries, and in host mode the run could be submitted for ever. Now both sides are
written in one form (an id that names a card as ``[[title]]``, a title full width) before they are compared, and
nothing is turned back. The three cases are the sixth review's probes; the cards and the source are invented.
"""
from __future__ import annotations

import copy
import json

from secondbrain import cli, jobs
from secondbrain.executors import FakeExecutor
from secondbrain.executors.host import HostExecutor
from secondbrain.pipelines import answerfix, ingest
from tests.ingest_helpers import first_candidate, scripted
from tests.test_answer_repair import CATALOG, TEXT, accept_all, card, lantern_job


def ctx():
    return answerfix.Context(text=TEXT, catalog=CATALOG, prefix='kc_', title_of={'kc_old-card': 'Old card'},
                             targets={'kc_old-card'})


def test_a_card_named_by_title_in_one_place_and_by_id_in_another_is_repaired():
    answer = {'cards': [card('lantern', title='Lantern queue'),
                        card('order', confusions=['Unlike [[Lantern queue]], it keeps no markers.'],
                             propositions=['kc_lantern keeps arrival order too.'])]}
    out, records = answerfix.repair(answer, ctx())
    assert out['cards'][1]['propositions'] == ['[[Lantern queue]] keeps arrival order too.']
    assert out['cards'][1]['confusions'] == ['Unlike [[Lantern queue]], it keeps no markers.']
    assert [(r['rule'], r['card'], r['before'], r['after']) for r in records] == \
        [('card_id', 'order', 'kc_lantern', '[[Lantern queue]]')]


def test_a_library_card_named_by_title_in_the_text_and_by_id_in_a_relation_note_is_repaired():
    answer = {'cards': [card('order', essence='Like [[Old card]], it keeps order.',
                             relations=[{'type': 'similar_to', 'target': 'kc_old-card',
                                         'note': 'kc_old-card keeps order too'}])]}
    out, records = answerfix.repair(answer, ctx())
    assert out['cards'][0]['essence'] == 'Like [[Old card]], it keeps order.'
    assert out['cards'][0]['relations'][0]['note'] == '[[Old card]] keeps order too'
    assert [r['rule'] for r in records] == ['card_id']


def test_a_renamed_title_named_both_ways_is_repaired_too():
    answer = {'cards': [card('queues', title='Queues / stacks'),
                        card('order', essence='See [[Queues / stacks]].', propositions=['kc_queues holds order.'])]}
    out, records = answerfix.repair(answer, ctx())
    assert out['cards'][1]['essence'] == 'See [[Queues ／ stacks]].'
    assert out['cards'][1]['propositions'] == ['[[Queues ／ stacks]] holds order.']
    assert sorted(r['rule'] for r in records) == ['card_id', 'title']


def mixed(prefix):
    answer = copy.deepcopy(first_candidate())
    cards = {c['slug']: c for c in answer['cards']}
    title = cards['lit-marker-cost']['title']
    cards['lantern-queue']['confusions'] = [f'Unlike [[{title}]], it says nothing of memory.',
                                            f'{prefix}lit-marker-cost is about memory, not order.']
    return answer, title


def test_in_a_job_the_mixed_answer_is_taken_and_its_cards_are_written(tmp_path):
    root, job_id, prefix = lantern_job(tmp_path)
    answer, title = mixed(prefix)
    produce, review, seen = scripted([answer], [accept_all])
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=review))
    assert done['status'] == 'applied' and done['pending'] == [] and len(seen['producer']) == 1
    assert set(done['outcomes']) == {c['slug'] for c in answer['cards']}
    [rv] = seen['reviewer']
    queue = next(c for c in rv.packet['candidate']['cards'] if c['slug'] == 'lantern-queue')
    assert queue['confusions'] == [f'Unlike [[{title}]], it says nothing of memory.',
                                   f'[[{title}]] is about memory, not order.']
    assert [r['rule'] for r in done['program_repairs']] == ['card_id']


def test_in_host_mode_the_mixed_answer_is_taken_on_its_first_submission(tmp_path):
    root, job_id, prefix = lantern_job(tmp_path)
    answer, _ = mixed(prefix)
    host = HostExecutor()
    out = ingest.advance(root, job_id, producer=host, reviewer=host)
    [h] = [x for x in out['handoffs'] if x['role'] == 'producer']
    path = tmp_path / 'answer.json'
    path.write_text(json.dumps(answer), encoding='utf-8')
    assert cli.main(['--vault', str(root), 'job', 'submit', job_id, '--role', 'producer', '--run-id', h['run_id'],
                     '--file', str(path), '--model', 'gpt-test']) == 0
    run = jobs.load_run(root, job_id, h['run_id'])
    info = jobs.load(root, job_id)['chunks']['c01']
    assert run['status'] == 'submitted' and ingest._refusals(info, ingest.ANSWER_ROLES[0]) == 0

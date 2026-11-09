"""A producer's answer is repaired where the program can, kept where it passes, and asked again only where it fails.

Before, one mechanical slip anywhere in a chunk's answer (two links between one pair of cards, a quote whose bold
marks differ from the source, a ``/`` in a title, a card named by its id) refused the whole answer, and the next ask
carried the error lines but not the answer. Now :mod:`secondbrain.pipelines.answerfix` makes the fixes that write no
knowledge text, by a table of rules, and records each; a card that still fails is refused alone (one refusal against
the slot's budget, however many cards), asked for again with its lines, and merged back; the slot's last answer keeps
the cards that pass and leaves the others to the owner. Host and API mode take an answer the same way.

The source is the invented lantern-queue fixture (tests/fixtures/ingest/lantern_queues.md); every card, quote and
link here is invented.
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

from secondbrain import cli, config, jobs, schemas
from secondbrain.config import ReviewPolicy
from secondbrain.executors import FakeExecutor
from secondbrain.executors.api import read_answer
from secondbrain.executors.host import HostExecutor
from secondbrain.pipelines import answerfix, ingest
from secondbrain.service import Service
from tests.ingest_helpers import CARDS, SOURCE_NAME, build_ingest_vault, first_candidate, first_review, packet_in, \
    scripted
from tests.test_ingest import settings

# --- the rules, one synthetic case each ---------------------------------------------------------------------------------
CATALOG = {'relation_types': ['has_part', 'part_of', 'depends_on', 'required_by', 'similar_to'],
           'inverse_relations': {'has_part': 'part_of', 'part_of': 'has_part', 'depends_on': 'required_by',
                                 'required_by': 'depends_on', 'similar_to': 'similar_to'}}
TEXT = ('A **lantern queue** keeps every item in arrival order.\n\n'
        'A writer lights a marker every eight items, and a reader jumps to it.')


def ctx(**kw):
    return answerfix.Context(text=TEXT, catalog=CATALOG, prefix='kc_', **kw)


def card(slug, **kw):
    return {'slug': slug, 'title': slug.replace('-', ' ').capitalize(), 'essence': f'The {slug} essence.',
            'propositions': [f'The {slug} proposition.'], 'intuition': None, 'confusions': [],
            'flashcards': [{'q': 'Which?', 'a': 'This.'}, {'q': 'Why?', 'a': 'Because.'}],
            'source_quotes': [], 'relations': [], **kw}


def rel(kind, target):
    return {'type': kind, 'target': target, 'note': 'n'}


#: rule -> (answer, context fields, what the repaired answer must say)
CASES = {
    'title': ({'cards': [card('queues', title='Queues / stacks'), card('other', essence='See [[Queues / stacks]].')]},
              {}, lambda out: out['cards'][0]['title'] == 'Queues ／ stacks'
              and out['cards'][1]['essence'] == 'See [[Queues ／ stacks]].'),
    'card_id': ({'cards': [card('lantern', title='Lantern queue'),
                           card('order', confusions=['Unlike kc_lantern, it keeps no markers.',
                                                     'kc_old-card says the same.', 'kc_nowhere stays.'])]},
                {'title_of': {'kc_old-card': 'Old card'}},
                lambda out: out['cards'][1]['confusions'] == ['Unlike [[Lantern queue]], it keeps no markers.',
                                                              '[[Old card]] says the same.', 'kc_nowhere stays.']),
    'quote': ({'cards': [card('lantern', source_quotes=['lantern queue keeps every item',  # lost the source's **
                                                        'A writer **lights** a marker every eight items'])],
               'coverage': [{'source_quote': 'a reader   jumps to it', 'disposition': 'card', 'cards': ['lantern']}]},
              {}, lambda out: out['cards'][0]['source_quotes'] == ['**lantern queue** keeps every item',
                                                                  'A writer lights a marker every eight items']
              and out['coverage'][0]['source_quote'] == 'a reader jumps to it'),
    'relation_target': ({'cards': [card('lantern', relations=[rel('similar_to', 'ghost'), rel('similar_to', 'kc_old'),
                                                              rel('part_of', 'order')]), card('order')]},
                        {'targets': {'kc_old'}},
                        lambda out: [r['target'] for r in out['cards'][0]['relations']] == ['kc_old', 'order']),
    'relation_pair': ({'cards': [card('lantern', relations=[rel('depends_on', 'order'), rel('has_part', 'order')]),
                                 card('order', relations=[rel('similar_to', 'lantern')])]},
                      {}, lambda out: out['cards'][0]['relations'] == [rel('has_part', 'order')]
                      and out['cards'][1]['relations'] == []),
    'relation_direction': ({'cards': [card('lantern', relations=[rel('part_of', 'order'), rel('has_part', 'order')]),
                                      card('order'), card('reader', relations=[rel('depends_on', 'writer')]),
                                      card('writer', relations=[rel('depends_on', 'reader')])]},
                           {}, lambda out: out['cards'][0]['relations'] == [rel('has_part', 'order')]
                           and out['cards'][2]['relations'] == [rel('depends_on', 'writer')]
                           and out['cards'][3]['relations'] == []),
}


def test_every_rule_of_the_table_has_a_case():
    assert sorted(CASES) == sorted(r.name for r in answerfix.RULES)


@pytest.mark.parametrize('rule', sorted(CASES))
def test_each_rule_repairs_its_slip_records_it_and_leaves_the_answer_given_as_it_was(rule):
    answer, fields, holds = CASES[rule]
    given = copy.deepcopy(answer)
    out, records = answerfix.repair(answer, ctx(**fields))
    assert holds(out), out
    assert records and {r['rule'] for r in records} == {rule}
    assert answer == given  # a copy is repaired
    assert answerfix.repair(out, ctx(**fields))[1] == []  # nothing left to repair


def test_a_quote_not_in_the_chunk_or_too_short_or_there_twice_is_left_to_the_check():
    for quote in ('a reader skips to it', 'marker'):
        out, records = answerfix.repair({'cards': [card('x', source_quotes=[quote])]}, ctx())
        assert out['cards'][0]['source_quotes'] == [quote] and records == []
    twice = answerfix.Context(text='a lit marker here; a **lit** marker there', catalog=CATALOG, prefix='kc_')
    for quote in ('a  lit marker', 'a **lit marker**'):  # not in the chunk as written; twice once marks and blanks go
        assert quote not in twice.text
        assert answerfix.repair({'cards': [card('x', source_quotes=[quote])]}, twice)[1] == []


def test_a_repair_that_changes_a_proposition_is_a_fault_of_the_program(monkeypatch):
    """The mutation the guard is for: a rule that writes knowledge text stops the answer, never passes it on."""
    def rewrite(payload, ctx):
        payload['cards'][0]['propositions'][0] = 'Items leave in any order.'
        return [{'rule': 'quote', 'card': 'x', 'before': 'b', 'after': 'a'}]
    monkeypatch.setattr(answerfix, 'RULES', answerfix.RULES + (answerfix.Rule('evil', 'x', rewrite),))
    with pytest.raises(answerfix.RepairFault):
        answerfix.repair({'cards': [card('x')]}, ctx())


@pytest.mark.parametrize('text, inner', [
    ('```json\n{"cards": []}\n```', '{"cards": []}'),
    ('  ```\n{"cards": []}\n```\n', '{"cards": []}'),
    ('```JSON\r\n{"a": "x\\ny"}\r\n```', '{"a": "x\\ny"}'),
    ('{"cards": []}', None),                              # no fence
    ('Here it is:\n```json\n{"cards": []}\n```', None),   # text outside the fence: not loosened
    ('```json\\n{"cards": []}\\n```', None),              # a literal \n is not a line break
])
def test_one_code_fence_around_the_whole_answer_is_taken_off_and_nothing_else(text, inner):
    assert answerfix.strip_fence(text) == inner


def test_an_answer_written_with_a_literal_backslash_n_is_still_no_json():
    assert read_answer('```json\\n{\\n"cards": []\\n}\\n```')[0] is None


# --- in a job: the lantern-queue fixture, one chunk -----------------------------------------------------------------
def lantern_job(tmp_path, retries=None):
    root = build_ingest_vault(tmp_path / 'vault')
    settings()
    if retries is not None:
        config.set_review_policy(replace(ReviewPolicy(), max_invalid_retries=retries))
    job_id = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)['job_id']
    return root, job_id, jobs.read_input(root, job_id)['settings']['id_prefix']


#: a quote the chunk does not have: a knowledge error the program cannot repair
BAD_QUOTES = {'marker-dimming': 'markers before the cut are deleted outright',
              'queue-order': 'a lantern queue sorts its items by key'}


def slipped(prefix, bad=('marker-dimming',)):
    """The first answer of tests/ingest_helpers.py with four mechanical slips, and ``bad`` cards quoting what the
    chunk does not say."""
    answer = copy.deepcopy(first_candidate())
    cards = {c['slug']: c for c in answer['cards']}
    cards['lit-marker-cost']['title'] = 'Lit marker / memory cost'
    cards['lantern-queue']['source_quotes'] = ['A **lantern queue** keeps every item in arrival order but lets a '
                                               'reader skip ahead by lit markers.']
    cards['lantern-queue']['confusions'] = [f'Unlike {prefix}lit-marker-cost, it says nothing of memory.']
    cards['lit-marker-cost']['relations'].append({'type': 'similar_to', 'target': 'lantern-queue', 'note': 'n'})
    for slug in bad:
        cards[slug]['source_quotes'] = [BAD_QUOTES[slug]]
    return answer


def fixed(slug):
    return {'cards': [copy.deepcopy(CARDS[slug])]}


def accept_all(task):
    review = first_review()
    for row in review['cards']:
        if row['decision'] != 'accept':
            row.update(decision='accept', overlap='distinct', reason='ok')
    shown = {c['slug'] for c in task.packet['candidate']['cards']}
    review['cards'] = [r for r in review['cards'] if r['slug'] in shown]
    return review


def test_a_card_that_fails_is_asked_for_alone_with_its_lines_and_merged_back(tmp_path):
    root, job_id, prefix = lantern_job(tmp_path)
    asked = []

    def again(task):
        asked.append(task)
        return fixed('marker-dimming')
    produce, review, seen = scripted([slipped(prefix), again], [accept_all])
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=review))
    assert done['status'] == 'applied', done.get('pending')
    first, second = seen['producer']
    assert first.output_schema == schemas.CARDS and 'fix' not in first.packet
    [task] = asked
    assert task.output_schema == schemas.CARD_FIX
    assert '## Some cards of your previous answer failed the program\'s checks' in task.prompt
    fix = task.packet['fix']
    assert [c['slug'] for c in fix['cards']] == ['marker-dimming']
    assert fix['cards'][0]['source_quotes'] == [BAD_QUOTES['marker-dimming']]  # as it was given
    [line] = fix['problems']['marker-dimming']
    assert line.startswith('cards[marker-dimming].source_quotes')
    assert fix['kept'] == ['lantern-queue', 'lit-marker-cost', 'queue-order']
    # one refusal, for the one card; the run's reason names it and its line
    info = jobs.load(root, job_id)['chunks']['c01']
    assert ingest._refusals(info, ingest.ANSWER_ROLES[0]) == 1
    assert 'Answer again with these cards only, fixed: marker-dimming' in info['last_validation_error']
    # the reviewer judged the four cards, repaired, and saw what the program changed
    [rv] = seen['reviewer']
    cand = rv.packet['candidate']
    assert sorted(c['slug'] for c in cand['cards']) == sorted(CARDS)
    assert 'program_repairs' not in cand and 'held_cards' not in cand
    by = {c['slug']: c for c in cand['cards']}
    assert by['lit-marker-cost']['title'] == 'Lit marker ／ memory cost'
    assert by['lantern-queue']['confusions'] == ['Unlike [[Lit marker ／ memory cost]], it says nothing of memory.']
    assert by['lantern-queue']['source_quotes'] == CARDS['lantern-queue']['source_quotes']
    assert by['marker-dimming'] == {k: v for k, v in CARDS['marker-dimming'].items() if k != 'reason'}  # withheld
    rules = [r['rule'] for r in rv.packet['program_repairs']]
    assert sorted(set(rules)) == ['card_id', 'quote', 'relation_pair', 'title']
    assert [{k: v for k, v in r.items() if k != 'slot'} for r in done['program_repairs']] == \
        rv.packet['program_repairs'] and {r['slot'] for r in done['program_repairs']} == {'c01'}
    for c in cand['cards']:  # no knowledge text changed but for the id named by its title
        if c['slug'] != 'lantern-queue':
            assert {k: c.get(k) for k in answerfix.KNOWLEDGE} == {k: CARDS[c['slug']].get(k)
                                                                 for k in answerfix.KNOWLEDGE}


def test_two_failing_cards_cost_one_refusal(tmp_path):
    root, job_id, prefix = lantern_job(tmp_path)
    asked = []

    def again(task):
        asked.append(sorted(task.packet['fix']['problems']))
        return {'cards': fixed('marker-dimming')['cards'] + fixed('queue-order')['cards']}
    produce, review, _ = scripted([slipped(prefix, bad=('marker-dimming', 'queue-order')), again], [accept_all])
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=review))
    assert done['status'] == 'applied' and asked == [['marker-dimming', 'queue-order']]
    assert ingest._refusals(jobs.load(root, job_id)['chunks']['c01'], ingest.ANSWER_ROLES[0]) == 1


def test_a_problem_of_the_whole_chunk_still_refuses_the_whole_answer(tmp_path):
    root, job_id, prefix = lantern_job(tmp_path)
    whole = slipped(prefix)
    whole['coverage'][0]['source_quote'] = 'a sentence the chunk does not have at all'
    answers = []

    def again(task):
        answers.append(task)
        return slipped(prefix, bad=())
    produce, review, _ = scripted([whole, again], [accept_all])
    ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                   reviewer=FakeExecutor('claude-test', responder=review))
    [task] = answers
    assert 'fix' not in task.packet and task.output_schema == schemas.CARDS
    assert '## Your previous answer was rejected by the validator' in task.prompt


def test_the_last_answer_keeps_the_cards_that_pass_and_the_others_wait_for_the_owner(tmp_path):
    root, job_id, prefix = lantern_job(tmp_path, retries=0)
    produce, review, seen = scripted([slipped(prefix)], [accept_all])
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=review))
    [rv] = seen['reviewer']
    assert sorted(c['slug'] for c in rv.packet['candidate']['cards']) == ['lantern-queue', 'lit-marker-cost',
                                                                          'queue-order']
    [held] = rv.packet['held_cards']
    assert held['slug'] == 'marker-dimming' and held['problems'][0].startswith('cards[marker-dimming].source_quotes')
    assert {'rule': 'held_back', 'coverage': 2} == {k: v for r in rv.packet['program_repairs']
                                                     if r['rule'] == 'held_back' and 'coverage' in r
                                                     for k, v in r.items() if k != 'dropped'}
    [pending] = [p for p in done['pending'] if p.get('slug') == 'marker-dimming']
    assert 'failed the program\'s checks' in pending['reason'] and 'cards[marker-dimming].source_quotes' in pending['reason']
    assert done['outcomes']['lit-marker-cost'] == 'write' and 'marker-dimming' not in done['outcomes']


def test_a_repair_that_writes_knowledge_text_is_never_taken_and_is_counted_as_a_program_error(tmp_path, monkeypatch):
    root, job_id, prefix = lantern_job(tmp_path)

    def rewrite(payload, ctx):
        for c in payload.get('cards') or []:
            c['propositions'] = [p.replace('arrival', 'any') for p in c['propositions']]
        return []
    monkeypatch.setattr(answerfix, 'RULES', answerfix.RULES + (answerfix.Rule('evil', 'x', rewrite),))
    limit = ingest.ANSWER_ROLES[0].retries(ReviewPolicy()) + 1
    produce, review, seen = scripted([slipped(prefix, bad=())] * limit, [accept_all])
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=review))
    info = jobs.load(root, job_id)['chunks'].get('c01') or {}
    assert not info.get('candidate') and ingest._refusals(info, ingest.ANSWER_ROLES[0]) == limit
    assert len(seen['producer']) == limit and seen['reviewer'] == []
    assert 'the program failed while checking this answer (RepairFault' in done['error']['pending'][0]['reason']
    [item] = [i for i in Service(root).owner_items() if i.get('kind') == 'program_error']
    assert (item['job_id'], item['slot'], item['step'], item['error'], item['times']) == \
        (job_id, 'c01', 'produce', 'RepairFault', limit)


# --- host mode takes an answer the same way ------------------------------------------------------------------------------
def host_answers(root, job_id, answers, tmp_path):
    """Hand the chunk's producer run over, answer it with ``answers`` in turn (through ``sb job submit``), and
    return the refusal messages and the candidate."""
    host = HostExecutor()
    out = ingest.advance(root, job_id, producer=host, reviewer=host)
    [h] = [x for x in out['handoffs'] if x['role'] == 'producer']
    messages = []
    for i, answer in enumerate(answers):
        path = tmp_path / f'answer{i}.json'
        path.write_text(answer if isinstance(answer, str) else json.dumps(answer), encoding='utf-8')
        code = cli.main(['--vault', str(root), 'job', 'submit', job_id, '--role', 'producer', '--run-id',
                         h['run_id'], '--file', str(path), '--model', 'gpt-test'])
        if code:
            messages.append(jobs.load(root, job_id)['chunks']['c01']['last_validation_error'])
    state = jobs.load(root, job_id)
    return messages, jobs.artifact(root, state, 'candidate', 'c01'), jobs.load_run(root, job_id, h['run_id'])


def test_host_and_api_mode_refuse_keep_and_merge_alike(tmp_path):
    root, job_id, prefix = lantern_job(tmp_path / 'api')
    produce, review, _ = scripted([slipped(prefix), fixed('marker-dimming')], [accept_all])
    ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                   reviewer=FakeExecutor('claude-test', responder=review))
    api_state = jobs.load(root, job_id)
    api_candidate = jobs.artifact(root, api_state, 'candidate', 'c01')
    api_message = jobs.load_run(root, job_id, next(r['run_id'] for r in jobs.list_runs(root, job_id)
                                                   if r.get('rejected')))['rejected']

    hroot, hjob, hprefix = lantern_job(tmp_path / 'host')
    assert hprefix == prefix
    messages, candidate, run = host_answers(hroot, hjob, [slipped(prefix), fixed('marker-dimming')], tmp_path)
    assert messages == api_message
    assert candidate == api_candidate
    info = jobs.load(hroot, hjob)['chunks']['c01']
    assert ingest._refusals(info, ingest.ANSWER_ROLES[0]) == 1 and not info.get('draft')


def test_a_fenced_answer_is_taken_in_both_modes_and_the_fence_is_recorded(tmp_path):
    root, job_id, prefix = lantern_job(tmp_path)
    fence = '```json\n' + json.dumps(slipped(prefix, bad=())) + '\n```'
    messages, candidate, run = host_answers(root, job_id, [fence], tmp_path)
    assert messages == [] and run['unwrapped'] == 'fence' and run['status'] == 'submitted'
    state = jobs.load(root, job_id)
    packet = ingest.review_packet(root, state, jobs.read_input(root, job_id), 'c01')
    assert packet['program_repairs'][0]['rule'] == 'fence'
    assert ingest.program_repairs(root, state)[0] == {'slot': 'c01', 'role': 'producer', 'rule': 'fence',
                                                      'run_id': run['run_id'],
                                                      'what': packet['program_repairs'][0]['what']}
    # API mode: the executor reads the fenced text and says so
    from secondbrain.executors import api_anthropic, api_openai
    data = {'choices': [{'message': {'content': fence}, 'finish_reason': 'stop'}], 'model': 'm'}
    assert api_openai.parse(data)[0] == json.loads(fence[8:-4]) and api_openai.parse(data)[4] == 'fence'
    assert api_openai.parse({'choices': [{'message': {'content': fence[8:-4]}}]})[4] is None
    text = {'content': [{'type': 'text', 'text': fence}]}
    assert api_anthropic.parse(text)[0] == json.loads(fence[8:-4]) and api_anthropic.parse(text)[4] == 'fence'
    assert api_anthropic.parse({'content': [{'type': 'tool_use', 'name': api_anthropic.TOOL, 'input': {}}]})[4] \
        is None


def test_a_file_that_is_not_json_even_without_its_fence_is_refused_and_counted(tmp_path):
    root, job_id, _ = lantern_job(tmp_path)
    host = HostExecutor()
    out = ingest.advance(root, job_id, producer=host, reviewer=host)
    [h] = [x for x in out['handoffs'] if x['role'] == 'producer']
    path = tmp_path / 'broken.json'
    path.write_text('```json\n{"cards": [\n```', encoding='utf-8')
    code = cli.main(['--vault', str(root), 'job', 'submit', job_id, '--role', 'producer', '--run-id', h['run_id'],
                     '--file', str(path), '--model', 'gpt-test'])
    info = jobs.load(root, job_id)['chunks']['c01']
    assert code != 0 and ingest._refusals(info, ingest.ANSWER_ROLES[0]) == 1
    assert 'the answer file is not JSON' in info['last_validation_error']


def test_host_packet_of_the_next_ask_carries_the_fix(tmp_path):
    """A host run given up after a refusal is asked again with a new packet: it carries the failing cards too."""
    root, job_id, prefix = lantern_job(tmp_path)
    host = HostExecutor()
    out = ingest.advance(root, job_id, producer=host, reviewer=host)
    [h] = [x for x in out['handoffs'] if x['role'] == 'producer']
    with pytest.raises(ValueError):
        jobs.submit(root, job_id, slipped(prefix), role='producer', run_id=h['run_id'], model_reported='gpt-test')
    jobs.abandon_run(root, job_id, h['run_id'], reason='the sub-agent stopped')
    out = ingest.advance(root, job_id, producer=host, reviewer=host)
    [h2] = [x for x in out['handoffs'] if x['role'] == 'producer']
    text = open(h2['packet_path'], encoding='utf-8').read()
    packet = packet_in(text)
    assert [c['slug'] for c in packet['fix']['cards']] == ['marker-dimming']
    assert '## Some cards of your previous answer failed the program\'s checks' in text
    assert packet['fix']['kept'] == ['lantern-queue', 'lit-marker-cost', 'queue-order']
    jobs.submit(root, job_id, fixed('marker-dimming'), role='producer', run_id=h2['run_id'], model_reported='gpt-test')
    cand = jobs.artifact(root, jobs.load(root, job_id), 'candidate', 'c01')
    assert sorted(c['slug'] for c in cand['cards']) == sorted(CARDS)


def test_a_card_held_back_keeps_its_text_for_the_owner_and_the_reviewer_sees_only_why(tmp_path):
    root, job_id, prefix = lantern_job(tmp_path, retries=0)
    answer = slipped(prefix)
    produce, review, seen = scripted([answer], [accept_all])
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=review))
    given = next(c for c in answer['cards'] if c['slug'] == 'marker-dimming')
    [pending] = [p for p in done['pending'] if p.get('slug') == 'marker-dimming']
    assert pending['card']['propositions'] == given['propositions']
    assert pending['card']['flashcards'] == given['flashcards']
    assert pending['card']['source_quotes'] == [BAD_QUOTES['marker-dimming']]
    held = jobs.artifact(root, jobs.load(root, job_id), 'candidate', 'c01')['held_cards']
    assert held[0]['card'] == pending['card']
    [rv] = seen['reviewer']
    assert set(rv.packet['held_cards'][0]) == {'slug', 'title', 'problems'}

"""A reviewer's ``minor`` note whose words are a sign of a blocking problem is checked once, never sent back blind.

A chunk reviewer accepted a card and wrote down, as ``minor``, that the card does not say a thing or leaves out a
condition of the chunk. The program finds such notes by their words (``cardlint.cued``) and asks the reviewer once,
in a slot of its own (``nc-c01``), whether the problem the note names makes the card unfaithful, not readable without
the source, or not one unit. A yes sends the card back as a revise of that kind, which counts toward the repair
rounds like any revise; a no leaves it accepted; the check is not a repair round. A ``minor`` note about wording
(no cue word) is not asked about. Host and API mode go the same way: the role table and the one entry point. The
texts are invented; the cue words are Chinese, the language they were measured on.
"""
from __future__ import annotations

import copy
import json

import pytest

from secondbrain import cli, jobs
from secondbrain.executors.host import HostExecutor
from secondbrain.pipelines import cardlint, ingest, notecheck
from tests.ingest_helpers import CARDS, first_candidate, first_review, repair_candidate
from tests.test_answer_repair import lantern_job
from tests.test_review_rules import run, with_rows

CUED = '没写明一个标记占几个字'
WORDING = '这句可以写得更紧凑'


def noted(text=CUED):
    return with_rows(first_review(), **{'lit-marker-cost': {'notes': [{'category': 'minor', 'text': text}]}})


#: Words of lit-marker-cost a yes quotes.
QUOTED = 'Each marker costs one word of memory'


def answer(value, category=None, rid='N1', quote=QUOTED, problem='it never says how large a word is'):
    yes = value == 'yes'
    return {'answers': [{'id': rid, 'answer': value, 'category': category, 'quote': quote if yes else None,
                         'problem': problem if yes else None,
                         'reason': 'the card relies on a size it never gives' if yes
                         else 'the size is on the card already'}]}


def repair_with_cost():
    """The repair round's answer: the dimming card fixed, and the marker cost card again (the program had sent it
    back too)."""
    out = repair_candidate()
    cost = copy.deepcopy(CARDS['lit-marker-cost'])
    out['cards'].append(cost)
    out['coverage'].append({'source_quote': cost['source_quotes'][0], 'disposition': 'card',
                            'cards': ['lit-marker-cost'], 'reason': 'covered'})
    return out


def dimming_accepted():
    return {'cards': accept_both()['cards'][:1], 'new_topics': [],
            'coverage': {'decision': 'accept', 'reason': 'covered'}}


def accept_both():
    rows = [{'slug': s, 'decision': 'accept', 'overlap': 'distinct', 'reason': 'now right'}
            for s in ('marker-dimming', 'lit-marker-cost')]
    return {'cards': rows, 'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'covered'}}


# --- which notes ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('text', [CUED, '漏了块里给的限定', '块文没有这个数', '没说是哪个索引', '比原文说得宽',
                                  '去掉了“近似”二字', '指向原文的例子'])
def test_a_minor_note_with_a_cue_word_is_asked_about(text):
    assert cardlint.cued(text)
    assert [i['slug'] for i in notecheck.items(noted(text))] == ['lit-marker-cost']


@pytest.mark.parametrize('text', [WORDING, '措辞可再顺一点', '标题可以更短'])
def test_a_minor_note_about_wording_is_not(text):
    assert not cardlint.cued(text)
    assert notecheck.items(noted(text)) == []


def test_only_minor_notes_of_cards_accepted_and_written_are_asked_about():
    review = noted()
    for row in review['cards']:
        row.setdefault('notes', [{'category': 'minor', 'text': CUED}])
    # marker-dimming is revised already; queue-order is accepted as the same as an existing card (not written)
    assert [i['slug'] for i in notecheck.items(review)] == ['lantern-queue', 'lit-marker-cost']
    assert [i['id'] for i in notecheck.items(review)] == ['N1', 'N2']


# --- in a job -----------------------------------------------------------------------------------------------------
def verdicts(root, job_id, slug):
    record = jobs.load(root, job_id)['decisions']
    return [(e['slot'], e['data']['decision'], e.get('reason')) for e in record
            if e['kind'] == 'value' and e['value'] == 'verdict' and e['object'] == slug]


def test_a_yes_sends_the_card_back_and_its_repair_takes_a_round_as_any_revise(tmp_path):
    # one repair round: the check takes none of it
    root, job_id, done, seen = run(tmp_path, [first_candidate(), repair_with_cost()],
                                   [noted(), answer('yes', 'unreadable'), accept_both()], max_repair_rounds=1)
    assert done['status'] == 'applied', done.get('pending')
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'nc-c01', 'c01.r1']
    check = seen['reviewer'][1].packet
    [card] = check['cards']
    assert (card['slug'], card['notes']) == ('lit-marker-cost', [{'id': 'N1', 'text': CUED}])
    assert check['chunk']['id'] == 'c01' and check['chunk']['text']
    # the repair round's producer reads the reason, as for any revise
    findings = seen['producer'][1].packet['review_findings']['cards']
    row = next(r for r in findings if r['slug'] == 'lit-marker-cost')
    assert row['decision'] == 'revise' and row['blocking'] == 'unreadable' and CUED in row['reason']
    assert QUOTED in row['reason'] and 'it never says how large a word is' in row['reason']
    assert row['turned']['check'] == [{'id': 'N1', 'category': 'unreadable', 'quote': QUOTED,
                                      'problem': 'it never says how large a word is',
                                      'reason': 'the card relies on a size it never gives'}]
    assert verdicts(root, job_id, 'lit-marker-cost') == [('c01', 'accept', None), ('nc-c01', 'revise', 'note_check'),
                                                         ('c01.r1', 'accept', 'repair_round')]
    assert any(t['slug'] == 'lit-marker-cost' and t['category'] == 'unreadable' and t['slot'] == 'c01'
               for t in done['notes_turned'])
    assert 'lit-marker-cost' in done['outcomes']


def test_a_no_leaves_the_card_accepted(tmp_path):
    root, job_id, done, seen = run(tmp_path, [first_candidate(), repair_candidate()],
                                   [noted(), answer('no'), dimming_accepted()])
    assert done['status'] == 'applied', done.get('pending')
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'nc-c01', 'c01.r1']
    assert verdicts(root, job_id, 'lit-marker-cost') == [('c01', 'accept', None)]
    assert done['outcomes']['lit-marker-cost'] == 'write'
    assert done['notes_checked'] == [{'chunk': 'c01', 'slot': 'c01', 'slug': 'lit-marker-cost', 'note': CUED,
                                      'id': 'N1', 'answer': 'no', 'category': None, 'quote': None, 'problem': None,
                                      'reason': 'the size is on the card already'}]
    assert not any(t['slug'] == 'lit-marker-cost' for t in done['notes_turned'])
    stored = jobs.artifact(root, jobs.load(root, job_id), 'review', 'nc-c01')
    assert stored['answers'][0]['answer'] == 'no' and stored['turned'] == []


def test_a_minor_note_about_wording_asks_nothing(tmp_path):
    _, _, done, seen = run(tmp_path, [first_candidate(), repair_candidate()],
                           [noted(WORDING), dimming_accepted()])
    assert done['status'] == 'applied', done.get('pending')
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'c01.r1']


def test_the_check_is_no_repair_round_and_its_revise_waits_when_none_is_left(tmp_path):
    _, _, done, seen = run(tmp_path, [first_candidate()], [noted(), answer('yes', 'fact')], max_repair_rounds=0)
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'nc-c01']
    pending = {p.get('slug'): p['reason'] for p in done['pending']}
    assert CUED in pending['lit-marker-cost'] and 'marker-dimming' in pending


def test_an_answer_that_misses_a_note_is_asked_again_and_given_up_leaves_the_cards_accepted(tmp_path):
    bad = {'answers': []}
    root, job_id, done, seen = run(tmp_path, [first_candidate(), repair_candidate()],
                                   [noted(), bad, bad, bad, dimming_accepted()],
                                   max_invalid_retries=2)
    assert done['status'] == 'applied', done.get('pending')
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'nc-c01', 'nc-c01', 'nc-c01', 'c01.r1']
    info = jobs.load(root, job_id)['chunks']['nc-c01']
    assert info['skipped'] and info['invalid_reviews'] == 3
    assert 'N1 have no answer' in info['last_review_error']
    assert done['outcomes']['lit-marker-cost'] == 'write'


def test_a_fault_of_the_program_reading_the_checks_answer_sets_that_answer_aside_not_the_review(tmp_path,
                                                                                                   monkeypatch):
    real, calls = notecheck.applied, []

    def applied(review, check):
        calls.append(check)
        if len(calls) == 1:
            raise RuntimeError('reading the check broke')
        return real(review, check)
    monkeypatch.setattr(notecheck, 'applied', applied)
    root, job_id, done, seen = run(tmp_path, [first_candidate(), repair_with_cost()],
                                   [noted(), answer('yes', 'unreadable'), answer('yes', 'unreadable'), accept_both()])
    assert done['status'] == 'applied', done.get('pending')
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'nc-c01', 'nc-c01', 'c01.r1']
    [fault] = jobs.load(root, job_id)['program_errors']
    assert (fault['slot'], fault['role'], fault['set_aside']) == ('nc-c01', 'reviewer', True)


def test_a_yes_whose_quote_is_not_on_the_card_is_asked_again_and_given_up_leaves_the_card_accepted(tmp_path):
    wrong = answer('yes', 'fact', quote='Each marker costs two words')  # not the card's words
    root, job_id, done, seen = run(tmp_path, [first_candidate(), repair_candidate()],
                                   [noted(), wrong, wrong, wrong, dimming_accepted()], max_invalid_retries=2)
    assert done['status'] == 'applied', done.get('pending')
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'nc-c01', 'nc-c01', 'nc-c01', 'c01.r1']
    info = jobs.load(root, job_id)['chunks']['nc-c01']
    assert info['skipped'] and "'Each marker costs two words' is not on the card" in info['last_review_error']
    assert done['outcomes']['lit-marker-cost'] == 'write'  # given up: as a no


def test_the_check_asks_for_the_words_and_a_no_for_a_note_that_says_the_card_is_not_wrong():
    from secondbrain.pipelines.ingest import note_check_prompt
    text = note_check_prompt('ingest-x1')
    assert 'quote in `quote`, word for word, the words of the card that are wrong' in text
    assert 'A note that says itself the card is not wrong, or still reads well: answer `no`, unless you can point ' \
           'to the very words of the card that are wrong.' in text


ON_CARD = {'quote': QUOTED, 'problem': 'it never says how large a word is'}


@pytest.mark.parametrize('given, problem', [
    ({'answers': [{'id': 'N1', 'answer': 'yes', 'category': None, 'reason': 'r', **ON_CARD}]}, 'a yes names the kind'),
    ({'answers': [{'id': 'N1', 'answer': 'yes', 'category': 'minor', 'reason': 'r', **ON_CARD}]},
     'a yes names the kind'),
    ({'answers': [{'id': 'N1', 'answer': 'yes', 'category': 'fact', 'reason': 'r', 'quote': None,
                   'problem': 'p'}]}, 'a yes quotes, word for word'),
    ({'answers': [{'id': 'N1', 'answer': 'yes', 'category': 'fact', 'reason': 'r', 'quote': 'one word of fog',
                   'problem': 'p'}]}, "'one word of fog' is not on the card"),
    ({'answers': [{'id': 'N1', 'answer': 'yes', 'category': 'fact', 'reason': 'r', 'quote': 'cos',
                   'problem': 'p'}]}, 'characters or more'),  # too short to name words of the card
    ({'answers': [{'id': 'N1', 'answer': 'yes', 'category': 'fact', 'reason': 'r', 'quote': QUOTED,
                   'problem': ' '}]}, 'says what the quoted words lack'),
    ({'answers': [{'id': 'N1', 'answer': 'no', 'reason': ' '}]}, 'give the reason'),
    ({'answers': [{'id': 'N9', 'answer': 'no', 'reason': 'r'}]}, 'is not a note this check asks about'),
    ({'answers': [{'id': 'N1', 'answer': 'no', 'reason': 'r'}] * 2}, 'answered twice'),
])
def test_an_answer_out_of_form_is_refused(given, problem):
    frozen = {'cards': [{'slug': 'lit-marker-cost', 'card': CARDS['lit-marker-cost'],
                         'notes': [{'id': 'N1', 'text': CUED}]}]}
    with pytest.raises(ValueError, match=problem):
        notecheck.validate(frozen, given, noted())


# --- host mode: the same role, the same entry -------------------------------------------------------------------
def submit(root, job_id, tmp_path, handoff, payload, model):
    path = tmp_path / f'{handoff["run_id"]}.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return cli.main(['--vault', str(root), 'job', 'submit', job_id, '--role', handoff['role'], '--run-id',
                     handoff['run_id'], '--file', str(path), '--model', model])


def test_in_host_mode_the_check_is_a_reviewer_hand_off_taken_at_the_one_entry(tmp_path):
    root, job_id, _ = lantern_job(tmp_path)

    def step():
        return ingest.advance(root, job_id, producer=HostExecutor(), reviewer=HostExecutor())['handoffs']
    [h] = step()
    assert submit(root, job_id, tmp_path, h, first_candidate(), 'gpt-test') == 0
    [h] = step()
    assert (h['role'], h['chunk']) == ('reviewer', 'c01')
    assert submit(root, job_id, tmp_path, h, noted(), 'claude-test') == 0
    [h] = step()
    assert (h['role'], h['chunk'], h['step']) == ('reviewer', 'nc-c01', 'notecheck')
    again = ingest.packet_for(root, job_id, role='reviewer', chunk=None, executor=HostExecutor())
    assert (again['chunk'], again['step']) == ('nc-c01', 'notecheck')
    assert submit(root, job_id, tmp_path, again, {'answers': []}, 'claude-test') != 0  # refused at the entry
    run = jobs.load_run(root, job_id, again['run_id'])
    assert run['rejected_submits'] == 1 and 'N1 have no answer' in run['rejected'][0]
    [h] = step()
    assert submit(root, job_id, tmp_path, h, answer('yes', 'unreadable'), 'claude-test') == 0
    [h] = step()
    assert (h['role'], h['chunk']) == ('producer', 'c01.r1')

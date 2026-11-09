"""`tools/review_replay.py`: a reviewer's task of an earlier job, given this version's instructions and schema.

The packet stays as it was; the instructions are this version's, with the same added parts; the note the earlier
answer got from the validator is left out. Reading an answer applies the program's rule (an accept with a note of a
blocking kind is a revise). The task texts here are invented.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from secondbrain import schemas, unit
from secondbrain.executors.base import render_task_text
from secondbrain.pipelines import source_check

TOOL = Path(__file__).resolve().parents[1] / 'tools' / 'review_replay.py'
spec = importlib.util.spec_from_file_location('review_replay', TOOL)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)

PACKET = {'chunk': {'id': 'c01', 'text': 'Lanterns dim in order.'},
          'candidate': {'cards': [{'slug': 'dim'}, {'slug': 'lit'}]}}


def old_task(*parts, validator=False):
    text = ('You are working on one chunk of a source document for the SecondBrain ingest job `ingest-x1`.\n\n'
            '## Task: independently review proposed cards for this chunk\n\nOld instructions.\n')
    text += ''.join(f'\n{p}\n\nOld part.\n' for p in parts)
    if validator:
        text += '\n## Your previous answer was rejected by the validator\n\n- something\n'
    return render_task_text(text, PACKET, {'type': 'object'})


def packet_of(text):
    return json.loads(re.search(r'<task_packet>\n(.*)\n</task_packet>', text, re.S).group(1))


def test_a_chunk_review_is_rendered_with_this_versions_prompt_and_schema_and_the_same_packet():
    new = replay.render(old_task('## This is a repair round: judge the fixes', validator=True))
    assert packet_of(new) == PACKET
    assert 'job `ingest-x1`' in new and unit.ONE_QUESTION in new and '`repeats`' in new
    assert '## This is a repair round: judge the fixes' in new and 'Old instructions' not in new
    assert 'rejected by the validator' not in new
    assert json.dumps(schemas.REVIEW_CARDS, ensure_ascii=False, indent=2) in new


def test_the_merge_check_is_rendered_with_its_prompt_and_schema():
    old = render_task_text('You are checking the cards that the SecondBrain ingest job `ingest-x1` is about to '
                           'write.\n\n## Task: merge cards of this source that repeat each other\n', PACKET)
    new = replay.render(old)
    assert packet_of(new) == PACKET and "the patch's `question`" in new
    assert json.dumps(source_check.SCHEMA, ensure_ascii=False, indent=2) in new


def test_reading_an_answer_applies_the_programs_rule():
    task = replay.render(old_task())
    level = {'overlap': 'distinct', 'with': []}
    answer = {'cards': [{'slug': 'dim', 'question': 'How do lanterns dim?', 'decision': 'accept', 'reason': 'ok',
                         **level, 'notes': [{'category': 'repeats', 'text': 'P2 says P1 again'}]},
                        {'slug': 'lit', 'decision': 'accept', 'reason': 'ok', **level,
                         'notes': [{'category': 'minor', 'text': 'wording'}]}],
              'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'covered'}}
    assert replay.read(task, answer) == ['dim\trevise\trepeats\tHow do lanterns dim?', 'lit\taccept\t\t']


def test_a_task_it_does_not_know_is_refused():
    with pytest.raises(SystemExit):
        replay.render(render_task_text('## Task: write an article\n', PACKET))


# --- against the task texts real runs write ------------------------------------------------------------------------
def written_tasks(root, job_id):
    from secondbrain import jobs
    from secondbrain.store import state_root
    return sorted((state_root(root) / jobs.location(job_id, 'packets')).glob('reviewer-*.md'))


@pytest.fixture(scope='module')
def runs(tmp_path_factory):
    """Task texts two jobs wrote: one with a split slot and the merge check, one with a focused repair round."""
    from tests.test_patch_set import job, reviewer, run
    from tests.test_review_notes import WRONG, accepted_with
    from tests.test_review_rules import run as run_rules
    from tests.ingest_helpers import first_candidate, repair_candidate, repair_review
    root, job_id, _ = job(tmp_path_factory.mktemp('split'), n_props=11)
    run(root, job_id, reviewer())
    root2, job2, _, _ = run_rules(tmp_path_factory.mktemp('focused'), [first_candidate(), repair_candidate()],
                                  [accepted_with(WRONG), repair_review()])
    return written_tasks(root, job_id) + written_tasks(root2, job2)


def test_render_gives_back_the_text_a_run_was_given(runs):
    """The tool assembles the instructions with the job's own functions: a task text a run wrote is rendered as it
    was. (One that carried a validator's note comes back without it: the first test above.)"""
    kinds = set()
    for path in runs:
        text = path.read_text(encoding='utf-8')
        if 'rejected by the validator' in text:
            continue
        kind, _, _ = replay.parse(text)
        kinds.add(kind + (' focused' if replay.PARTS['focused'] in text else ''))
        assert replay.render(text) == text, path.name
    assert kinds >= {'review', 'review focused', 'split', 'merge'}, kinds


def one_of(runs, kind, focused=False):
    for path in runs:
        text = path.read_text(encoding='utf-8')
        if replay.parse(text)[0] == kind and (replay.PARTS['focused'] in text) == focused:
            return replay.render(text)
    raise AssertionError(kind)


def test_read_prints_refused_for_an_answer_the_program_refuses(runs):
    task = one_of(runs, 'review')
    _, _, packet = replay.parse(task)
    slugs = [c['slug'] for c in packet['candidate']['cards']]
    rows = [{'slug': s, 'decision': 'accept', 'reason': 'ok', 'overlap': 'distinct', 'with': []} for s in slugs]
    good = {'cards': rows, 'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}
    assert [line.split('\t')[:2] for line in replay.read(task, good)] == [[s, 'accept'] for s in slugs]
    for broken, why in (({**good, 'cards': [{**rows[0], 'question': ['a list']}] + rows[1:]}, 'question'),
                        ({**good, 'cards': rows[:1]}, 'every candidate card exactly once'),
                        ({**good, 'cards': 'all accepted'}, 'cards')):
        [line] = replay.read(task, broken)
        assert line.startswith('REFUSED\t') and why in line, line
    split_task = one_of(runs, 'split')
    card = replay.parse(split_task)[2]['card']
    matches = [{'proposition': f'P{i}', 'existing': None} for i in range(1, len(card['propositions']) + 1)]
    [line] = replay.read(split_task, {'decision': 'accept', 'restates': True, 'reason': 'ok', 'matches': matches})
    assert line.startswith('REFUSED\t') and 'restates' in line, line
    [line] = replay.read(split_task, {'decision': 'accept', 'restates': False, 'reason': 'ok', 'matches': matches})
    assert line.split('\t')[1] == 'accept'


def test_read_applies_the_focused_rounds_rule(runs):
    task = one_of(runs, 'review', focused=True)
    slugs = [c['slug'] for c in replay.parse(task)[2]['candidate']['cards']]
    rows = [{'slug': s, 'decision': 'revise', 'reason': 'the wording', 'blocking': None} for s in slugs]
    [line] = replay.read(task, {'cards': rows, 'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}})
    assert line.startswith('REFUSED\t') and 'in a repair round a card is revised only' in line


def test_render_adds_the_programs_questions_an_older_packet_did_not_ask_and_read_wants_them_answered():
    packet = {'chunk': {'id': 'c01', 'text': 'x'},
              'candidate': {'cards': [{'slug': 'dim', 'essence': 'e', 'propositions': ['原文说它会变暗。']}]}}
    new = replay.render(render_task_text(
        'You are working on one chunk of a source document for the SecondBrain ingest job `ingest-x1`.\n\n'
        '## Task: independently review proposed cards for this chunk\n', packet, {'type': 'object'}))
    asked = packet_of(new)['program_questions']['dim']
    assert [q['id'] for q in asked] == ['K1']
    row = {'slug': 'dim', 'decision': 'accept', 'reason': 'ok', 'overlap': 'distinct', 'with': []}
    answer = {'cards': [row], 'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}
    [line] = replay.read(new, answer)
    assert line.startswith('REFUSED\t') and 'K1' in line
    yes = {**row, 'answers': [{'id': 'K1', 'answer': 'yes', 'reason': 'it leans on the source'}]}
    assert replay.read(new, {**answer, 'cards': [yes]}) == ['dim\trevise\tunreadable\t']

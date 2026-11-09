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

PACKET = {'chunk': {'id': 'c01', 'text': 'Lanterns dim in order.'}, 'candidate': {'cards': [{'slug': 'dim'}]}}


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
    answer = {'cards': [{'slug': 'dim', 'question': 'How do lanterns dim?', 'decision': 'accept', 'reason': 'ok',
                         'notes': [{'category': 'repeats', 'text': 'P2 says P1 again'}]},
                        {'slug': 'lit', 'decision': 'accept', 'reason': 'ok', 'notes': [{'category': 'minor',
                                                                                          'text': 'wording'}]}]}
    assert replay.read(task, answer) == ['dim\trevise\trepeats\tHow do lanterns dim?\t', 'lit\taccept\t\t\t']


def test_a_task_it_does_not_know_is_refused():
    with pytest.raises(SystemExit):
        replay.render(render_task_text('## Task: write an article\n', PACKET))

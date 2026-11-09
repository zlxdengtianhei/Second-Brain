"""An answer is read the same way in API mode and through `sb job submit`.

The API executor read the JSON object out of words around it (``Here it is: {...}``, ``{...}\\nDone.``), while
`sb job submit` read the file as strict JSON with one code fence allowed: the same answer was taken in one mode and
refused in the other (the sixth review's probe). Now both use one function, ``executors.api.read_answer``, which
keeps the API's reading: the text as it is, else without one Markdown code fence around the whole of it, else its
first ``{`` to its last ``}`` when words stand around it; nothing else is loosened. What was set aside is recorded on
the run (``fence`` or ``around``) and shown in ``program_repairs``. The answers are invented.
"""
from __future__ import annotations

import json

import pytest

from secondbrain import jobs
from secondbrain.executors import api_openai
from secondbrain.executors.api import read_answer
from secondbrain.pipelines import ingest
from tests.test_answer_repair import host_answers, lantern_job, slipped

OBJECT = {'cards': []}
READINGS = [
    ('{"cards": []}', OBJECT, None),
    ('```json\n{"cards": []}\n```', OBJECT, 'fence'),
    ('Here it is:\n```json\n{"cards": []}\n```', OBJECT, 'around'),
    ('Here it is: {"cards": []}', OBJECT, 'around'),
    ('{"cards": []}\nDone.', OBJECT, 'around'),
    ('[1, 2]', None, None),                                    # JSON, but not an object
    ('{\\n"cards": []\\n}', None, None),                       # a literal \n for a line break is no JSON
    ('```json\\n{\\n"cards": []\\n}\\n```', None, None),
    ('no answer at all', None, None),
]


@pytest.mark.parametrize('text, value, how', READINGS)
def test_one_reading_for_both_modes(text, value, how):
    assert read_answer(text) == (value, how)
    data = {'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}], 'model': 'm'}
    assert api_openai.parse(data)[0] == value and api_openai.parse(data)[4] == how


@pytest.mark.parametrize('wrap', ['Here it is: {}', '{}\nDone.'])
def test_sb_job_submit_takes_an_answer_with_words_around_it_as_the_api_does(tmp_path, wrap):
    root, job_id, prefix = lantern_job(tmp_path)
    text = wrap.replace('{}', json.dumps(slipped(prefix, bad=())))
    messages, candidate, run = host_answers(root, job_id, [text], tmp_path)
    assert messages == [] and run['status'] == 'submitted' and run['unwrapped'] == 'around'
    state = jobs.load(root, job_id)
    assert ingest.program_repairs(root, state)[0]['rule'] == 'around'
    assert sorted(c['slug'] for c in candidate['cards']) == sorted(c['slug'] for c in slipped(prefix)['cards'])

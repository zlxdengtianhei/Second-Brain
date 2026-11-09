"""A producer's answer the program refuses is kept whole, in the job's state, for reading why it was refused.

Before, a refused answer left only the refusal's text: the run's ``output_sha256`` was empty and the answer itself was
gone, so why a model's answers kept failing (a quote with other quotation marks, a coverage row naming another card)
could not be read back. Now each refused producer answer is written under the job's state (never in the vault, never
in an export), with who gave it, the reply as it came (before a code fence was set aside and the JSON read), the
answer as read, before and after the program's repairs with the repairs, each error line with the field it names and
its kind (a quote also with its own text, the longest stretch of it the chunk has, its length and where that stretch
starts), and the coverage rows as given. The texts are invented.
"""
from __future__ import annotations

import copy
import hashlib
import json

from secondbrain import cli, jobs
from secondbrain.executors import api_anthropic
from secondbrain.executors.base import Result
from secondbrain.executors.host import HostExecutor
from secondbrain.pipelines import ingest
from secondbrain.store import read_state
from tests.ingest_helpers import first_candidate
from tests.test_answer_repair import lantern_job

WRONG = 'A lantern queue keeps every item in arrival order, and a reader may skip ahead by lit markers.'


def submit(root, job_id, tmp_path, text):
    [h] = [x for x in ingest.advance(root, job_id, producer=HostExecutor(), reviewer=HostExecutor())['handoffs']
           if x['role'] == 'producer']
    path = tmp_path / 'answer.txt'
    path.write_text(text, encoding='utf-8')
    code = cli.main(['--vault', str(root), 'job', 'submit', job_id, '--role', 'producer', '--run-id', h['run_id'],
                     '--file', str(path), '--model', 'gpt-test'])
    return code, jobs.load_run(root, job_id, h['run_id'])


def test_a_refused_answer_is_kept_with_its_reply_and_its_error_lines(tmp_path):
    root, job_id, _ = lantern_job(tmp_path)
    answer = copy.deepcopy(first_candidate())
    answer['cards'][0]['source_quotes'] = [WRONG]
    text = 'Here it is:\n```json\n' + json.dumps(answer, ensure_ascii=False) + '\n```'
    code, run = submit(root, job_id, tmp_path, text)
    assert code != 0 and run['rejected_submits'] == 1
    [where] = run['rejected_answers']
    kept = json.loads(read_state(root, where))
    assert kept['reply_text'] == text                                    # as it came, the fence and words included
    assert run['output_sha256'] == kept['reply_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert kept['payload'] == answer and kept['payload_before_repair'] == answer
    assert kept['payload_after_repair']['cards'][0]['source_quotes'] == [WRONG]  # no repair could make it a slice
    assert (kept['run_id'], kept['chunk'], kept['base_chunk'], kept['round'], kept['attempt']) == \
        (run['run_id'], 'c01', 'c01', 0, 1)
    assert kept['task_sha256'] == run['task_sha256'] and kept['input_sha256'] and kept['chunk_sha256']
    [quote] = [r for r in kept['errors'] if r.get('quote')]
    assert quote['path'] == 'cards[lantern-queue].source_quotes[0]' and quote['quote'] == WRONG
    assert 0 < quote['k'] < quote['n'] == len(WRONG) and quote['b'] >= 0
    assert any(r['kind'] == 'quote' and r['path'] == 'cards[lantern-queue].source_quotes' for r in kept['errors'])
    assert kept['coverage'] == [{k: r.get(k) for k in ('disposition', 'cards', 'with', 'source_quote')}
                                for r in answer['coverage']]
    assert 'not a verbatim slice' in kept['refusal']
    assert where == jobs.location(job_id, jobs.REJECTED, f"{run['run_id']}-1.json")  # in the job's state
    assert not [p for p in (root / '10_Knowledge').rglob('*') if run['run_id'] in p.name]


def test_an_answer_that_is_not_json_is_kept_as_it_came(tmp_path):
    root, job_id, _ = lantern_job(tmp_path)
    code, run = submit(root, job_id, tmp_path, '{"cards": [ oops')
    kept = json.loads(read_state(root, run['rejected_answers'][0]))
    assert code != 0 and kept['reply_text'] == '{"cards": [ oops' and kept['payload'] is None
    assert kept['step'] == 'read'


def test_a_taken_answer_is_not_kept_apart_and_its_run_reads_as_before(tmp_path):
    root, job_id, _ = lantern_job(tmp_path)
    code, run = submit(root, job_id, tmp_path, json.dumps(first_candidate()))
    assert code == 0 and run['status'] == 'submitted' and 'rejected_answers' not in run
    state = jobs.load(root, job_id)
    assert run['output_sha256'] == state['chunks']['c01']['candidate']['sha256']


def test_an_executors_reply_text_is_kept_on_its_result():
    data = {'content': [{'type': 'text', 'text': 'Here: {"cards": []}'}], 'model': 'm', 'stop_reason': 'end_turn'}
    assert api_anthropic.answer_text(data) == 'Here: {"cards": []}'
    tool = {'content': [{'type': 'tool_use', 'name': api_anthropic.TOOL, 'input': {'cards': []}}]}
    assert json.loads(api_anthropic.answer_text(tool)) == {'cards': []}
    assert jobs._reply_text(None, jobs._NO_ANSWER, Result(status='completed', output={'a': 1}, text='raw')) == 'raw'
    assert json.loads(jobs._reply_text(None, jobs._NO_ANSWER, Result(status='completed', output={'a': 1}))) == {'a': 1}

"""Edges of the one entry point that each had the right behaviour and no test to keep it (the seventh review).

- An answer file that is not UTF-8 text is refused and counted, and the host run closes when the budget is spent.
- The split card's reviewer sees what the program repaired in the card.
- A host's answer to an export reads raw TeX backslashes as the API executor does; other jobs do not.
- A ``ValueError`` the program raised on purpose in a check is the answer's refusal; one that Python or a library
  raised while the check ran (``int('x')``) is a fault of the program, counted the same and listed for the owner.
- The same text is taken or refused alike through an API executor and through `sb job submit` (the review's probe,
  run through the CLI).

The cards, answers and jobs are invented.
"""
from __future__ import annotations

import copy
import importlib
import json
from types import SimpleNamespace

import pytest

from secondbrain import cli, jobs
from secondbrain.config import ReviewPolicy
from secondbrain.executors import api_openai
from secondbrain.executors.host import HostExecutor
from secondbrain.pipelines import ingest, split
from secondbrain.service import Service
from secondbrain.store import Conflict
from tests.helpers import build_vault
from tests.ingest_helpers import SOURCE_NAME, build_ingest_vault, first_candidate, with_diagrams
from tests.test_answer_repair import lantern_job
from tests.test_ingest import settings
from tests.test_invariants import SOURCE, two_chunk_producer
from tests.test_patch_set import SPLIT_CARD, reviewer, run, sb_existing
from tests.test_split_repair import BOLD, PLAIN

LIMIT = ingest.ANSWER_ROLES[0].retries(ReviewPolicy()) + 1


def host_run(root, job_id):
    host = HostExecutor()
    out = ingest.advance(root, job_id, producer=host, reviewer=host)
    [h] = [x for x in out['handoffs'] if x['role'] == 'producer']
    return h


def test_an_answer_file_that_is_not_utf8_is_counted_and_closes_the_run(tmp_path):
    root, job_id, _ = lantern_job(tmp_path)
    h = host_run(root, job_id)
    path = tmp_path / 'answer.json'
    path.write_bytes(b'{"cards": "\xff\xfe"}')
    codes = [cli.main(['--vault', str(root), 'job', 'submit', job_id, '--role', 'producer', '--run-id', h['run_id'],
                       '--file', str(path), '--model', 'gpt-test']) for _ in range(LIMIT + 1)]
    info = jobs.load(root, job_id)['chunks']['c01']
    run = jobs.load_run(root, job_id, h['run_id'])
    assert all(code != 0 for code in codes) and ingest._refusals(info, ingest.ANSWER_ROLES[0]) == LIMIT
    assert run['status'] == 'failed' and run['error']['given_up'] is True
    assert 'not UTF-8 text' in info['last_validation_error']


def test_the_split_reviewer_sees_what_the_program_repaired_in_the_card(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    settings(chunk_chars=1000)
    (root / '00_Inbox' / SOURCE_NAME).write_text(SOURCE.replace(PLAIN, BOLD), encoding='utf-8')
    job_id = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)['job_id']
    sb_existing(root, 11)

    def produce(task):
        if split.is_slot(task.chunk.split('.')[0]):
            return {'card': copy.deepcopy(SPLIT_CARD)}
        plain = {**task.packet, 'chunk': {**task.packet['chunk'], 'text': task.packet['chunk']['text'].replace(
            BOLD, PLAIN)}}
        return two_chunk_producer(SimpleNamespace(packet=plain))
    seen, inner = [], reviewer()

    def review(task):
        if split.is_slot(task.chunk.split('.')[0]):
            seen.append(task)
        return inner(task)
    run(root, job_id, review, with_diagrams(produce))
    [split_review] = seen
    assert [(r['rule'], r['before'], r['after']) for r in split_review.packet['program_repairs']] == \
        [('quote', PLAIN, BOLD)]


RAW_TEX = b'{"items": [{"p": "$a \\mid b$"}]}'  # \m is no JSON escape: strict JSON refuses it


@pytest.mark.parametrize('kind, taken', [('export', True), ('ingest', False)])
def test_a_host_answer_reads_raw_tex_backslashes_only_for_an_export(tmp_path, monkeypatch, kind, taken):
    root = build_vault(tmp_path / 'vault')
    job_id = f'{kind}-job'
    jobs.create(root, job_id, kind, {'settings': None, 'cards': []})
    module, name = jobs.SUBMISSION_VALIDATORS[kind].split(':')
    monkeypatch.setattr(importlib.import_module(module), name, lambda packet, payload, **kw: payload)
    monkeypatch.setattr(jobs, '_recorder', lambda kind: None)
    jobs.start_run(root, job_id, role='producer', step='produce', executor='host', run_id='r1', model_requested='m',
                   chunk='u01')
    if taken:
        Service(root).submit(job_id, role='producer', run_id='r1', raw=RAW_TEX)
        stored = jobs.artifact(root, jobs.load(root, job_id), 'candidate', 'u01')
        assert stored['items'][0]['p'] == '$a \\mid b$'
    else:
        with pytest.raises(ValueError, match='the answer file is not JSON'):
            Service(root).submit(job_id, role='producer', run_id='r1', raw=RAW_TEX)


@pytest.mark.parametrize('how, program', [('on purpose', False), ('by Python', True)])
def test_a_value_error_the_program_raised_on_purpose_is_a_refusal_and_any_other_a_fault(tmp_path, monkeypatch, how,
                                                                                        program):
    """The chunk's own check refuses a name given as a number on purpose (a ``raise`` of the package); a check that
    calls ``int('x')`` raises Python's ``ValueError``, which is the program's fault."""
    root = build_vault(tmp_path / 'vault')
    jobs.create(root, 'ing', 'ingest', {'settings': None, 'cards': []})
    if how == 'by Python':
        monkeypatch.setattr(ingest, 'validate_submission', lambda packet, payload, **kw: int(payload['cards']))
    jobs.start_run(root, 'ing', role='producer', step='produce', executor='host', run_id='r1', model_requested='m',
                   chunk='c01')
    with pytest.raises(ValueError) as refused:
        Service(root).submit('ing', {'cards': 'x'}, role='producer', run_id='r1')
    assert refused.value.counted
    faults = [i for i in Service(root).owner_items() if i.get('kind') == 'program_error']
    assert bool(faults) is program
    if program:
        assert faults[0]['error'] == 'ValueError' and faults[0]['stage'] == 'check'
    else:
        assert 'rejected by validation' in str(refused.value)


ANSWER = json.dumps(first_candidate())
FENCE = '```json\n' + ANSWER + '\n```'
TEXTS = {'plain': ANSWER, 'fence': FENCE, 'words+fence': 'Here it is:\n' + FENCE,
         'words before': 'Here it is: ' + ANSWER, 'words after': ANSWER + '\nDone.',
         'two objects': ANSWER + '\n' + ANSWER, 'literal \\n': ANSWER.replace(', ', ',\\n')}


@pytest.mark.parametrize('name', sorted(TEXTS))
def test_an_api_executor_and_sb_job_submit_take_or_refuse_the_same_text(tmp_path, name):
    text = TEXTS[name]
    out, _model, _usage, _cut, how = api_openai.parse(
        {'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}], 'model': 'm'})
    root, job_id, _ = lantern_job(tmp_path)
    h = host_run(root, job_id)
    path = tmp_path / 'answer.txt'
    path.write_text(text, encoding='utf-8')
    code = cli.main(['--vault', str(root), 'job', 'submit', job_id, '--role', 'producer', '--run-id', h['run_id'],
                     '--file', str(path), '--model', 'gpt-test'])
    run_ = jobs.load_run(root, job_id, h['run_id'])
    assert (code == 0) is isinstance(out, dict)
    assert (run_['status'] == 'submitted') is isinstance(out, dict)
    assert run_.get('unwrapped') == (how if isinstance(out, dict) else None)
    if not isinstance(out, dict):
        with pytest.raises(Conflict, match='TERMINAL_RUN'):
            for _ in range(LIMIT):
                cli.main(['--vault', str(root), 'job', 'submit', job_id, '--role', 'producer', '--run-id',
                          h['run_id'], '--file', str(path), '--model', 'gpt-test'])
            jobs.submit(root, job_id, {}, role='producer', run_id=h['run_id'])

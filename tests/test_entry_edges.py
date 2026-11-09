"""Edges of the one entry point that each had the right behaviour and no test to keep it (the seventh review).

- An answer file that is not UTF-8 text is refused and counted, and the host run closes when the budget is spent.
- The split card's reviewer sees what the program repaired in the card.
- A host's answer to an export reads raw TeX backslashes as the API executor does; other jobs do not.
- Only an ``AnswerError``, which a check raises on purpose for what the answer says, is the answer's refusal. A
  ``ValueError`` that Python or a library raised while the check ran (``int('x')``), or one the program raised
  because its own state was wrong, is a fault of the program, counted the same and listed for the owner; every
  ``raise`` in the registered checks is an ``AnswerError`` but for the few that name such a state.
- The same text is taken or refused alike through an API executor and through `sb job submit` (the review's probe,
  run through the CLI).

The cards, answers and jobs are invented.
"""
from __future__ import annotations

import ast
import copy
import importlib
import inspect
import json
from types import SimpleNamespace

import pytest

from secondbrain import cli, jobs
from secondbrain.config import ReviewPolicy
from secondbrain.executors import api_openai
from secondbrain.executors.host import HostExecutor
from secondbrain.schemas import AnswerError
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


@pytest.mark.parametrize('how, program', [('on purpose', False), ('by Python', True), ('program state', True)])
def test_only_an_answer_error_is_the_answers_refusal_and_any_other_value_error_a_fault(tmp_path, monkeypatch, how,
                                                                                    program):
    """The chunk's own check refuses a name given as a number on purpose (an ``AnswerError``); a check that calls
    ``int('x')`` raises Python's ``ValueError``; a check called without the slot the program binds every ingest run
    to raises the package's own ``IngestError`` on a line that is a ``raise`` (the old guess took it for the
    answer's). Both of the last are the program's fault."""
    root = build_vault(tmp_path / 'vault')
    jobs.create(root, 'ing', 'ingest', {'settings': None, 'cards': []})
    real = ingest.validate_submission
    if how == 'by Python':
        monkeypatch.setattr(ingest, 'validate_submission', lambda packet, payload, **kw: int(payload['cards']))
    elif how == 'program state':
        monkeypatch.setattr(ingest, 'validate_submission',
                            lambda packet, payload, **kw: real(packet, payload, **{**kw, 'chunk': None}))
    jobs.start_run(root, 'ing', role='producer', step='produce', executor='host', run_id='r1', model_requested='m',
                   chunk='c01')
    with pytest.raises(ValueError) as refused:
        Service(root).submit('ing', {'cards': 'x'}, role='producer', run_id='r1')
    assert refused.value.counted
    assert isinstance(refused.value, AnswerError) is not program
    faults = [i for i in Service(root).owner_items() if i.get('kind') == 'program_error']
    assert bool(faults) is program
    if program:
        assert faults[0]['stage'] == 'check'
        assert faults[0]['error'] == ('ValueError' if how == 'by Python' else 'IngestError')
    else:
        assert 'rejected by validation' in str(refused.value)


#: The check functions of each module that holds a registered check (:data:`jobs.SUBMISSION_VALIDATORS`) or one it
#: hands an answer to, and the raises in them that name a state of the program (not of the answer).
CHECKS = {
    'secondbrain.classification': (['validate_submission', 'validate', '_validate_candidate_shape'],
                                   ['Unknown submission role', 'needs the candidate it reviews']),
    'secondbrain.curation': (['validate', '_deep_keys_equal_preserved'], []),
    'secondbrain.relocate': (['validate'], []),
    'secondbrain.domcreate': (['validate'], []),
    'secondbrain.runner': (['validate_submission'], []),
    'secondbrain.export.pipeline': (['validate_submission', '_validate_write', '_validate_review', '_validate_smooth'],
                                    []),
    'secondbrain.pipelines.ingest': (['validate_submission', 'validate_candidate', 'validate_review',
                                      '_check_focused', '_made_amends', '_producer_answer', '_validate_rejudge'],
                                     ['always bound to a chunk slot']),
    'secondbrain.pipelines.split': (['validate_card', 'validate_review'], []),
    'secondbrain.pipelines.notes': (['validate_summary', 'validate_concepts'], []),
    'secondbrain.pipelines.source_check': (['validate_answer'], []),
    'secondbrain.pipelines.card_diagrams': (['validate_answer'], []),
    'secondbrain.schemas': (['shape_checked'], []),
}


def test_every_refusal_a_registered_check_raises_is_an_answer_error():
    for ref in jobs.SUBMISSION_VALIDATORS.values():
        module, name = ref.split(':')
        assert name in CHECKS[module][0], ref
    seen = 0
    for module, (names, program) in CHECKS.items():
        mod = importlib.import_module(module)
        tree = ast.parse(inspect.getsource(mod))
        found = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        assert sorted(n.name for n in found) == sorted(names), module
        for fn in found:
            for r in ast.walk(fn):
                if not (isinstance(r, ast.Raise) and isinstance(r.exc, ast.Call)):
                    continue  # a bare raise or a name raises what was raised or built above
                seen += 1
                cls = eval(ast.unparse(r.exc.func), vars(mod))  # noqa: S307 - a class name of the module
                states = any(p in ast.unparse(r.exc) for p in program)
                assert issubclass(cls, AnswerError) is not states, f'{module}.{fn.name}:{r.lineno}'
    assert seen > 80


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

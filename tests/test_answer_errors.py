"""What a check raises for an answer is an ``AnswerError``; the program holds that, not a list written by hand.

The last review found refusals of answers that were still plain errors (fifteen: a curation taxonomy with a pillar
named twice, a classification review that leaves an item out), so the answer was told the program had failed and
the owner's list filled with false faults; and a check whose own input lacked a field refused the answer. Now the
call graph of every registered check is read from the source (``tests/raise_reach.py``), and every ``raise`` it can
reach is an ``AnswerError`` or one of the checks of the program's own state listed here, each with its reason. A
check's other inputs are the program's: a field one lacks is the program's fault (``ProgramInputError``), not the
answer's ``ShapeError``. The cards, maps and answers are invented.
"""
from __future__ import annotations

import copy

from secondbrain import jobs, runner
from secondbrain.executors import ExecutorFailure, FakeExecutor
from secondbrain.schemas import AnswerError, ProgramInputError
from secondbrain.service import Service
from tests import test_curation as tc
from tests.raise_reach import raises, reachable
from tests.test_answer_repair import lantern_job
from tests.test_maintenance_reviews import curation_job

#: The raises a check may reach that are about the program's own state, never the answer: ``(module, function)``
#: with the reason. A module named alone stands for all of its functions.
PROGRAM_STATE = {
    'secondbrain.config': 'the configuration in force',
    'secondbrain.store': 'the state store and the vault paths',
    'secondbrain.vault': 'the cards in the vault',
    ('secondbrain.classification', 'validate_submission'): 'a run without its role or the candidate it reviews',
    ('secondbrain.curation', '_domain_moc'): "a domain the job's frozen catalog lacks",
    ('secondbrain.export.pipeline', '_unit'): 'a unit the export job does not have',
    ('secondbrain.jobs', '_slot'): 'a slot the job does not have',
    ('secondbrain.jobs', '_verified_bytes'): 'a stored answer changed or out of place',
    ('secondbrain.jobs', 'artifact'): 'an answer the job does not hold yet',
    ('secondbrain.pipelines.answerfix', 'repair'): 'a repair that changed knowledge text: the program at fault',
    ('secondbrain.pipelines.chunking', 'chunk_text'): 'a chunk size the settings got wrong',
    ('secondbrain.pipelines.ingest', '_chunk'): 'a chunk the job does not have',
    ('secondbrain.pipelines.ingest', 'answer_role'): 'a slot no role answers in',
    ('secondbrain.pipelines.ingest', 'validate_submission'): 'an ingest run not bound to a chunk',
    ('secondbrain.runner', '_spec'): 'a job kind with no model steps',
}


def listed(module, function):
    return module in PROGRAM_STATE or (module, function) in PROGRAM_STATE


def test_every_raise_a_registered_check_can_reach_is_an_answer_error_or_a_listed_program_state():
    _, seen = reachable()
    assert len(seen) > 100  # the graph reaches through the pipelines, not only the entry functions
    found = raises()
    assert len(found) >= 150
    stray = [f'{m}.{fn}:{line} {cls.__name__}' for m, fn, line, cls, _ in found
             if not issubclass(cls, AnswerError) and not listed(m, fn)]
    assert stray == []


def test_every_listed_program_state_is_still_reached():
    """A name left in the list after its raise went away would let a new raise there pass unseen."""
    reached = {(m, fn) for m, fn, _, cls, _ in raises() if not issubclass(cls, AnswerError)}
    for key in PROGRAM_STATE:
        assert any(m == key for m, _ in reached) if isinstance(key, str) else key in reached, key


def test_a_taxonomy_with_a_pillar_named_twice_is_the_answers_error(tmp_path):
    root, job_id, _, _ = curation_job(tmp_path)
    twice = {'title': tc.MOC, 'narrative': '新叙事',
             'pillars': [{'name': tc.PILLAR, 'order': 1, 'why': '支柱说明', 'topics': [tc.TOPIC]},
                         {'name': tc.PILLAR, 'order': 2, 'why': '又一次', 'topics': ['训练目标']}]}
    body = tc.proposal([tc.item(tc.DOM, twice, [tc.EXISTING])])
    produce = FakeExecutor('gpt-test', responder=lambda t: copy.deepcopy(body))
    for _ in range(5):
        try:
            runner.run_job(root, job_id, producer=produce,
                           reviewer=FakeExecutor('claude-test', responder=lambda t: {'items': []}))
        except ExecutorFailure:  # the refused answer, raised when the round's budget is spent
            continue
        break
    runs = [r for r in jobs.list_runs(root, job_id) if r['role'] == 'producer']
    messages = [r['error']['message'] for r in runs]
    assert len(runs) >= 3 and all('pillar name repeated' in m for m in messages), messages
    assert not any('error of the program' in m for m in messages)
    assert not [i for i in Service(root).owner_items() if i.get('kind') == 'program_error']
    assert jobs.load(root, job_id).get('escalation_required') is True


def test_a_check_whose_own_input_lacks_a_field_is_the_programs_fault(tmp_path):
    """The candidate the program made for a judgement again lacks its card: the answer is not to blame."""
    root, job_id, _ = lantern_job(tmp_path)
    slot = 'xr1-lantern-queue'
    jobs.put_program_candidate(root, job_id, slot, {'not_card': {'slug': 'lantern-queue'}})
    run = jobs.start_run(root, job_id, role='reviewer', step='review', executor='host', model_requested='m',
                         chunk=slot)
    answer = {'slug': 'lantern-queue', 'overlap': 'none', 'with': [], 'reason': 'a concrete reason'}
    try:
        Service(root).submit(job_id, answer, role='reviewer', run_id=run['run_id'])
    except jobs.Refused as refused:
        assert refused.counted and not isinstance(refused, AnswerError)
    [item] = [i for i in Service(root).owner_items() if i.get('kind') == 'program_error']
    assert (item['error'], item['stage']) == (ProgramInputError.__name__, 'check')

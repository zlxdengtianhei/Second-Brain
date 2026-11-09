"""Controller for the maintenance jobs: classify, taxonomy, move, domain create.

Each job runs produce → independent review → (bounded repair) → apply. Model
calls go through an :class:`~secondbrain.executors.Executor`; this module only
builds prompts and packets, records runs and enforces the round limit. The
same :func:`call_executor` is used by the ingest pipeline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Callable

from . import budget, jobs, schemas
from .executors.base import Executor, ExecutorFailure, INVALID_OUTPUT, Result, Task, render_task_text
from .locks import job_lock
from .store import Conflict, digest

#: Fields that carry the producer's own reasoning; never shown to the reviewer.
WITHHELD_FIELDS = frozenset({'reason', 'rationale', 'self_review', 'self_assessment',
                             'confidence', 'thinking'})


class AwaitingSubmission(RuntimeError):
    """A host executor handed the task off; the job continues after submit."""

    def __init__(self, handoff: dict):
        self.handoff = handoff
        super().__init__('awaiting submission')


class _ContinueJob(RuntimeError):
    pass


def prompt(name: str) -> str:
    """A prompt file, with the shared definitions put in: the repeat scale (:mod:`secondbrain.overlap`) and the
    kinds of a reviewer's notes (:mod:`secondbrain.review_notes`), so every prompt that uses one says the same."""
    from . import overlap, review_notes
    text = resources.files('secondbrain').joinpath('prompts', name).read_text(encoding='utf-8')
    return text.replace(overlap.PLACEHOLDER, overlap.definition().rstrip('\n')).replace(
        review_notes.PLACEHOLDER, review_notes.definition())


def withhold_reasons(value):
    """Copy ``value`` without the producer's reasoning fields (recursively)."""
    if isinstance(value, dict):
        return {k: withhold_reasons(v) for k, v in value.items() if k not in WITHHELD_FIELDS}
    if isinstance(value, list):
        return [withhold_reasons(v) for v in value]
    return value


@dataclass(frozen=True)
class KindSpec:
    produce_prompt: str
    review_prompt: str
    produce_schema: dict
    review_schema: dict
    check: Callable[[dict, dict], object]
    apply: Callable[[Path, str], dict]
    #: ``(packet, candidate, review)``: the review covers the candidate as the kind's apply reads it, checked when the
    #: review is taken, so a review that is taken can be read
    review_check: Callable[[dict, dict, dict], object]


def _spec(kind: str) -> KindSpec:
    if kind == 'classification':
        from . import classification as m
        return KindSpec('classify.md', 'review_classify.md', schemas.CLASSIFY, schemas.REVIEW_BY_ID,
                        lambda packet, cand: m.validate(packet, cand, check_evidence=False), m.apply, m.check_review)
    if kind == 'curation':
        from . import curation as m
        return KindSpec('taxonomy.md', 'review_taxonomy.md', schemas.TAXONOMY, schemas.REVIEW_BY_DOMAIN,
                        m.validate, m.apply, m.check_review)
    if kind == 'relocation':
        from . import relocate as m
        return KindSpec('move.md', 'review_move.md', schemas.MOVE, schemas.REVIEW_BY_ID,
                        m.validate, m.apply, m.check_review)
    if kind == 'domcreate':
        from . import domcreate as m
        return KindSpec('domain_create.md', 'review_domain_create.md', schemas.DOMAIN_CREATE,
                        schemas.REVIEW_BY_DOMAIN, m.validate, m.apply, m.check_review)
    raise ValueError(f'This job kind has no model steps: {kind}')


def validate_submission(packet: dict, payload: dict, *, role: str, state: dict, candidate=None,
                        **_context) -> dict:
    """jobs.submit hook of the maintenance kinds without their own (curation, relocation, domain creation): the
    answer's shape, a proposal by the kind's own check, and a review by whether it covers the proposal it answers as
    the kind's apply reads it (each domain or item once). Before, a proposal, and later a review, was first read after
    it was kept, so one that failed stopped every later run of the job and was never counted or asked for again."""
    spec = _spec(state['kind'])
    wrong = schemas.type_problems(payload, spec.produce_schema if role == 'producer' else spec.review_schema)
    if wrong:
        raise schemas.AnswerError('Answer rejected by validation:\n- ' + '\n- '.join(wrong))
    if role == 'producer':
        spec.check(packet, payload)
    else:
        spec.review_check(packet, candidate, payload)
    return payload


def _open_run(root: Path, state: dict, role: str, chunk: str | None) -> dict | None:
    for run_id in reversed(state.get('runs', [])):
        run = jobs.load_run(root, state['job_id'], run_id)
        if run['role'] == role and run.get('chunk') == chunk and run['status'] == 'started':
            return run
    return None


def call_executor(root: Path, job_id: str, executor: Executor, *, role: str, step: str,
                  prompt_text: str, packet: dict, output_schema: dict,
                  chunk: str | None = None, tex_backslashes: bool = False) -> Result:
    """Record a run, call ``executor`` and submit its output through the gate.

    Raises :class:`AwaitingSubmission` for a host hand-off, and
    :class:`ExecutorFailure` (code ``INVALID_OUTPUT`` for outputs the
    validators reject) after recording the failure on the run.
    """
    try:
        task = start_call(root, job_id, executor, role=role, step=step, prompt_text=prompt_text, packet=packet,
                          output_schema=output_schema, chunk=chunk, tex_backslashes=tex_backslashes)
        try:
            result = executor.run(task)
        except Exception as exc:
            fail_call(root, task, exc)
            raise
        return finish_call(root, task, result)
    except Exception as exc:  # an error of the model call is not one of reading a taken answer: raised as it is
        raise jobs.from_model_call(exc)


def start_call(root: Path, job_id: str, executor: Executor, *, role: str, step: str,
               prompt_text: str, packet: dict, output_schema: dict,
               chunk: str | None = None, tex_backslashes: bool = False) -> Task:
    """The first part of :func:`call_executor`: record the run and keep its text; returns the task to run.

    :func:`start_call`, :func:`finish_call` and :func:`fail_call` read and write the job's state; only
    ``executor.run(task)`` between them may run on another thread (several model calls at once), so the state
    of one job is only ever read and written by one thread. Raises :class:`AwaitingSubmission` when the host
    already holds this hand-off.
    """
    state = jobs.load(root, job_id)
    stale = _open_run(root, state, role, chunk)
    if stale is not None:
        if stale.get('handoff') and executor.name == 'host':
            raise AwaitingSubmission(stale['handoff'])
        jobs.abandon_run(root, job_id, stale['run_id'],
                         reason='controller restarted before this run submitted')
    text = render_task_text(prompt_text, packet, output_schema)
    run = jobs.start_run(root, job_id, role=role, step=step, executor=executor.name,
                         model_requested=executor.model, endpoint_host=executor.endpoint_host,
                         prompt_sha256=digest(prompt_text.encode()), task_sha256=digest(text.encode()),
                         chunk=chunk)
    # Every run keeps the exact text it was given, for audit and for host hand-off.
    from .store import _atomic_state, state_root
    packet_rel = jobs.location(job_id, 'packets', f"{role}-{chunk or 'job'}-{run['run_id']}.md")
    _atomic_state(root, packet_rel, text.encode('utf-8'))
    return Task(job_id=job_id, run_id=run['run_id'], role=role, step=step, prompt=prompt_text,
                packet=packet, packet_sha256=state['input_sha256'], output_schema=output_schema,
                chunk=chunk, packet_path=str(state_root(root) / packet_rel), tex_backslashes=tex_backslashes)


def fail_call(root: Path, task: Task, exc: BaseException) -> None:
    """Record on the run of ``task`` that its executor raised ``exc``."""
    jobs.fail_run(root, task.job_id, task.run_id,
                  error={'code': 'EXECUTOR_EXCEPTION', 'message': str(exc), 'retryable': True})


def finish_call(root: Path, task: Task, result: Result) -> Result:
    """The last part of :func:`call_executor`: record a hand-off, a failure, or submit the output."""
    job_id = task.job_id
    if result.status == 'awaiting_submission':
        _record_handoff(root, job_id, task.run_id, result.handoff or {})
        raise AwaitingSubmission(result.handoff or {})
    answered = result.status == 'completed' and isinstance(result.output, dict)
    error = dict(result.error or {'code': INVALID_OUTPUT, 'message': 'executor returned no JSON object',
                                  'retryable': True})
    if not answered and error.get('code') != INVALID_OUTPUT:  # no answer came (transport, provider): not counted
        jobs.fail_run(root, job_id, task.run_id, error=error)
        raise ExecutorFailure(error)
    try:  # an answer, or one the executor could not read: the one entry point takes it, or refuses and counts it
        jobs.receive(root, job_id, task.run_id, role=task.role, result=result)
    except ValueError as exc:  # refused there (``counted``, the run failed there) or a conflict of the run (neither)
        if getattr(exc, 'counted', False):
            raise ExecutorFailure(exc.error) from exc
        error = {'code': INVALID_OUTPUT, 'message': str(exc), 'retryable': True}
        jobs.fail_run(root, job_id, task.run_id, error=error)
        raise ExecutorFailure(error) from exc
    return result


def _record_handoff(root: Path, job_id: str, run_id: str, handoff: dict) -> None:
    from .store import vault_lock, write_json
    with vault_lock(root):
        run = jobs.load_run(root, job_id, run_id)
        run['handoff'] = handoff
        write_json(root, jobs.run_location(job_id, run_id), run)


def rounds_left(packet: dict, state: dict) -> int:
    """The repair rounds a maintenance job has left (a repair child job counts its parent's): the one test,
    :func:`secondbrain.budget.rounds_left`."""
    used = int(packet.get('repair_depth', 0)) + int(state.get('repair_round', 0))
    return budget.rounds_left(used, jobs.policy_of(packet).max_repair_rounds)


def _refusal_key(state: dict, role: str) -> str:
    """Where a maintenance job counts the refused answers of ``role``: by repair round (``producer@1``), as export
    counts a unit's steps."""
    return f'{role}@{int(state.get("repair_round", 0))}'


def record_refusal(packet, *, role, root, chunk, state, run, message, keep=None) -> bool:
    """The gate's hook (:func:`secondbrain.jobs.receive`) for the maintenance kinds: an answer of ``role`` that failed
    the program's checks, from an API run or a host sub-agent alike, counted in the job state
    (:func:`secondbrain.budget.count`); True when the role's budget for this round is now spent."""
    row = state.setdefault('refused', {}).setdefault(_refusal_key(state, role), {'count': 0})
    return budget.used_up(budget.count(row, message, keep=2000), jobs.policy_of(packet).max_invalid_retries)


def _spent(state: dict, packet: dict, role: str) -> str | None:
    """Why ``role`` is not asked again in this round (its refused answers spent ``max_invalid_retries``), or None."""
    row = (state.get('refused') or {}).get(_refusal_key(state, role)) or {}
    if not budget.used_up(int(row.get('count', 0)), jobs.policy_of(packet).max_invalid_retries):
        return None
    return f'the {role} answer failed validation {row["count"]} times: {row.get("message", "")}'


def _give_up(root: Path, job_id: str, why: str, step: str) -> dict:
    """The job waits for the owner (``sb status --owner-items``): nothing more is asked, nothing applied; ``step``
    is the step that stopped (``produce``, ``repair``, ``review``)."""
    return jobs.update(root, job_id, escalation_required=True, escalation_reason=why, escalation_step=step)


def run_job(root: Path, job_id: str, *, producer: Executor, reviewer: Executor,
            apply: bool = True, stop_after_review: bool = False) -> dict:
    """Drive one maintenance job as far as it can go; safe to call again. An error while it reads the answers it
    took goes to the one guard (:func:`secondbrain.jobs.guarded`)."""
    return jobs.guarded(root, job_id, lambda: _run_job(root, job_id, producer=producer, reviewer=reviewer,
                                                       apply=apply, stop_after_review=stop_after_review))


def _run_job(root: Path, job_id: str, *, producer: Executor, reviewer: Executor,
             apply: bool, stop_after_review: bool) -> dict:
    with job_lock(root, job_id):
        while True:
            try:
                return _run_locked(root, job_id, producer=producer, reviewer=reviewer,
                                   apply=apply, stop_after_review=stop_after_review)
            except _ContinueJob:
                continue
            except AwaitingSubmission as waiting:
                state = jobs.load(root, job_id)
                return {**state, 'status': 'awaiting_submission', 'handoff': waiting.handoff}


def _run_locked(root: Path, job_id: str, *, producer: Executor, reviewer: Executor,
                apply: bool, stop_after_review: bool) -> dict:
    state = jobs.load(root, job_id)
    if state.get('commit_intent') and not apply:
        # Proposal mode must not resume an interrupted knowledge transaction.
        return state
    with jobs.reading('resume'):  # a write under way: no one answer to blame
        resumed = jobs.resume_transaction(root, job_id)
    if resumed is not None or state['status'] in {'applied', 'partial', 'published', 'superseded'}:
        if state.get('repair_child'):
            run_job(root, state['repair_child'], producer=producer, reviewer=reviewer, apply=apply)
        return jobs.load(root, job_id)
    spec = _spec(state['kind'])
    packet = jobs.read_input(root, job_id)
    head = prompt('common.md').format(kind=state['kind'], job_id=job_id)
    if not state.get('candidate'):
        spent = _spent(state, packet, 'producer')
        if spent:
            return _give_up(root, job_id, spent, 'produce')
        feedback = packet.get('repair_feedback')
        text = head + '\n' + prompt(spec.produce_prompt)
        if feedback:
            text += '\n' + prompt('repair.md') + '\n```json\n' + json.dumps(feedback, ensure_ascii=False) + '\n```\n'
        model_packet = {k: v for k, v in packet.items() if k != 'repair_feedback'}
        _produce(root, job_id, producer, text, model_packet, spec, packet, step='produce')
        raise _ContinueJob()
    with jobs.reading('candidate', (None, 'producer')):  # the round's one candidate
        candidate = jobs.artifact(root, state, 'candidate')
        spec.check(packet, candidate)
    if not state.get('review'):
        spent = _spent(state, packet, 'reviewer')
        if spent:
            return _give_up(root, job_id, spent, 'review')
        with jobs.reading('review_packet', (None, 'producer')):
            shown = withhold_reasons(candidate) if jobs.policy_of(packet).withhold_producer_reasons else candidate
            review_packet = {'input': {k: v for k, v in packet.items() if k != 'repair_feedback'},
                             'candidate': shown}
        call_executor(root, job_id, reviewer, role='reviewer', step='review',
                      prompt_text=head + '\n' + prompt(spec.review_prompt),
                      packet=review_packet, output_schema=spec.review_schema)
        state = jobs.load(root, job_id)
    with jobs.reading('review', (None, 'reviewer')):  # the round's one review
        review = jobs.require_independent_review(root, state)
        rejected = [x for x in review.get('items', []) if isinstance(x, dict) and x.get('decision') == 'revise']
    if stop_after_review:
        return state
    if rejected and state['kind'] == 'classification':
        with jobs.reading('repair'):  # a child job and an apply of the accepted rows: no one answer to blame
            return _repair_classification(root, state, packet, review, producer=producer,
                                          reviewer=reviewer, apply=apply)
    if rejected and rounds_left(packet, state) > 0:
        text = (head + '\n' + prompt(spec.produce_prompt) + '\n' + prompt('repair.md')
                + '\n```json\n' + json.dumps({'review_findings': rejected}, ensure_ascii=False) + '\n```\n')
        with jobs.reading('repair_packet', (None, 'producer')):
            repair_packet = {'input': {k: v for k, v in packet.items() if k != 'repair_feedback'},
                             'previous_candidate': jobs.as_given(candidate)}
        jobs._internal_update(root, job_id, repair_round=int(state.get('repair_round', 0)) + 1)
        _produce(root, job_id, producer, text, repair_packet, spec, packet, step='repair')
        raise _ContinueJob()
    if rejected:
        _give_up(root, job_id, 'repair rounds exhausted; the review still rejects items', 'repair')
    if not apply:
        return jobs.load(root, job_id)
    with jobs.reading('apply', (None, 'reviewer')):  # the round's review, the newest answer
        return spec.apply(root, job_id)


def _produce(root, job_id, producer, text, model_packet, spec, packet, *, step):
    """Producer call; an invalid output spends one repair round with feedback."""
    while True:
        try:
            call_executor(root, job_id, producer, role='producer', step=step, prompt_text=text,
                          packet=model_packet, output_schema=spec.produce_schema)
            return
        except ExecutorFailure as failure:
            state = jobs.load(root, job_id)
            if failure.code != INVALID_OUTPUT or rounds_left(packet, state) <= 0 or _spent(state, packet, 'producer'):
                raise
            jobs._internal_update(root, job_id, repair_round=int(state.get('repair_round', 0)) + 1)
            text = text + ('\n## Your previous answer was rejected by the validator\n\n'
                           + failure.error.get('message', '') + '\n')


def _repair_classification(root, state, packet, review, *, producer, reviewer, apply):
    """Accepted rows keep their review; only rejected ids get a frozen child job."""
    from .classification import apply as apply_classification
    rejected_ids = {x['id'] for x in review['items'] if x['decision'] == 'revise'}
    child_id = state.get('repair_child')
    if child_id is None and rounds_left(packet, state) <= 0:
        _give_up(root, state['job_id'], 'repair rounds exhausted; the review still rejects items', 'repair')
        return apply_classification(root, state['job_id']) if apply else jobs.load(root, state['job_id'])
    if child_id is None:
        key = digest((state['job_id'] + state['candidate']['sha256'] + state['review']['sha256']).encode())[:24]
        child_id = f'repair-{key}'
        child_packet = {k: v for k, v in packet.items() if k != 'repair_feedback'}
        child_packet.update(cards=[x for x in packet['cards'] if x['id'] in rejected_ids],
                            repair_depth=int(packet.get('repair_depth', 0)) + 1 + int(state.get('repair_round', 0)),
                            repair_parent=state['job_id'],
                            repair_parent_candidate=state['candidate']['sha256'],
                            repair_parent_review=state['review']['sha256'],
                            repair_feedback={
                                'candidate': {'items': [x for x in jobs.artifact(root, state, 'candidate')['items'] if x['id'] in rejected_ids]},
                                'review': {'items': [x for x in review['items'] if x['id'] in rejected_ids]}})
        jobs.create(root, child_id, 'classification', child_packet)
        jobs.update(root, state['job_id'], repair_child=child_id)
    child_input = jobs.read_input(root, child_id)
    if (child_input.get('repair_parent') != state['job_id']
            or child_input.get('repair_parent_candidate') != state['candidate']['sha256']
            or child_input.get('repair_parent_review') != state['review']['sha256']
            or {x['id'] for x in child_input['cards']} != rejected_ids):
        raise Conflict('The revision job does not bind every counterexample of this round')
    # Apply the parent first: it never writes the rejected ids, so the child
    # still consumes their unchanged bytes.
    if apply:
        apply_classification(root, state['job_id'])
    child = run_job(root, child_id, producer=producer, reviewer=reviewer, apply=apply)
    parent = jobs.load(root, state['job_id'])
    if parent.get('escalation_required') or not child.get('escalation_required'):
        return parent
    return jobs.update(root, state['job_id'], escalation_required=True,
                       escalation_reason=f'in its revision job {child_id}: {child.get("escalation_reason")}',
                       escalation_step=child.get('escalation_step'))

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

from . import config, jobs, schemas
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
    return resources.files('secondbrain').joinpath('prompts', name).read_text(encoding='utf-8')


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


def _spec(kind: str) -> KindSpec:
    if kind == 'classification':
        from . import classification as m
        return KindSpec('classify.md', 'review_classify.md', schemas.CLASSIFY, schemas.REVIEW_BY_ID,
                        lambda packet, cand: m.validate(packet, cand, check_evidence=False), m.apply)
    if kind == 'curation':
        from . import curation as m
        return KindSpec('taxonomy.md', 'review_taxonomy.md', schemas.TAXONOMY, schemas.REVIEW_BY_DOMAIN,
                        m.validate, m.apply)
    if kind == 'relocation':
        from . import relocate as m
        return KindSpec('move.md', 'review_move.md', schemas.MOVE, schemas.REVIEW_BY_ID,
                        m.validate, m.apply)
    if kind == 'domcreate':
        from . import domcreate as m
        return KindSpec('domain_create.md', 'review_domain_create.md', schemas.DOMAIN_CREATE,
                        schemas.REVIEW_BY_DOMAIN, m.validate, m.apply)
    raise ValueError(f'该任务类型没有模型流程：{kind}')


def _open_run(root: Path, state: dict, role: str, chunk: str | None) -> dict | None:
    for run_id in reversed(state.get('runs', [])):
        run = jobs.load_run(root, state['job_id'], run_id)
        if run['role'] == role and run.get('chunk') == chunk and run['status'] == 'started':
            return run
    return None


def call_executor(root: Path, job_id: str, executor: Executor, *, role: str, step: str,
                  prompt_text: str, packet: dict, output_schema: dict,
                  chunk: str | None = None) -> Result:
    """Record a run, call ``executor`` and submit its output through the gate.

    Raises :class:`AwaitingSubmission` for a host hand-off, and
    :class:`ExecutorFailure` (code ``INVALID_OUTPUT`` for outputs the
    validators reject) after recording the failure on the run.
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
    task = Task(job_id=job_id, run_id=run['run_id'], role=role, step=step, prompt=prompt_text,
                packet=packet, packet_sha256=state['input_sha256'], output_schema=output_schema,
                chunk=chunk, packet_path=str(state_root(root) / packet_rel))
    try:
        result = executor.run(task)
    except Exception as exc:
        jobs.fail_run(root, job_id, run['run_id'],
                      error={'code': 'EXECUTOR_EXCEPTION', 'message': str(exc), 'retryable': True})
        raise
    if result.status == 'awaiting_submission':
        _record_handoff(root, job_id, run['run_id'], result.handoff or {})
        raise AwaitingSubmission(result.handoff or {})
    if result.status != 'completed' or not isinstance(result.output, dict):
        error = result.error or {'code': INVALID_OUTPUT, 'message': 'executor returned no JSON object',
                                 'retryable': True}
        jobs.fail_run(root, job_id, run['run_id'], error=error)
        raise ExecutorFailure(error)
    try:
        jobs.submit(root, job_id, result.output, role=role, run_id=run['run_id'],
                    model_reported=result.model_reported, attestation=result.attestation,
                    usage=result.usage)
    except ValueError as exc:
        error = {'code': INVALID_OUTPUT, 'message': str(exc), 'retryable': True}
        jobs.fail_run(root, job_id, run['run_id'], error=error)
        raise ExecutorFailure(error) from exc
    return result


def _record_handoff(root: Path, job_id: str, run_id: str, handoff: dict) -> None:
    from .store import vault_lock, write_json
    with vault_lock(root):
        run = jobs.load_run(root, job_id, run_id)
        run['handoff'] = handoff
        write_json(root, jobs.run_location(job_id, run_id), run)


def rounds_left(packet: dict, state: dict) -> int:
    used = int(packet.get('repair_depth', 0)) + int(state.get('repair_round', 0))
    return config.review_policy().max_repair_rounds - used


def run_job(root: Path, job_id: str, *, producer: Executor, reviewer: Executor,
            apply: bool = True, stop_after_review: bool = False) -> dict:
    """Drive one maintenance job as far as it can go; safe to call again."""
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
    if jobs.resume_transaction(root, job_id) is not None or \
            state['status'] in {'applied', 'partial', 'published', 'superseded'}:
        if state.get('repair_child'):
            run_job(root, state['repair_child'], producer=producer, reviewer=reviewer, apply=apply)
        return jobs.load(root, job_id)
    spec = _spec(state['kind'])
    packet = jobs.read_input(root, job_id)
    head = prompt('common.md').format(kind=state['kind'], job_id=job_id)
    if not state.get('candidate'):
        feedback = packet.get('repair_feedback')
        text = head + '\n' + prompt(spec.produce_prompt)
        if feedback:
            text += '\n' + prompt('repair.md') + '\n```json\n' + json.dumps(feedback, ensure_ascii=False) + '\n```\n'
        model_packet = {k: v for k, v in packet.items() if k != 'repair_feedback'}
        _produce(root, job_id, producer, text, model_packet, spec, packet, step='produce')
        raise _ContinueJob()
    candidate = jobs.artifact(root, state, 'candidate')
    spec.check(packet, candidate)
    if not state.get('review'):
        shown = withhold_reasons(candidate) if config.review_policy().withhold_producer_reasons else candidate
        review_packet = {'input': {k: v for k, v in packet.items() if k != 'repair_feedback'},
                         'candidate': shown}
        call_executor(root, job_id, reviewer, role='reviewer', step='review',
                      prompt_text=head + '\n' + prompt(spec.review_prompt),
                      packet=review_packet, output_schema=spec.review_schema)
        state = jobs.load(root, job_id)
    review = jobs.require_independent_review(root, state)
    if stop_after_review:
        return state
    rejected = [x for x in review.get('items', []) if isinstance(x, dict) and x.get('decision') == 'revise']
    if rejected and state['kind'] == 'classification':
        return _repair_classification(root, state, packet, review, producer=producer,
                                      reviewer=reviewer, apply=apply)
    if rejected and rounds_left(packet, state) > 0:
        text = (head + '\n' + prompt(spec.produce_prompt) + '\n' + prompt('repair.md')
                + '\n```json\n' + json.dumps({'review_findings': rejected}, ensure_ascii=False) + '\n```\n')
        repair_packet = {'input': {k: v for k, v in packet.items() if k != 'repair_feedback'},
                         'previous_candidate': candidate}
        jobs._internal_update(root, job_id, repair_round=int(state.get('repair_round', 0)) + 1)
        _produce(root, job_id, producer, text, repair_packet, spec, packet, step='repair')
        raise _ContinueJob()
    if rejected:
        jobs.update(root, job_id, escalation_required=True,
                    escalation_reason='repair rounds exhausted; the review still rejects items')
    if not apply:
        return jobs.load(root, job_id)
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
            if failure.code != INVALID_OUTPUT or rounds_left(packet, state) <= 0:
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
        jobs.update(root, state['job_id'], escalation_required=True,
                    escalation_reason='repair rounds exhausted; the review still rejects items')
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
        raise Conflict('修订子任务没有绑定本轮全部反例')
    # Apply the parent first: it never writes the rejected ids, so the child
    # still consumes their unchanged bytes.
    if apply:
        apply_classification(root, state['job_id'])
    child = run_job(root, child_id, producer=producer, reviewer=reviewer, apply=apply)
    parent = jobs.load(root, state['job_id'])
    if parent.get('escalation_required') or not child.get('escalation_required'):
        return parent
    return jobs.update(root, state['job_id'], escalation_required=True,
                       escalation_reason=child.get('escalation_reason'))

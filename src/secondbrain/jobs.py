"""Content-bound processing jobs and their run records.

A job freezes its complete input (``input.json``) and is identified by it.
Every model call is one *run*: the controller writes a run record
(``runs/<run_id>.json``) before calling an executor, and the executor's output
is accepted only through :func:`submit`, which validates it, stores the exact
bytes under ``out/`` and binds them to the run. A run never approves its own
output; :func:`require_independent_review` checks the candidate/review pair
with :mod:`secondbrain.gates` before any knowledge write.

All job state lives in the vault's external state root
(``state_dir()/vaults/<sha256(vault path)>/v3/jobs/<job_id>/``), never inside
the vault. Knowledge files stay in the real vault and are written only through
:mod:`secondbrain.store` transactions.
"""
from __future__ import annotations

import contextvars
import importlib
import json
import os
import re
import sys
import uuid
from base64 import b64decode, b64encode
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

from . import config, gates
from .schemas import AnswerError
from . import layout as layouts
from .store import (Change, Conflict, _state_dir, apply_changes, digest, job_name,
                    read_state, relative, remove_state, vault_lock, write_json)

SCHEMA = "secondbrain-job/3"
RUN_SCHEMA = "secondbrain-run/1"

#: Job kind for deterministic projections (refresh/daily/today/docs). These are
#: computed locally from frozen inputs; no model run exists, and their frozen
#: plan never substitutes for the review gate of semantic jobs.
PROJECTION_KIND = "projection"
#: Every job kind the package creates, and whether its steps ask a model. A kind that asks one counts the answers
#: that fail the program's checks at the one gate (:data:`REFUSAL_RECORDERS`, :func:`receive`); a test reads this
#: table, and finds each ``jobs.create`` call in the package, so a new kind cannot be left out.
KINDS = {'ingest': True, 'export': True, 'classification': True, 'curation': True, 'relocation': True,
         'domcreate': True, 'classification-batch': False, PROJECTION_KIND: False}

#: Kind -> "module:function" validating a submission before it is persisted.
#: Signature: ``fn(packet, payload, *, role, candidate, root, chunk, state) -> payload``;
#: the returned payload (possibly normalized) is what gets stored.
#: Every kind that asks a model has one, so each answer is checked here, at the gate, before it is kept: an answer it
#: refuses is counted against its slot's budget, and any other error while it checks is a fault of the program,
#: counted the same and kept for the owner (:class:`Refused`, :func:`receive`).
SUBMISSION_VALIDATORS = {
    'classification': 'secondbrain.classification:validate_submission',
    'ingest': 'secondbrain.pipelines.ingest:validate_submission',
    'export': 'secondbrain.export.pipeline:validate_submission',
    'curation': 'secondbrain.runner:validate_submission',
    'relocation': 'secondbrain.runner:validate_submission',
    'domcreate': 'secondbrain.runner:validate_submission',
}

TERMINAL_STATUSES = {'applying', 'applied', 'partial', 'published', 'rolled_back', 'superseded'}
_RUN_ID_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')


def _projection_allowed(root: Path, rel: str) -> bool:
    """Whitelist a vault-relative path for deterministic projection writes."""
    relative(rel)
    lay = layouts.of(root)
    return (rel in lay.standalone_files or lay.is_projection_json(rel)
            or lay.is_moc_page(rel) or lay.is_review_page(rel))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def location(job_id: str, *parts: str) -> str:
    """Path of a job file relative to the vault's external state root."""
    parts = parts or ('state.json',)
    for part in parts:
        if not part or '/' in part or part.startswith('.'):
            raise ValueError('invalid job file name')
    return '/'.join(['jobs', job_name(job_id), *parts])


def run_location(job_id: str, run_id: str) -> str:
    return location(job_id, 'runs', f'{_run_name(run_id)}.json')


def _run_name(run_id: str) -> str:
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f'invalid run id: {run_id!r}')
    return run_id


def new_run_id() -> str:
    return f'r-{uuid.uuid4().hex[:16]}'


def load(root: Path, job_id: str) -> dict:
    raw = read_state(root, location(job_id))
    if raw is None:
        raise ValueError(f'No such job: {job_id}')
    state = json.loads(raw)
    if state.get('job_id') != job_id:
        raise ValueError('job state does not belong to this job id')
    if state.get('schema') != SCHEMA:
        raise ValueError(f'job state is not {SCHEMA} ({state.get("schema")!r})')
    return state


def read_input(root: Path, job_id: str) -> dict:
    state = load(root, job_id)
    raw = read_state(root, location(job_id, 'input.json'))
    if raw is None or digest(raw) != state['input_sha256']:
        raise Conflict('The frozen job input was modified; prepare the job again')
    return json.loads(raw)


def create(root: Path, job_id: str, kind: str, packet: dict, **extra) -> dict:
    """Freeze ``packet`` as the job input; idempotent for identical input."""
    raw = encoded(packet)
    with vault_lock(root):
        old = read_state(root, location(job_id))
        if old is not None:
            state = json.loads(old)
            if state.get('schema') != SCHEMA:
                raise ValueError(f'Job {job_id} has an old state format ({state.get("schema")!r})')
            if state.get('input_sha256') != digest(raw) or state.get('kind') != kind:
                raise Conflict('This job id is already bound to other input')
            return state
        write_json(root, location(job_id, 'input.json'), packet)
        saved = read_state(root, location(job_id, 'input.json'))
        state = {'schema': SCHEMA, 'job_id': job_id, 'kind': kind,
                 'status': 'prepared', 'created_at': now(), 'updated_at': now(),
                 'input_sha256': digest(saved), 'runs': [],
                 'candidate': None, 'review': None, 'pending': [], **extra}
        write_json(root, location(job_id), state)
        return state


_PROTECTED = {'schema', 'job_id', 'input_sha256', 'kind', 'created_at', 'runs',
              'candidate', 'review', 'chunks', 'decisions'}

#: Kind -> "module:function" turning a validated submission into decision-record entries (see
#: secondbrain.decisions). Signature as for SUBMISSION_VALIDATORS, plus ``slot_ref``, ``run`` and
#: ``entries`` (the record so far, appended to in place); runs under the vault lock with the new
#: artifact bound to its slot, before the state is written.
DECISION_RECORDERS = {
    'ingest': 'secondbrain.pipelines.ingest:record_decisions',
}

#: Kind -> "module:function" counting an answer that failed the program's checks, from any executor (an API run or
#: a host sub-agent), against its slot's budget (:mod:`secondbrain.budget`). Every kind that asks a model has one
#: (a test enumerates them). Signature: ``fn(packet, *, role, root, chunk, state, run, message) -> bool``, True when
#: the slot is now given up; runs under the vault lock with the job's frozen settings and updates ``state`` in place,
#: which is then written. :func:`_refused_answer` is the one way in: :func:`receive` for an answer, :func:`set_aside`
#: for one taken and then not read, :func:`abandon_run` for a run with none. ``keep`` (given by the validator as
#: ``exc.keep``) is what a
#: refused answer leaves for the next ask (the cards that passed); a recorder that keeps nothing ignores it.
REFUSAL_RECORDERS = {
    'ingest': 'secondbrain.pipelines.ingest:record_refusal',
    'export': 'secondbrain.export.pipeline:record_refusal',
    'classification': 'secondbrain.runner:record_refusal',
    'curation': 'secondbrain.runner:record_refusal',
    'relocation': 'secondbrain.runner:record_refusal',
    'domcreate': 'secondbrain.runner:record_refusal',
}


def frozen_settings(root: Path, packet: dict):
    """The settings a job step reads (``config.job_settings``), made from what the job froze, or None.

    Ingest jobs freeze their review rules, vault layout, card language and
    ingest parameters in ``input.json`` (``settings``); a job frozen before that,
    and the other job kinds, have none, and read the configured settings.
    """
    frozen = (packet or {}).get('settings') if isinstance(packet, dict) else None
    if not isinstance(frozen, dict) or not isinstance(frozen.get('review'), dict):
        return None
    ingest = {f.name: frozen[f.name] for f in fields(config.IngestConfig) if f.name in frozen}
    return config.Settings(
        vault=Path(root).absolute(),
        layout=layouts.VaultLayout.from_mapping(frozen['layout']) if 'layout' in frozen else layouts.of(root),
        review=config.ReviewPolicy(**frozen['review']), ingest=config.IngestConfig(**ingest),
        card_language=frozen.get('language', 'en'), card_language_set=True,
        card_diagrams=bool(frozen.get('diagrams', True)))


#: The review rules an ingest job frozen before it froze them (``ingest/3``, sb 7e42c7b) ran under: that version's
#: defaults, one repair round of whole-card review, no mechanical fixes (a value configured then is not known).
EARLIER_INGEST_REVIEW = {'max_repair_rounds': 1, 'focused_review': False, 'amend': False}


def policy_of(packet: dict):
    """The review rules of a job: the ones it froze; for an ingest job frozen before it froze them,
    :data:`EARLIER_INGEST_REVIEW`; for the other kinds, the configured ones."""
    frozen = (packet or {}).get('settings') if isinstance(packet, dict) else None
    if isinstance(frozen, dict) and isinstance(frozen.get('review'), dict):
        return config.ReviewPolicy(**frozen['review'])
    if isinstance(packet, dict) and packet.get('kind') == 'ingest':
        from dataclasses import replace
        return replace(config.active_review(), **EARLIER_INGEST_REVIEW)
    return config.active_review()



def update(root: Path, job_id: str, **fields) -> dict:
    forbidden = _PROTECTED & fields.keys()
    if forbidden:
        raise ValueError(f'cannot change job identity or result bindings: {sorted(forbidden)}')
    return _internal_update(root, job_id, **fields)


def _internal_update(root: Path, job_id: str, **fields) -> dict:
    """Controller-side state transition (may touch result bindings)."""
    with vault_lock(root):
        state = load(root, job_id)
        state.update(fields, updated_at=now())
        write_json(root, location(job_id), state)
        return state


def _job_write_blocked(root: Path, job_id: str, state: dict) -> bool:
    """Return whether a job has entered a commit/transaction terminal state."""
    if state.get('commit_intent') or state.get('status') in TERMINAL_STATUSES:
        return True
    raw = read_state(root, location(job_id, 'transaction.json'))
    if raw is None:
        return False
    journal = json.loads(raw)
    return journal.get('status') in {'applying', 'applied', 'rolling_back', 'rolled_back', 'conflict'}


def _slot(state: dict, chunk: str | None, *, create: bool = False) -> dict:
    """The dict holding ``candidate``/``review`` refs for a chunk (or the job)."""
    if chunk is None:
        return state
    chunks = state.setdefault('chunks', {}) if create else (state.get('chunks') or {})
    if chunk not in chunks:
        if not create:
            raise ValueError(f'The job has no chunk {chunk}')
        chunks[chunk] = {'candidate': None, 'review': None, 'status': 'prepared', 'round': 0}
    return chunks[chunk]


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------
def load_run(root: Path, job_id: str, run_id: str) -> dict:
    raw = read_state(root, run_location(job_id, run_id))
    if raw is None:
        raise ValueError(f'No such run: {run_id}')
    run = json.loads(raw)
    if run.get('job_id') != job_id or run.get('run_id') != run_id or run.get('schema') != RUN_SCHEMA:
        raise ValueError('run record does not belong to this job and run id')
    return run


def list_runs(root: Path, job_id: str) -> list[dict]:
    return [load_run(root, job_id, rid) for rid in load(root, job_id).get('runs', [])]


def start_run(root: Path, job_id: str, *, role: str, step: str, executor: str,
              model_requested: str | None, endpoint_host: str | None = None,
              prompt_sha256: str | None = None, task_sha256: str | None = None,
              chunk: str | None = None, run_id: str | None = None) -> dict:
    """Record a run before its executor is called; returns the run record."""
    if role not in {'producer', 'reviewer'}:
        raise ValueError(f'unknown role: {role}')
    if step not in {'produce', 'review', 'repair'}:
        raise ValueError(f'unknown step: {step}')
    run_id = _run_name(run_id or new_run_id())
    with vault_lock(root):
        state = load(root, job_id)
        input_raw = read_state(root, location(job_id, 'input.json'))
        if input_raw is None or digest(input_raw) != state.get('input_sha256'):
            raise Conflict('INPUT_CHANGED')
        if _job_write_blocked(root, job_id, state):
            raise Conflict('TERMINAL_JOB')
        if read_state(root, run_location(job_id, run_id)) is not None:
            raise Conflict('RUN_ID_TAKEN')
        slot = _slot(state, chunk, create=True)
        candidate = slot.get('candidate')
        if role == 'reviewer':
            if not candidate:
                raise Conflict('REVIEW_BEFORE_CANDIDATE')
            _verified_bytes(root, job_id, candidate)
        elif candidate and step != 'repair':
            raise Conflict('REPAIR_REQUIRED: a candidate exists; produce again only as a repair step')
        run = {'schema': RUN_SCHEMA, 'run_id': run_id, 'job_id': job_id, 'role': role,
               'step': step, 'chunk': chunk, 'executor': executor,
               'endpoint_host': endpoint_host, 'model_requested': model_requested,
               'model_reported': None, 'attestation': None,
               'packet_sha256': state['input_sha256'], 'prompt_sha256': prompt_sha256,
               'task_sha256': task_sha256, 'output_sha256': None,
               'candidate_sha256': candidate['sha256'] if role == 'reviewer' else None,
               'base_candidate_sha256': (candidate or {}).get('sha256') if role == 'producer' else None,
               'usage': None, 'started_at': now(), 'ended_at': None,
               'status': 'started', 'error': None}
        write_json(root, run_location(job_id, run_id), run)
        state.setdefault('runs', []).append(run_id)
        state['updated_at'] = now()
        write_json(root, location(job_id), state)
        return run


def _close_run(root: Path, job_id: str, run_id: str, status: str, **fields) -> dict:
    with vault_lock(root):
        run = load_run(root, job_id, run_id)
        if run['status'] != 'started':
            raise Conflict(f'run already ended ({run["status"]}); cannot change it to {status}')
        run.update(fields, status=status, ended_at=now())
        write_json(root, run_location(job_id, run_id), run)
        return run


def fail_run(root: Path, job_id: str, run_id: str, *, error: dict) -> dict:
    """Mark an executor failure; the job stays retryable with a new run."""
    return _close_run(root, job_id, run_id, 'failed', error=dict(error))


def abandon_run(root: Path, job_id: str, run_id: str, *, reason: str) -> dict:
    """Give up a run that never submitted (e.g. a host sub-agent that vanished).

    A run given up without an answer counts against its slot's budget like a refused answer (:func:`receive`), for
    every role: a sub-agent that never answers is not asked for ever."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('abandon needs a concrete reason')
    with vault_lock(root):
        run = load_run(root, job_id, run_id)
        if run['status'] != 'started':
            raise Conflict(f'run already ended ({run["status"]}); cannot change it to abandoned')
        run.update(error={'code': 'ABANDONED', 'message': reason, 'retryable': True}, status='abandoned',
                   ended_at=now())
        write_json(root, run_location(job_id, run_id), run)
        state = load(root, job_id)
        _count_refused(root, job_id, state, run, f'the run was given up without an answer: {reason}')
        return run


def fault_of(exc: BaseException) -> dict:
    """What the owner is shown of a program fault: the exception type and its message."""
    return {'type': type(exc).__name__, 'message': str(exc)[:500]}


def _record_fault(state: dict, run: dict, fault: dict) -> str:
    """Keep ``fault`` on the run and in the job state (with the run's slot, role and step); returns the refusal
    message the run and the sub-agent are given."""
    row = {**fault, 'slot': run.get('chunk'), 'role': run['role'], 'step': run.get('step'), 'run_id': run['run_id'],
           'at': now()}
    run['program_error'] = row
    state['program_errors'] = list(state.get('program_errors') or []) + [row]
    when = 'checking this answer' if fault.get('stage') in (None, *RECEIVE_STEPS) else \
        'reading this answer after it was taken'
    return (f'the program failed while {when} ({fault["type"]}: {fault["message"]}); this is an error '
            'of the program, not of the answer, and it is listed for the owner; the answer is counted as refused')


def _refused_answer(root: Path, job_id: str, state: dict, run: dict, message: str, keep=None) -> bool:
    """The gate's one count of a refused answer (under the vault lock): the run keeps the reason, the kind's
    recorder counts it against the slot's budget, and a host run whose slot is now given up is closed, so the
    sub-agent cannot submit again. Writes the state and the run; True when the slot is given up."""
    run['rejected_submits'] = int(run.get('rejected_submits', 0)) + 1
    run['rejected'] = list(run.get('rejected') or []) + [str(message)[:4000]]
    given_up = _count_refused(root, job_id, state, run, message, keep)
    if given_up and run['executor'] == 'host' and run['status'] == 'started':
        run.update(status='failed', ended_at=now(), error={
            'code': 'INVALID_OUTPUT', 'message': str(message)[:4000], 'retryable': False, 'given_up': True})
    write_json(root, run_location(job_id, run['run_id']), run)
    return given_up


def _count_refused(root: Path, job_id: str, state: dict, run: dict, message: str, keep=None) -> bool:
    """Count one answer of ``run`` that failed the checks, by its job kind's recorder (:data:`REFUSAL_RECORDERS`),
    with the job's frozen settings; writes the state. A kind without one asks no model."""
    count = _refusal_recorder(state['kind'])
    if count is None:
        return False
    packet = read_input(root, job_id)
    with config.job_settings(frozen_settings(root, packet)):
        given_up = count(packet, role=run['role'], root=root, chunk=run.get('chunk'), state=state, run=run,
                         message=str(message), keep=keep)
    state['updated_at'] = now()
    write_json(root, location(job_id), state)
    return bool(given_up)


def _recorder(kind: str):
    target = DECISION_RECORDERS.get(kind)
    if not target:
        return None
    module, name = target.split(':')
    return getattr(importlib.import_module(module), name)


def _refusal_recorder(kind: str):
    target = REFUSAL_RECORDERS.get(kind)
    if not target:
        return None
    module, name = target.split(':')
    return getattr(importlib.import_module(module), name)


def _validator(kind: str):
    target = SUBMISSION_VALIDATORS.get(kind)
    if not target:
        return None
    module, name = target.split(':')
    return getattr(importlib.import_module(module), name)


#: The largest answer kept, encoded: a larger one is refused (and counted) as any other.
MAX_ANSWER_BYTES = 8_000_000

#: Fields of an answer that only the program writes into what it keeps (its repairs, the cards it held back, what
#: the merge check's last answer held, a verdict turned into a revise, a reviewer's fix it refused): a model that
#: copies them from a packet into its answer would otherwise put the program's words into the record. They are taken
#: out of every answer before it is checked (:func:`_without_program_fields`), at the top and in each row of
#: ``cards`` and ``items``. No model schema names one; every other field a check adds to an answer is a field of a
#: model schema (a default it fills in), which the tests hold for every answer they check. (``blocking`` is not
#: among them: a focused review writes it.)
PROGRAM_WRITTEN = ('program_repairs', 'held_cards', 'held', 'held_carries', 'turned', 'amend_refused')
PROGRAM_WRITTEN_IN_ROWS = PROGRAM_WRITTEN
#: At the top of an answer also ``blocking``: the program names there the kind of note it turned a split card's
#: accept into a revise for; no model schema has it at the top (a focused review writes it in a row of ``cards``).
PROGRAM_WRITTEN_AT_TOP = PROGRAM_WRITTEN + ('blocking',)

#: The steps of :func:`receive`, in order. An error in ``read``, ``size`` or ``check`` is the answer's when it is an
#: :class:`~secondbrain.schemas.AnswerError`, which a check raises on purpose to refuse an answer; any other error,
#: a ``ValueError`` included, and any error in the other steps, is the program's.
RECEIVE_STEPS = ('read', 'size', 'fields', 'check', 'store', 'record')
_ANSWER_STEPS = ('read', 'size', 'check')
_NO_ANSWER = object()


class Refused(ValueError):
    """An answer the gate refused and counted for a fault of the program (:func:`receive`); ``error`` is what an
    executor reports for it."""

    def __init__(self, message: str, *, given_up: bool = False, fault: dict | None = None):
        super().__init__(message)
        self.counted, self.given_up, self.fault = True, given_up, fault
        self.error = {'code': 'INVALID_OUTPUT', 'message': message[:4000], 'retryable': True, 'counted': True,
                      **({'program_error': fault} if fault else {})}


class _Unread(AnswerError):
    """An answer an executor could not read (its result carries the reason, and the fault when it was the program's)."""

    def __init__(self, message: str, fault: dict | None = None):
        super().__init__(message)
        self.fault = fault


class _NotAVerdict(Exception):
    """A check of the run's binding met while an answer is stored (a second, different answer for a submitted run):
    raised as the conflict it is, never counted."""

    def __init__(self, conflict: Conflict):
        super().__init__(str(conflict))
        self.conflict = conflict


def _without_program_fields(payload: dict) -> tuple[dict, list[str]]:
    """``(payload, removed)``: ``payload`` without the fields only the program writes (:data:`PROGRAM_WRITTEN_AT_TOP`
    at its top, :data:`PROGRAM_WRITTEN_IN_ROWS` in each row of ``cards`` and ``items``)."""
    removed = [k for k in PROGRAM_WRITTEN_AT_TOP if k in payload]
    out = {k: v for k, v in payload.items() if k not in PROGRAM_WRITTEN_AT_TOP}
    for key in ('cards', 'items'):
        rows = out.get(key)
        if not isinstance(rows, list):
            continue
        kept = []
        for i, row in enumerate(rows):
            if isinstance(row, dict) and any(f in row for f in PROGRAM_WRITTEN_IN_ROWS):
                removed += [f'{key}[{i}].{f}' for f in PROGRAM_WRITTEN_IN_ROWS if f in row]
                row = {k: v for k, v in row.items() if k not in PROGRAM_WRITTEN_IN_ROWS}
            kept.append(row)
        out[key] = kept
    return out, removed


def as_given(candidate: dict) -> dict:
    """A kept answer as a model may see it again (a redo round's ``previous_candidate``): without the fields only the
    program writes (:data:`PROGRAM_WRITTEN`), so the model does not copy them back."""
    return _without_program_fields(candidate)[0] if isinstance(candidate, dict) else candidate


def _reply_text(raw, answer, result) -> str | None:
    """The reply as it came, before it was read: a host's file as text, an executor's reply text, an object as JSON.
    Never raises: it only serves the record of a refused answer."""
    try:
        if raw is not None:
            return raw.decode('utf-8', errors='replace')
        if result is not None:
            text = getattr(result, 'text', None)
            return text if isinstance(text, str) else None if result.output is None else \
                json.dumps(result.output, ensure_ascii=False)
        return json.dumps(answer, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - an object that is not JSON: its repr
        try:
            return repr(answer)
        except Exception:  # noqa: BLE001
            return None


#: Where a refused producer answer is kept, under the job's state (never in the vault, never in an export).
REJECTED = 'rejected'


def _keep_rejected(root: Path, job_id: str, state: dict, run: dict, exc: BaseException, step: str, message: str,
                   seen: dict) -> None:
    """Keep a refused producer answer whole, for reading why it was refused: who gave it (run, slot, attempt,
    models, the task's and the job input's hashes), the reply as it came, the answer as read, the refusal, and what
    the kind's check reported (``exc.rejected``: the answer before and after the program's repairs, the repairs,
    each error line with its field path and kind, a quote's own text and where it comes closest in the chunk, the
    coverage rows as given). The run's ``output_sha256`` is the reply's."""
    try:
        _write_rejected(root, job_id, state, run, exc, step, message, seen)
    except Exception as fault:  # noqa: BLE001 - keeping the record never stops the count; the run says it failed
        run['rejected_not_kept'] = f'{type(fault).__name__}: {fault}'[:500]


def _write_rejected(root: Path, job_id: str, state: dict, run: dict, exc: BaseException, step: str, message: str,
                    seen: dict) -> None:
    text = seen.get('text')
    attempt = int(run.get('rejected_submits', 0)) + 1
    record = {'schema': 'secondbrain-rejected/1', 'job_id': job_id, 'run_id': run['run_id'], 'chunk': run.get('chunk'),
              'attempt': attempt, 'step': step, 'model_requested': run.get('model_requested'),
              'model_reported': seen.get('model_reported') or run.get('model_reported'),
              'task_sha256': run.get('task_sha256'), 'input_sha256': state.get('input_sha256'),
              'reply_text': text, 'reply_sha256': digest(text.encode('utf-8')) if isinstance(text, str) else None,
              'payload': seen.get('payload'), 'refusal': message[:20000],
              **(getattr(exc, 'rejected', None) or {})}
    where = location(job_id, REJECTED, f"{run['run_id']}-{attempt}.json")
    write_json(root, where, record)
    run['output_sha256'] = record['reply_sha256']
    run['rejected_answers'] = list(run.get('rejected_answers') or []) + [where]


def _read(state: dict, raw, answer, result) -> tuple:
    """``(payload, what was set aside, model_reported, attestation, usage)`` of an answer from one of the three
    sources: a host's file bytes, an object (MCP, a test), an API executor's result."""
    from .executors.api import json_problem, read_answer
    if raw is not None:
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise AnswerError(f'the answer file is not UTF-8 text: {exc}') from None
        payload, how = read_answer(text, tex_backslashes=state.get('kind') == 'export')
        if payload is None:
            raise AnswerError(f'the answer file is not JSON: {json_problem(text)}')
        return payload, how, None, None, None
    if result is not None:
        if result.status != 'completed' or not isinstance(result.output, dict):
            error = result.error or {}
            raise _Unread(str(error.get('message') or 'the executor returned no JSON object'),
                          error.get('program_error'))
        return result.output, result.unwrapped, result.model_reported, result.attestation, result.usage
    if not isinstance(answer, dict):
        raise AnswerError(f'a submission must be a JSON object, not {type(answer).__name__}')
    return answer, None, None, None, None


def receive(root: Path, job_id: str, run_id: str, *, role: str, answer=_NO_ANSWER, raw: bytes | None = None,
            result=None, model_reported: str | None = None, attestation: str = 'self_reported',
            usage: dict | None = None) -> dict:
    """The one way an answer of a run is taken, from any of its three sources: a host's answer file (``raw`` bytes,
    `sb job submit`), an object (``answer``, the MCP tool ``job_submit``), an API executor's result (``result``).
    Never writes knowledge.

    First the run's binding is checked (the input, the run's role and state, the job, the candidate a review
    answers): a conflict there is no verdict on the answer and is not counted. Then, in one ``try``, the steps of
    :data:`RECEIVE_STEPS`: read the answer (the one reading, :func:`executors.api.read_answer`), check its size,
    take out the fields only the program writes, run the kind's check (:data:`SUBMISSION_VALIDATORS`), store it,
    record its values (:data:`DECISION_RECORDERS`). An error in any step refuses the answer and counts it against
    the slot's budget (:func:`_refused_answer`; a host run is closed when the budget is spent, an API run is
    failed): an :class:`~secondbrain.schemas.AnswerError` a check raised as the refusal it is, any other error as a
    fault of the program, kept for the owner (``program_errors``, listed by ``sb status --owner-items``). What the failed steps changed is not kept. A refused
    answer raises a ``ValueError`` marked ``counted`` with its ``error``: the answer's own error as it was raised,
    a fault of the program as :class:`Refused`."""
    if attestation not in {'provider_response', 'self_reported', 'test'}:
        raise ValueError(f'unknown attestation: {attestation}')
    sources = (answer is not _NO_ANSWER) + (raw is not None) + (result is not None)
    if sources != 1:
        raise TypeError('receive takes one answer: raw bytes, an object, or an executor result')
    with vault_lock(root):
        state = load(root, job_id)
        input_raw = read_state(root, location(job_id, 'input.json'))
        if input_raw is None or digest(input_raw) != state.get('input_sha256'):
            raise Conflict('INPUT_CHANGED')
        run = load_run(root, job_id, run_id)
        if run['role'] != role:
            raise Conflict('RUN_ROLE_MISMATCH')
        if run['status'] not in {'started', 'submitted'}:
            raise Conflict(f'TERMINAL_RUN: {run["status"]}')
        chunk = run.get('chunk')
        slot = _slot(state, chunk, create=True)
        if run['status'] == 'started' and _job_write_blocked(root, job_id, state):
            raise Conflict('TERMINAL_JOB')
        candidate = None
        if role == 'reviewer':
            if (slot.get('candidate') or {}).get('sha256') != run.get('candidate_sha256'):
                raise Conflict('STALE_CANDIDATE')
            candidate = json.loads(_verified_bytes(root, job_id, slot['candidate']))
        elif run['status'] == 'started' and \
                (slot.get('candidate') or {}).get('sha256') != run.get('base_candidate_sha256'):
            raise Conflict('STALE_CANDIDATE')
        packet = json.loads(input_raw)
        frozen = frozen_settings(root, packet)
        step, how, written, path = 'read', None, False, location(job_id, 'out', f'{role}-{run_id}.json')
        seen = {'text': _reply_text(raw, answer, result)}  # what a refused producer answer is kept with
        try:
            payload, how, reported, given, used = _read(state, raw, answer, result)
            seen.update(payload=payload, model_reported=reported or model_reported)
            model_reported, attestation, usage = reported or model_reported, given or attestation, used or usage
            if how:
                run['unwrapped'] = how
            step = 'size'
            if len(encoded(payload)) > MAX_ANSWER_BYTES:
                raise AnswerError('the answer is larger than 8 MB; split the work into smaller batches')
            step = 'fields'
            payload, removed = _without_program_fields(payload)
            if removed:
                run['program_fields_removed'] = removed
            step = 'check'
            validate = _validator(state['kind'])
            if validate is not None:
                with config.job_settings(frozen):
                    checked = validate(packet, payload, role=role, candidate=candidate,
                                       root=root, chunk=chunk, state=state)
                payload = checked if isinstance(checked, dict) else payload
            raw_out = encoded(payload)
            if len(raw_out) > MAX_ANSWER_BYTES:
                raise AnswerError('the result is larger than 8 MB; split the work into smaller batches')
            step = 'store'
            existing = read_state(root, path)
            if run['status'] == 'submitted':
                if existing != raw_out:
                    raise _NotAVerdict(Conflict('RUN_ALREADY_SUBMITTED'))
                return run['submit_result']
            if existing is not None and existing != raw_out:
                raise _NotAVerdict(Conflict('RUN_ALREADY_SUBMITTED'))
            ref = {'path': path, 'sha256': digest(raw_out), 'run_id': run_id,
                   'input_sha256': state['input_sha256']}
            if role == 'reviewer':
                ref['candidate_sha256'] = run['candidate_sha256']
            if existing is None:
                write_json(root, path, payload)
                written = True
            if role == 'reviewer':
                slot['review'] = ref
                slot['status'] = 'review_submitted'
            else:
                slot['candidate'] = ref
                slot['review'] = None
                slot['status'] = 'proposed'
            if chunk is None:
                state['status'] = slot['status']
            run.update(status='submitted', output_sha256=ref['sha256'], ended_at=now(),
                       model_reported=model_reported or run.get('model_reported'),
                       attestation=attestation, usage=usage)
            if role == 'reviewer' and slot['candidate'].get('run_id'):  # a program-made input has no producer
                producer = load_run(root, job_id, slot['candidate']['run_id'])
                rules = policy_of(packet)
                a = gates.model_family(producer.get('model_reported') or producer.get('model_requested'),
                                       rules.model_families)
                b = gates.model_family(run.get('model_reported') or run.get('model_requested'),
                                       rules.model_families)
                run['same_family'] = a is not None and a == b
                try:
                    gates.assess_families(producer, run, rules)
                except ValueError as exc:
                    run['independence_error'] = str(exc)
                if run.get('same_family'):
                    slot['same_family'] = True
                    state['same_family'] = True
            step = 'record'
            recorder = _recorder(state['kind'])
            if recorder is not None:
                entries = list(state.get('decisions') or [])
                before = len(entries)
                with config.job_settings(frozen):
                    recorder(packet, payload, role=role, candidate=candidate, root=root, chunk=chunk, state=state,
                             run=run, entries=entries)
                state['decisions'] = entries
                if len(entries) > before:  # what this answer put in the record, voided if it is set aside
                    run['recorded'] = [e['n'] for e in entries[before:]]
            done = {'job_id': job_id, 'run_id': run_id, 'chunk': chunk, 'status': slot['status'],
                    'artifact': ref, 'written_to_knowledge': False}
            run['submit_result'] = done
            write_json(root, run_location(job_id, run_id), run)
            state['updated_at'] = now()
            write_json(root, location(job_id), state)
            return done
        except _NotAVerdict as conflict:
            raise conflict.conflict from None
        except Exception as exc:  # noqa: BLE001 - the one gate: every error while an answer is taken is counted
            return _refuse_received(root, job_id, run_id, exc, step, how, written and path, result is not None,
                                    seen)


def _refuse_received(root: Path, job_id: str, run_id: str, exc: BaseException, step: str, how, written,
                     executor: bool, seen: dict | None = None):
    """The answer of ``run_id`` failed at ``step`` of :func:`receive` with ``exc``: count it, from the state as it
    was before this answer (what the failed steps changed is dropped, the stored file too), and raise
    :class:`Refused`. An :class:`~secondbrain.schemas.AnswerError` is the refusal it is; any other error is kept as a
    fault of the program."""
    state, run = load(root, job_id), load_run(root, job_id, run_id)
    if written:
        remove_state(root, written)
    if how:
        run['unwrapped'] = how
    fault = getattr(exc, 'fault', None) if isinstance(exc, _Unread) else None
    if fault is None and not (isinstance(exc, AnswerError) and step in _ANSWER_STEPS):
        fault = {**fault_of(exc), 'stage': step}
    message = str(exc) if fault is None else _record_fault(state, run, fault)
    if fault is not None and isinstance(exc, _Unread):
        message += f' ({exc})'
    if run['role'] == 'producer':
        _keep_rejected(root, job_id, state, run, exc, step, message, seen or {})
    given_up = _refused_answer(root, job_id, state, run, message,
                               getattr(exc, 'keep', None) if fault is None else None)
    if executor and run['status'] == 'started':  # an API run takes one answer: refused, it is over
        run.update(status='failed', ended_at=now(), error={'code': 'INVALID_OUTPUT', 'message': message[:4000],
                                                           'retryable': True, 'counted': True})
        write_json(root, run_location(job_id, run['run_id']), run)
    if fault is None:  # the answer's own error: raised as it is, marked as counted
        exc.counted, exc.given_up = True, given_up
        exc.error = {'code': 'INVALID_OUTPUT', 'message': message[:4000], 'retryable': True, 'counted': True}
        raise exc
    raise Refused(message, given_up=given_up, fault=fault) from exc


# --------------------------------------------------------------------------
# After an answer is taken: one guard
# --------------------------------------------------------------------------
#: Attribute an error carries once it has passed a :func:`reading` mark: ``(stage, aside)``; ``None`` for an error of
#: a model call (:func:`from_model_call`), which is not the guard's.
_TAKEN = '_sb_after_taken'

#: How many answers one ``advance`` may set aside before it parks the job instead (each is counted, so a slot's
#: budget ends it long before this).
GUARD_LIMIT = 200


def _mark(exc: BaseException, where) -> BaseException:
    if not hasattr(exc, _TAKEN):
        try:
            setattr(exc, _TAKEN, where)
        except AttributeError:  # an exception that takes no attributes stays unmarked, and is raised as it is
            pass
    return exc


#: Whether the code running now is inside :func:`guarded` (a mark outside it has no guard to take its errors).
_GUARDED = contextvars.ContextVar('secondbrain_guarded', default=False)


class MarkOutsideGuard(RuntimeError):
    """A :func:`reading` mark was reached outside :func:`guarded` while ``SECONDBRAIN_STRICT_MARKS`` is set (the
    tests set it): every way into code that reads taken answers must go through the guard."""


class reading:
    """Mark code of an ``advance`` that reads answers the job took. It catches nothing: an error that passes the
    mark (the innermost one) is the guard's (:func:`guarded`). ``aside`` names the answer to set aside for it,
    ``(slot, role)`` (``slot`` None for the job's own), or a function of the job state that returns that or None;
    without one the job is parked. A mark has effect only inside the guard; reached outside it, the mark leaves an
    error to its caller as plain code would, and with ``SECONDBRAIN_STRICT_MARKS`` set it raises
    :class:`MarkOutsideGuard` at once. ``at`` is where the mark stands (file and line), for the tests' count of the
    marks a flow reached."""

    def __init__(self, stage: str, aside=None):
        caller = sys._getframe(1)
        self.stage, self.aside, self.at = stage, aside, (caller.f_code.co_filename, caller.f_lineno)
        if not _GUARDED.get() and os.environ.get('SECONDBRAIN_STRICT_MARKS'):
            raise MarkOutsideGuard(f'the mark {stage!r} at {self.at[0]}:{self.at[1]} was reached outside the guard')

    def __enter__(self):
        return self

    def __exit__(self, kind, exc, tb):
        if isinstance(exc, Exception):
            _mark(exc, (self.stage, self.aside, self.at))
        return False


def from_model_call(exc: BaseException) -> BaseException:
    """Mark an error of a model call (the executor, the hand-off, its answer taken at the entry point): the guard
    raises it as it is, wherever it was called."""
    return _mark(exc, None)


def guarded(root: Path, job_id: str, advance_once):
    """The one guard of the layer after an answer is taken, around every kind's ``advance`` (``advance_once``, safe
    to run again). An error that passed a :func:`reading` mark is counted, never raised: when it names an answer of
    a slot (the newest one, or the round's one candidate or review), that answer is set aside and counted
    (:func:`set_aside`) and the advance runs again, so the step is asked again until the budget is spent; when it
    names none (a stage that reads many answers), the job is parked (:func:`park`) and the advance returns. A
    :class:`~secondbrain.store.Halt` still stops the job, listed for the owner (:func:`note_halt`). Any other error
    (one of a model call, or met before any answer is read) is raised as it is. An error of the same stage, type and
    message as one that already set an answer aside is not that answer's: the job is parked, and no further answer
    is set aside for it. Once an advance runs through, a parking that is over is cleared, from the job, from what
    the advance returns and from the owner's list."""
    from .store import Halt
    token = _GUARDED.set(True)
    try:
        for _ in range(GUARD_LIMIT):
            try:
                out = advance_once()
            except Halt as exc:
                note_halt(root, job_id, exc)
                raise
            except Exception as exc:
                where = getattr(exc, _TAKEN, None)
                if not where:
                    raise
                stage, aside = where[0], where[1]
                if _set_aside_before(load(root, job_id), stage, exc):
                    return park(root, job_id, exc, stage)
                target = aside(load(root, job_id)) if callable(aside) else aside
                if target and set_aside(root, job_id, target[0], target[1], exc, stage=stage):
                    continue
                return park(root, job_id, exc, stage)
            if isinstance(out, dict) and load(root, job_id).get('parked'):
                _unpark(root, job_id)  # the stage that parked it passed this time
                out = {k: v for k, v in out.items() if k != 'parked'}
            return out
        return park(root, job_id, RuntimeError(f'more than {GUARD_LIMIT} answers set aside in one advance'), 'guard')
    finally:
        _GUARDED.reset(token)


def _set_aside_before(state: dict, stage: str, exc: BaseException) -> bool:
    """Whether an error of this stage, type and message already set an answer of the job aside."""
    fault = {**fault_of(exc), 'stage': stage}
    return any(row.get('set_aside') and (row.get('stage'), row.get('type'), row.get('message')) ==
               (stage, fault['type'], fault['message']) for row in state.get('program_errors') or [])


def _unpark(root: Path, job_id: str) -> None:
    """The job is parked no more: its parking and the owner's item for it go."""
    with vault_lock(root):
        state = load(root, job_id)
        state.pop('parked', None)
        state['program_errors'] = [r for r in state.get('program_errors') or [] if not r.get('parked')]
        write_json(root, location(job_id), state)


def park(root: Path, job_id: str, exc: BaseException, stage: str) -> dict:
    """The job met ``exc`` at ``stage`` while it read answers it took, and no one answer is to blame: it waits for
    the owner. The fault is kept (``program_errors``, listed by ``sb status --owner-items`` with its stage; the same
    fault again is counted on ``parked``, not listed twice), and the advance returns the job as it is."""
    fault = {**fault_of(exc), 'stage': stage}
    with vault_lock(root):
        state = load(root, job_id)
        before = state.get('parked') or {}
        same = (before.get('stage'), before.get('type'), before.get('message')) == \
            (stage, fault['type'], fault['message'])
        if not same:
            state['program_errors'] = list(state.get('program_errors') or []) + [
                {**fault, 'slot': None, 'role': None, 'step': stage, 'run_id': None, 'at': now(), 'parked': True}]
        state['parked'] = {**fault, 'at': now(), 'times': int(before.get('times', 0)) + 1 if same else 1}
        write_json(root, location(job_id), state)
    return load(root, job_id)


def note_halt(root: Path, job_id: str, exc: BaseException) -> None:
    """List a stop on purpose for the owner (``halts`` of the job, ``kind: job_stopped`` in ``sb status
    --owner-items``): the same stop again is counted, not listed twice. A job that cannot be read is left alone."""
    try:
        with vault_lock(root):
            state = load(root, job_id)
            rows = list(state.get('halts') or [])
            key = (type(exc).__name__, str(exc)[:500])
            if rows and (rows[-1].get('type'), rows[-1].get('message')) == key:
                rows[-1] = {**rows[-1], 'times': int(rows[-1].get('times', 1)) + 1, 'at': now()}
            else:
                rows.append({'type': key[0], 'message': key[1], 'times': 1, 'at': now()})
            state['halts'] = rows
            write_json(root, location(job_id), state)
    except (OSError, ValueError, KeyError):
        pass


def set_aside(root: Path, job_id: str, chunk: str | None, role: str, exc: BaseException, *, stage: str) -> bool:
    """The guard's count of an answer the program failed to read after it took it (:func:`guarded`), the same as
    the entry point's for one it failed to check: the slot ``chunk`` (None: the job's own, as a maintenance job
    keeps its one candidate and review) no longer holds ``role``'s answer (a producer's answer takes its review with
    it), the answer's run is failed, the fault is kept for the owner (``program_errors``, with ``stage``), and the
    answer is counted against the slot's budget, so the step is asked again until the budget is spent. False when
    the slot holds no answer of ``role``, or while a write is under way (``commit_intent``: its frozen plan names the
    answers it writes, so none is taken away; the job is parked, and the next advance finishes the write)."""
    with vault_lock(root):
        state = load(root, job_id)
        if state.get('commit_intent'):
            return False
        slot = state if chunk is None else (state.get('chunks') or {}).get(chunk)
        field = 'review' if role == 'reviewer' else 'candidate'
        ref = (slot or {}).get(field) or {}
        if not ref.get('run_id'):
            return False
        run = load_run(root, job_id, ref['run_id'])
        message = _record_fault(state, run, {**fault_of(exc), 'stage': stage})
        state['program_errors'][-1]['set_aside'] = True
        review = (slot.get('review') or {}).get('run_id') if role == 'producer' else None
        _void_recorded(root, job_id, state, chunk, [run] + ([load_run(root, job_id, review)] if review else []))
        slot[field] = None
        if role == 'producer':
            slot['review'] = None
        slot['status'] = 'proposed' if role == 'reviewer' else 'prepared'
        run.update(status='failed', ended_at=now(), set_aside=stage, error={
            'code': 'INVALID_OUTPUT', 'message': message[:4000], 'retryable': True, 'counted': True,
            'program_error': run['program_error']})
        _refused_answer(root, job_id, state, run, message)
        return True


def _void_recorded(root: Path, job_id: str, state: dict, chunk: str | None, runs: list) -> None:
    """The decision record no longer counts what the answers of ``runs`` put in it when they were taken."""
    from . import decisions
    entries = list(state.get('decisions') or [])
    for run in runs:
        decisions.void(entries, run.get('recorded') or [], role=run['role'], slot=chunk, run=run['run_id'],
                       reason='set_aside')
    state['decisions'] = entries


def submit(root: Path, job_id: str, payload, *, role: str, run_id: str, model_reported: str | None = None,
           attestation: str = 'self_reported', usage: dict | None = None) -> dict:
    """An answer given as an object, through :func:`receive` (the one entry point)."""
    return receive(root, job_id, run_id, role=role, answer=payload, model_reported=model_reported,
                   attestation=attestation, usage=usage)


def put_program_candidate(root: Path, job_id: str, chunk: str, payload: dict) -> dict:
    """Store input the program made for a reviewer-only step (the ingest source check) as ``chunk``'s candidate.

    The reviewer's answer is then bound to it like any review to its candidate.
    Returns the existing ref when the slot already has one.
    """
    with vault_lock(root):
        state = load(root, job_id)
        if _job_write_blocked(root, job_id, state):
            raise Conflict('TERMINAL_JOB')
        slot = _slot(state, chunk, create=True)
        if slot.get('candidate'):
            return slot['candidate']
        raw = encoded(payload)
        path = location(job_id, 'out', f'program-{chunk}.json')
        write_json(root, path, payload)
        slot.update(candidate={'path': path, 'sha256': digest(raw), 'run_id': None,
                               'input_sha256': state['input_sha256'], 'by': 'program'},
                    review=None, status='proposed')
        state['updated_at'] = now()
        write_json(root, location(job_id), state)
        return slot['candidate']


def _verified_bytes(root: Path, job_id: str, ref: dict) -> bytes:
    prefix = f'jobs/{job_name(job_id)}/'
    if not isinstance(ref, dict) or not str(ref.get('path', '')).startswith(prefix):
        raise ValueError('job result path is outside the job folder')
    raw = read_state(root, ref['path'])
    if raw is None or digest(raw) != ref.get('sha256'):
        raise Conflict('A job result was modified after it was recorded')
    return raw


def artifact(root: Path, state: dict, kind: str, chunk: str | None = None) -> dict:
    ref = _slot(state, chunk).get(kind)
    if not ref:
        raise ValueError(f'The job has no {kind} yet')
    return json.loads(_verified_bytes(root, state['job_id'], ref))


def require_independent_review(root: Path, state: dict, chunk: str | None = None) -> dict:
    """Return the review after the independence gate accepts the pair."""
    input_raw = read_state(root, location(state['job_id'], 'input.json'))
    if input_raw is None or digest(input_raw) != state.get('input_sha256'):
        raise Conflict('INPUT_CHANGED')
    slot = _slot(state, chunk)
    candidate_ref, review_ref = slot.get('candidate'), slot.get('review')
    if not candidate_ref or not review_ref:
        raise Conflict('Missing candidate or review')
    _verified_bytes(root, state['job_id'], candidate_ref)
    review_raw = _verified_bytes(root, state['job_id'], review_ref)
    if candidate_ref.get('by') == 'program' and candidate_ref.get('run_id') is None:
        return json.loads(review_raw)  # the program made the input (put_program_candidate): no producer to differ from
    producer = load_run(root, state['job_id'], candidate_ref['run_id'])
    reviewer = load_run(root, state['job_id'], review_ref['run_id'])
    gates.check_independence(state['input_sha256'], candidate_ref, review_ref,
                             producer, reviewer, policy_of(json.loads(input_raw)))
    return json.loads(review_raw)


# --------------------------------------------------------------------------
# Completion transactions
# --------------------------------------------------------------------------
def _bindings(state: dict) -> dict:
    """Candidate/review hashes per slot that a frozen write plan depends on."""
    out = {}
    if state.get('candidate') and state.get('review'):
        out['-'] = {'candidate': state['candidate']['sha256'], 'review': state['review']['sha256']}
    for chunk, slot in sorted((state.get('chunks') or {}).items()):
        if slot.get('candidate') and slot.get('review'):
            out[chunk] = {'candidate': slot['candidate']['sha256'], 'review': slot['review']['sha256']}
    return out


def _encode_changes(changes: list[Change]) -> list[dict]:
    return [{'path': c.relative_path, 'before_sha256': c.before_sha256,
             'after': None if c.after is None else b64encode(c.after).decode('ascii')} for c in changes]


def _decode_changes(rows: list[dict]) -> list[Change]:
    return [Change(c['path'], c['before_sha256'],
                   None if c['after'] is None else b64decode(c['after'], validate=True)) for c in rows]


def finish_transaction(root: Path, job_id: str, result: dict, changes: list[Change], *,
                       checks: dict | None = None) -> dict:
    """Freeze the completion state before writing any knowledge file.

    A crash after the file transaction but before updating the job is resumed
    from this intent, without rebuilding a plan from already changed files.
    """
    intent = {'result': result, 'checks': dict(checks or {}), 'changes': _encode_changes(changes)}
    with vault_lock(root):
        state = load(root, job_id)
        if state.get('commit_intent'):
            raise Conflict('The job already has a frozen write plan; resume it')
        intent['bindings'] = _bindings(state)
        if not intent['bindings']:
            raise Conflict('No reviewed candidate: nothing can be written')
        write_json(root, location(job_id, 'commit-intent.json'), intent)
        state.update(status='applying', updated_at=now(),
                     commit_intent=digest(read_state(root, location(job_id, 'commit-intent.json'))))
        write_json(root, location(job_id), state)
    return resume_transaction(root, job_id)


def archive(root: Path, job_id: str, *, reason: str) -> dict:
    """Move a job that left nothing in the vault aside, so its id can be used again.

    Allowed when the job never wrote (no transaction) or its transaction was
    rolled back. The job directory is renamed to ``<id>.reset-<UTC time>``
    with its runs, packets and journal, so the history stays readable.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('reset needs a reason')
    with vault_lock(root):
        state = load(root, job_id)
        raw = read_state(root, location(job_id, 'transaction.json'))
        journal = json.loads(raw) if raw is not None else None
        if journal is not None and journal.get('status') != 'rolled_back':
            raise Conflict(f'Job {job_id} has written to the vault ({journal.get("status")}); '
                           'roll it back first: sb recover <job> --rollback')
        if state.get('commit_intent') and journal is None:
            raise Conflict(f'Job {job_id} has a frozen write plan; resume or roll it back first')
        if state.get('pruned'):
            raise Conflict(f'Job {job_id} wrote to the vault; its rollback journal was pruned, so it stays')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        target = job_name(f'{job_id}.reset-{stamp}')
        state.update(job_id=target, status='archived', updated_at=now(),
                     archived={'original_id': job_id, 'reason': reason.strip(), 'at': now(), 'was': state['status']})
        write_json(root, location(job_id), state)
        with _state_dir(root, 'jobs', create=False) as fd:
            os.rename(job_id, target, src_dir_fd=fd, dst_dir_fd=fd)
        return {'job_id': job_id, 'archived_as': target, 'was': state['archived']['was']}


def release_intent(root: Path, job_id: str, *, reason: str) -> dict:
    """Drop a frozen write plan that the store refused before writing anything.

    Used when a compare-and-swap check fails (a file changed after the plan
    was built): nothing reached the vault, so the job goes to
    ``needs_attention`` and the next ``advance`` builds a fresh plan from the
    same reviewed candidates.
    """
    with vault_lock(root):
        if read_state(root, location(job_id, 'transaction.json')) is not None:
            raise Conflict('The write already started; use sb recover to resume or roll it back')
        state = load(root, job_id)
        state.pop('commit_intent', None)
        state.update(status='needs_attention', attention=reason, updated_at=now())
        write_json(root, location(job_id), state)
        return state


def freeze_projection(root: Path, job_id: str, changes: list[Change], *, checks: dict | None = None,
                      result: dict | None = None) -> dict:
    """Freeze a deterministic projection plan before any vault write.

    Projections (refresh/daily/today/docs) are recomputed from a frozen date,
    operation options and snapshot fingerprint; the completion intent stores
    the exact byte plan, so a resumed run replays the same content. No model
    run is recorded and this path never substitutes for the review gate.
    """
    plan = dict(result or {})
    plan.setdefault('status', 'applied')
    intent = {'projection': True, 'result': plan, 'checks': dict(checks or {}),
              'changes': _encode_changes(changes)}
    read_input(root, job_id)
    with vault_lock(root):
        state = load(root, job_id)
        if state.get('kind') != PROJECTION_KIND:
            raise ValueError('only deterministic projection jobs can freeze a projection plan')
        if state.get('commit_intent'):
            raise Conflict('The job already has a frozen write plan; resume it')
        for change in changes:
            if not _projection_allowed(root, change.relative_path):
                raise ValueError(f'a deterministic projection cannot write this path: {change.relative_path}')
        if not changes:
            state.update(**plan, applied_paths=[], updated_at=now())
            write_json(root, location(job_id), state)
            return state
        write_json(root, location(job_id, 'commit-intent.json'), intent)
        state.update(status='applying',
                     commit_intent=digest(read_state(root, location(job_id, 'commit-intent.json'))),
                     updated_at=now())
        write_json(root, location(job_id), state)
    return resume_transaction(root, job_id)


def resume_transaction(root: Path, job_id: str) -> dict | None:
    state = load(root, job_id)
    if not state.get('commit_intent'):
        return None  # never wrote, or wrote and its records were pruned (``pruned``)
    raw = read_state(root, location(job_id, 'commit-intent.json'))
    if raw is None or digest(raw) != state['commit_intent']:
        raise Conflict('The frozen write plan was modified')
    intent = json.loads(raw)
    if state['status'] in {'applied', 'published', 'partial', 'rolled_back'}:
        return state
    if intent.get('projection') is True:
        if state.get('kind') != PROJECTION_KIND:
            raise Conflict('The frozen plan type does not match the job kind')
        read_input(root, job_id)
        changes = _decode_changes(intent['changes'])
        for change in changes:
            if not _projection_allowed(root, change.relative_path):
                raise ValueError(f'a deterministic projection cannot write this path: {change.relative_path}')
    else:
        if intent.get('bindings') != _bindings(state):
            raise Conflict('The frozen write plan does not match the current candidate or review')
        for slot in intent['bindings']:
            require_independent_review(root, state, None if slot == '-' else slot)
        changes = _decode_changes(intent['changes'])
    tx = apply_changes(root, job_id, changes, checks=intent.get('checks'),
                       operation=intent.get('operation'))
    return update(root, job_id, **{**intent['result'], 'applied_paths': tx['paths']})


def list_jobs(root: Path) -> list[dict]:
    """List jobs from the vault's external state root.

    Transaction-only records (init/refresh flows write a journal without a job
    state) are listed as ``kind: transaction``; a broken job entry is reported
    as ``unreadable`` with its reason. A job retried by a child that still
    counts (not rolled back or set aside) shows ``retried_by`` and an empty
    ``pending``: its open items are the child's now.
    """
    out: list[dict] = []
    try:
        with _state_dir(root, 'jobs', create=False) as fd:
            entries = sorted(os.scandir(fd), key=lambda e: e.name)
            for entry in entries:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
                job_id = entry.name
                try:
                    if read_state(root, location(job_id)) is None:
                        raw = read_state(root, location(job_id, 'transaction.json'))
                        if raw is not None:
                            transaction = json.loads(raw)
                            if transaction.get('schema') not in {'secondbrain-transaction/1', 'secondbrain-transaction/2'} or transaction.get('job_id') != job_id:
                                raise ValueError('transaction record has the wrong identity or format')
                            out.append({'job_id': job_id, 'kind': 'transaction',
                                        'status': transaction['status'],
                                        'paths': [x['path'] for x in transaction['changes']]})
                            continue
                    state = load(root, job_id)
                    entry = {k: state.get(k) for k in ['job_id', 'kind', 'status', 'created_at', 'updated_at', 'pending']}
                    if state.get('retried_by'):
                        entry['retried_by'] = state['retried_by']
                    out.append(entry)
                except (ValueError, OSError) as exc:
                    out.append({'job_id': job_id, 'status': 'unreadable', 'error': str(exc)})
    except FileNotFoundError:
        return []
    # a job whose pending items a retry child took over has none of its own left open
    statuses = {e['job_id']: e.get('status') for e in out}
    for entry in out:
        if statuses.get(entry.get('retried_by')) not in (None, 'rolled_back', 'unreadable'):
            entry['pending'] = []
    return out


# --------------------------------------------------------------------------
# Pruning rollback records
# --------------------------------------------------------------------------
#: The files of a finished write that only rollback and resume read: the journal with the
#: before and after bytes of every changed file, and the frozen write plan.
PRUNABLE_FILES = ('transaction.json', 'commit-intent.json')
#: Statuses of a job whose write finished.
WRITTEN_STATUSES = {'applied', 'partial', 'published'}


def _prune_group(state: dict) -> str:
    """Jobs of one group follow each other; the newest of a group keeps its records.

    An ingest job and its retry children form one group (one source); other
    jobs group by kind and the operation their id starts with (``moc``,
    ``daily``, ``classify`` ...).
    """
    job_id = state['job_id']
    if state.get('kind') == 'ingest':
        return re.sub(r'-retry\d+$', '', job_id)
    return f"{state.get('kind')}:{job_id.split('-', 1)[0]}"


def prune(root: Path, *, days: int = 30, apply: bool = False, now_utc: datetime | None = None) -> dict:
    """List, or with ``apply`` delete, the rollback records of old finished writes.

    A job qualifies when its write finished (its journal says ``applied``),
    its last update is more than ``days`` days old, and it is not the newest
    job of its group (:func:`_prune_group`), so the last write of every source
    and operation can still be undone. Only :data:`PRUNABLE_FILES` go; the job
    state, its runs and packets stay, and the state records ``pruned`` so
    that `sb recover` can say why it cannot roll the job back. Nothing is ever
    pruned without ``apply``.
    """
    if not isinstance(days, int) or days < 0:
        raise ValueError('days must be 0 or more')
    moment = now_utc or datetime.now(timezone.utc)
    states = []
    for entry in list_jobs(root):
        if entry.get('kind') in (None, 'transaction') or entry.get('status') == 'unreadable':
            continue
        states.append(load(root, entry['job_id']))
    newest: dict = {}
    for state in states:
        if state.get('status') not in WRITTEN_STATUSES:
            continue  # the newest write of a group keeps its records, not a job that never wrote
        key = _prune_group(state)
        stamp = state.get('created_at') or ''
        if key not in newest or stamp > newest[key][0]:
            newest[key] = (stamp, state['job_id'])
    chosen = []
    for state in states:
        job_id = state['job_id']
        if state.get('pruned') or state.get('status') not in WRITTEN_STATUSES or \
                newest.get(_prune_group(state), ('', ''))[1] == job_id:
            continue
        updated = datetime.fromisoformat(state.get('updated_at') or state['created_at'])
        if (moment - updated).total_seconds() < days * 86400:
            continue
        raw = read_state(root, location(job_id, 'transaction.json'))
        if raw is None or json.loads(raw).get('status') != 'applied':
            continue
        sizes = {}
        for name in PRUNABLE_FILES:
            data = raw if name == 'transaction.json' else read_state(root, location(job_id, name))
            if data is not None:
                sizes[name] = len(data)
        chosen.append({'job_id': job_id, 'kind': state.get('kind'), 'status': state['status'],
                       'updated_at': state.get('updated_at'), 'files': sorted(sizes),
                       'mb': round(sum(sizes.values()) / 1e6, 2)})
    out = {'apply': apply, 'days': days, 'jobs': chosen, 'mb': round(sum(j['mb'] for j in chosen), 2),
           'kept_newest': sorted(job for _, job in newest.values())}
    if not apply:
        if chosen:
            out['hint'] = ('sb job prune --apply deletes these rollback journals and write plans; '
                           'those writes can then no longer be undone with sb recover --rollback')
        return out
    with vault_lock(root):
        for job in chosen:
            freed = sum(remove_state(root, location(job['job_id'], name)) for name in PRUNABLE_FILES)
            state = load(root, job['job_id'])
            state.pop('commit_intent', None)
            state['pruned'] = {'at': now(), 'files': job['files'], 'bytes': freed}
            write_json(root, location(job['job_id']), state)
    return out


def pruned_note(root: Path, job_id: str) -> str | None:
    """Why ``job_id`` cannot be resumed or rolled back because its records were pruned, or None."""
    try:
        state = load(root, job_id)
    except ValueError:
        return None
    pruned = state.get('pruned')
    if not pruned:
        return None
    return (f'The rollback journal of {job_id} was pruned on {pruned["at"][:10]} (sb job prune), so its write '
            'can no longer be undone with sb recover; the cards it wrote stay. Undo them by hand if needed.')


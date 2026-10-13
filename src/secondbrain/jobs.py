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

import importlib
import json
import os
import re
import uuid
from base64 import b64decode, b64encode
from datetime import datetime, timezone
from pathlib import Path

from . import config, gates
from . import layout as layouts
from .store import (Change, Conflict, _state_dir, apply_changes, digest, job_name,
                    read_state, relative, vault_lock, write_json)

SCHEMA = "secondbrain-job/3"
RUN_SCHEMA = "secondbrain-run/1"

#: Job kind for deterministic projections (refresh/daily/today/docs). These are
#: computed locally from frozen inputs; no model run exists, and their frozen
#: plan never substitutes for the review gate of semantic jobs.
PROJECTION_KIND = "projection"

#: Kind -> "module:function" validating a submission before it is persisted.
#: Signature: ``fn(packet, payload, *, role, candidate, root, chunk, state) -> payload``;
#: the returned payload (possibly normalized) is what gets stored.
SUBMISSION_VALIDATORS = {
    'classification': 'secondbrain.classification:validate_submission',
    'ingest': 'secondbrain.pipelines.ingest:validate_submission',
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
            raise ValueError('非法任务文件名')
    return '/'.join(['jobs', job_name(job_id), *parts])


def run_location(job_id: str, run_id: str) -> str:
    return location(job_id, 'runs', f'{_run_name(run_id)}.json')


def _run_name(run_id: str) -> str:
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f'非法run_id：{run_id!r}')
    return run_id


def new_run_id() -> str:
    return f'r-{uuid.uuid4().hex[:16]}'


def load(root: Path, job_id: str) -> dict:
    raw = read_state(root, location(job_id))
    if raw is None:
        raise ValueError(f'任务不存在：{job_id}')
    state = json.loads(raw)
    if state.get('job_id') != job_id:
        raise ValueError('任务身份不匹配')
    if state.get('schema') != SCHEMA:
        raise ValueError(f'任务格式不是{SCHEMA}（{state.get("schema")!r}）')
    return state


def read_input(root: Path, job_id: str) -> dict:
    state = load(root, job_id)
    raw = read_state(root, location(job_id, 'input.json'))
    if raw is None or digest(raw) != state['input_sha256']:
        raise Conflict('任务输入被修改，请重新准备任务')
    return json.loads(raw)


def create(root: Path, job_id: str, kind: str, packet: dict, **extra) -> dict:
    """Freeze ``packet`` as the job input; idempotent for identical input."""
    raw = encoded(packet)
    with vault_lock(root):
        old = read_state(root, location(job_id))
        if old is not None:
            state = json.loads(old)
            if state.get('schema') != SCHEMA:
                raise ValueError(f'任务{job_id}为旧格式（{state.get("schema")!r}）')
            if state.get('input_sha256') != digest(raw) or state.get('kind') != kind:
                raise Conflict('任务ID已绑定其他输入')
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
              'candidate', 'review', 'chunks'}


def update(root: Path, job_id: str, **fields) -> dict:
    forbidden = _PROTECTED & fields.keys()
    if forbidden:
        raise ValueError(f'不能改任务身份或结果绑定：{sorted(forbidden)}')
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
            raise ValueError(f'任务没有该块：{chunk}')
        chunks[chunk] = {'candidate': None, 'review': None, 'status': 'prepared', 'round': 0}
    return chunks[chunk]


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------
def load_run(root: Path, job_id: str, run_id: str) -> dict:
    raw = read_state(root, run_location(job_id, run_id))
    if raw is None:
        raise ValueError(f'run不存在：{run_id}')
    run = json.loads(raw)
    if run.get('job_id') != job_id or run.get('run_id') != run_id or run.get('schema') != RUN_SCHEMA:
        raise ValueError('run记录身份不匹配')
    return run


def list_runs(root: Path, job_id: str) -> list[dict]:
    return [load_run(root, job_id, rid) for rid in load(root, job_id).get('runs', [])]


def start_run(root: Path, job_id: str, *, role: str, step: str, executor: str,
              model_requested: str | None, endpoint_host: str | None = None,
              prompt_sha256: str | None = None, task_sha256: str | None = None,
              chunk: str | None = None, run_id: str | None = None) -> dict:
    """Record a run before its executor is called; returns the run record."""
    if role not in {'producer', 'reviewer'}:
        raise ValueError(f'未知角色：{role}')
    if step not in {'produce', 'review', 'repair'}:
        raise ValueError(f'未知步骤：{step}')
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
            raise Conflict('REPAIR_REQUIRED: 已有候选，只能以repair步骤重产')
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
            raise Conflict(f'run已结束（{run["status"]}），不能改为{status}')
        run.update(fields, status=status, ended_at=now())
        write_json(root, run_location(job_id, run_id), run)
        return run


def fail_run(root: Path, job_id: str, run_id: str, *, error: dict) -> dict:
    """Mark an executor failure; the job stays retryable with a new run."""
    return _close_run(root, job_id, run_id, 'failed', error=dict(error))


def abandon_run(root: Path, job_id: str, run_id: str, *, reason: str) -> dict:
    """Give up a run that never submitted (e.g. a host sub-agent that vanished)."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('abandon需要具体原因')
    return _close_run(root, job_id, run_id, 'abandoned',
                      error={'code': 'ABANDONED', 'message': reason, 'retryable': True})


def _validator(kind: str):
    target = SUBMISSION_VALIDATORS.get(kind)
    if not target:
        return None
    module, name = target.split(':')
    return getattr(importlib.import_module(module), name)


def submit(root: Path, job_id: str, payload: dict, *, role: str, run_id: str,
           model_reported: str | None = None, attestation: str = 'self_reported',
           usage: dict | None = None) -> dict:
    """Validate and persist one run's output; never writes knowledge."""
    if not isinstance(payload, dict):
        raise ValueError('提交内容必须是JSON对象')
    if attestation not in {'provider_response', 'self_reported', 'test'}:
        raise ValueError(f'未知attestation：{attestation}')
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
        validate = _validator(state['kind'])
        if validate is not None:
            checked = validate(json.loads(input_raw), payload, role=role, candidate=candidate,
                               root=root, chunk=chunk, state=state)
            payload = checked if isinstance(checked, dict) else payload
        raw = encoded(payload)
        if len(raw) > 8_000_000:
            raise ValueError('结果超过8MB，请拆成独立处理批次')
        path = location(job_id, 'out', f'{role}-{run_id}.json')
        existing = read_state(root, path)
        if run['status'] == 'submitted':
            if existing != raw:
                raise Conflict('RUN_ALREADY_SUBMITTED')
            return run['submit_result']
        if existing is not None and existing != raw:
            raise Conflict('RUN_ALREADY_SUBMITTED')
        ref = {'path': path, 'sha256': digest(raw), 'run_id': run_id,
               'input_sha256': state['input_sha256']}
        if role == 'reviewer':
            ref['candidate_sha256'] = run['candidate_sha256']
        if existing is None:
            write_json(root, path, payload)
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
        if role == 'reviewer':
            producer = load_run(root, job_id, slot['candidate']['run_id'])
            policy = config.review_policy()
            a = gates.model_family(producer.get('model_reported') or producer.get('model_requested'),
                                   policy.model_families)
            b = gates.model_family(run.get('model_reported') or run.get('model_requested'),
                                   policy.model_families)
            run['same_family'] = a is not None and a == b
            try:
                gates.assess_families(producer, run, policy)
            except ValueError as exc:
                run['independence_error'] = str(exc)
            if run.get('same_family'):
                slot['same_family'] = True
                state['same_family'] = True
        result = {'job_id': job_id, 'run_id': run_id, 'chunk': chunk, 'status': slot['status'],
                  'artifact': ref, 'written_to_knowledge': False}
        run['submit_result'] = result
        write_json(root, run_location(job_id, run_id), run)
        state['updated_at'] = now()
        write_json(root, location(job_id), state)
        return result


def _verified_bytes(root: Path, job_id: str, ref: dict) -> bytes:
    prefix = f'jobs/{job_name(job_id)}/'
    if not isinstance(ref, dict) or not str(ref.get('path', '')).startswith(prefix):
        raise ValueError('任务结果路径越界')
    raw = read_state(root, ref['path'])
    if raw is None or digest(raw) != ref.get('sha256'):
        raise Conflict('任务结果在记录后被修改')
    return raw


def artifact(root: Path, state: dict, kind: str, chunk: str | None = None) -> dict:
    ref = _slot(state, chunk).get(kind)
    if not ref:
        raise ValueError(f'任务尚无{kind}结果')
    return json.loads(_verified_bytes(root, state['job_id'], ref))


def require_independent_review(root: Path, state: dict, chunk: str | None = None) -> dict:
    """Return the review after the independence gate accepts the pair."""
    input_raw = read_state(root, location(state['job_id'], 'input.json'))
    if input_raw is None or digest(input_raw) != state.get('input_sha256'):
        raise Conflict('INPUT_CHANGED')
    slot = _slot(state, chunk)
    candidate_ref, review_ref = slot.get('candidate'), slot.get('review')
    if not candidate_ref or not review_ref:
        raise Conflict('缺少候选或复核结果')
    _verified_bytes(root, state['job_id'], candidate_ref)
    review_raw = _verified_bytes(root, state['job_id'], review_ref)
    producer = load_run(root, state['job_id'], candidate_ref['run_id'])
    reviewer = load_run(root, state['job_id'], review_ref['run_id'])
    gates.check_independence(state['input_sha256'], candidate_ref, review_ref,
                             producer, reviewer, config.review_policy())
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
            raise Conflict('任务已经有冻结写入计划，请恢复它')
        intent['bindings'] = _bindings(state)
        if not intent['bindings']:
            raise Conflict('没有已复核的候选，不能写入')
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
            raise Conflict('写入已经开始，只能用 recover 恢复或回滚')
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
            raise ValueError('只有确定性投影任务能冻结投影计划')
        if state.get('commit_intent'):
            raise Conflict('任务已经有冻结写入计划，请恢复它')
        for change in changes:
            if not _projection_allowed(root, change.relative_path):
                raise ValueError(f'确定性投影不能写该路径：{change.relative_path}')
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
        return None
    raw = read_state(root, location(job_id, 'commit-intent.json'))
    if raw is None or digest(raw) != state['commit_intent']:
        raise Conflict('冻结写入计划已改变')
    intent = json.loads(raw)
    if state['status'] in {'applied', 'published', 'partial', 'rolled_back'}:
        return state
    if intent.get('projection') is True:
        if state.get('kind') != PROJECTION_KIND:
            raise Conflict('冻结计划类型与任务类型不一致')
        read_input(root, job_id)
        changes = _decode_changes(intent['changes'])
        for change in changes:
            if not _projection_allowed(root, change.relative_path):
                raise ValueError(f'确定性投影不能写该路径：{change.relative_path}')
    else:
        if intent.get('bindings') != _bindings(state):
            raise Conflict('冻结写入计划与当前候选或复核不一致')
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
    as ``unreadable`` with its reason.
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
                                raise ValueError('事务身份或格式不匹配')
                            out.append({'job_id': job_id, 'kind': 'transaction',
                                        'status': transaction['status'],
                                        'paths': [x['path'] for x in transaction['changes']]})
                            continue
                    state = load(root, job_id)
                    out.append({k: state.get(k) for k in ['job_id', 'kind', 'status', 'created_at', 'updated_at', 'pending']})
                except (ValueError, OSError) as exc:
                    out.append({'job_id': job_id, 'status': 'unreadable', 'error': str(exc)})
    except FileNotFoundError:
        return []
    return out

"""Durable classification batches: full cards, shared domain context, no fallback labels.

A batch parent job freezes a deterministic plan (per-domain chunks of card ids)
BEFORE any child classification job is created, so a crash inside preparation can
be resumed: re-running ``prepare`` with the same job id (or any later
``resume``) only materializes the children that are still missing — a planned
card is never orphaned and then silently skipped. Historical prepared work
(prior classification jobs) is not repeated unless ``include_pending`` is
requested. Running the batch pushes every selected child through the normal
produce/review pipeline and records per-child outcomes only after an
independently bound review exists; candidate-only or error states are never
counted as reviewed, and already reviewed children are not re-run.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import classification, jobs
from .catalog import category_pairs
from .store import Conflict
from .vault import read_snapshot


def prepare(root: Path, *, job_id: str,
            domain: str | None = None, batch_size: int = 50,
            repair_invalid: bool = False, include_pending: bool = False) -> dict:
    """Freeze a batch plan, then materialize its child classification jobs.

    Called again with the exact same frozen parameters, preparation only
    completes interrupted work (creates the missing children); a parameter
    change on an existing job id is rejected explicitly instead of being
    silently ignored — the frozen plan can only be continued via ``resume``.
    """
    if not 1 <= batch_size <= 100:
        raise ValueError('每批1–100张；全文过大时通过MCP完整读取')
    jobs.job_name(job_id)
    if len(job_id) > 115:
        raise ValueError('任务ID过长：子任务ID需追加序号，总长不能超过120字符')
    snapshot = read_snapshot(root)
    classification.writable_snapshot(snapshot)
    if domain and domain not in snapshot.catalog['domains']:
        raise ValueError('未知领域')
    old = {x['job_id']: x for x in jobs.list_jobs(root)}
    if job_id in old:
        state = jobs.load(root, job_id)
        if state['kind'] != 'classification-batch':
            raise ValueError('该任务ID不是分类批次计划')
        packet = jobs.read_input(root, job_id)
        _reject_frozen_change(packet, job_id, domain, batch_size,
                              repair_invalid, include_pending)
        created, errors = _materialize(root, state, snapshot=snapshot)
        done = jobs.update(root, job_id, materialize_errors=errors)
        return {**done, 'children': packet['children'], 'resumed': True,
                'materialized_children': created,
                'prior_jobs': packet.get('prior_jobs', []),
                'skipped_ids': packet.get('skipped_ids', [])}
    attempted, prior_jobs = set(), []
    if not include_pending:
        for entry in old.values():
            if entry.get('kind') != 'classification' or entry.get('status') == 'superseded':
                continue
            packet = jobs.read_input(root, entry['job_id'])
            if bool(packet.get('repair_invalid')) != repair_invalid:
                continue
            attempted.update(c['id'] for c in packet['cards'])
            prior_jobs.append(entry['job_id'])
    by_domain, skipped = {}, []
    for card in sorted(snapshot.cards.values(), key=lambda c: (c.domain, c.id)):
        if domain and card.domain != domain:
            continue
        pair = (card.fm.get('pillar'), card.fm.get('topic'))
        eligible = (all(pair) and pair not in category_pairs(snapshot, card.domain)) if repair_invalid else not all(pair)
        if not eligible:
            continue
        if card.id in attempted:
            skipped.append(card.id)
        else:
            by_domain.setdefault(card.domain, []).append(card.id)
    children = []
    for slug, ids in by_domain.items():
        for start in range(0, len(ids), batch_size):
            selected = ids[start:start + batch_size]
            child_id = f'{job_id}-{len(children) + 1:03d}'
            children.append({'job_id': child_id, 'domain': slug, 'ids': selected,
                             'card_hashes': {i: snapshot.cards[i].sha256 for i in selected},
                             'taxonomy_sha256': classification.taxonomy_fingerprint(snapshot, [slug]),
                             'repair_invalid': repair_invalid})
    if not children:
        return {'status': 'unchanged', 'job_id': job_id, 'input_count': 0,
                'child_count': 0, 'skipped_ids': skipped, 'prior_jobs': prior_jobs}
    # Freeze the complete plan before creating any child job: a crash in the
    # create loop must never orphan a subtask that a later skip pass forgets.
    packet = {'kind': 'classification-batch', 'plan_version': 'classification-batch/1',
              'children': children, 'repair_invalid': repair_invalid, 'domain': domain,
              'batch_size': batch_size, 'include_pending': include_pending,
              'skipped_ids': skipped, 'prior_jobs': prior_jobs,
              'snapshot_fingerprint': snapshot.fingerprint}
    state = jobs.create(root, job_id, 'classification-batch', packet)
    jobs.update(root, job_id, child_count=len(children),
                input_count=sum(len(c['ids']) for c in children),
                skipped_existing_tasks=len(skipped), outcomes=[], materialize_errors={})
    created, errors = _materialize(root, state, snapshot=snapshot)
    done = jobs.update(root, job_id, materialize_errors=errors)
    return {**done, 'children': children, 'materialized_children': created,
            'prior_jobs': prior_jobs, 'skipped_ids': skipped}


def _reject_frozen_change(packet: dict, job_id: str, domain: str | None,
                          batch_size: int, repair_invalid: bool,
                          include_pending: bool) -> None:
    frozen = {'domain': packet.get('domain'), 'batch_size': packet.get('batch_size'),
              'repair_invalid': bool(packet.get('repair_invalid')),
              'include_pending': bool(packet.get('include_pending'))}
    fresh = {'domain': domain, 'batch_size': batch_size,
             'repair_invalid': repair_invalid, 'include_pending': include_pending}
    changed = sorted(k for k in frozen if frozen[k] != fresh[k])
    if changed:
        raise ValueError(
            f'任务{job_id}已冻结参数，本次{changed}与冻结计划不同；'
            f'继续请用 resume --job {job_id} 恢复本计划，或换新任务ID重新准备')


def _materialize(root: Path, state: dict, *, snapshot=None) -> tuple[int, dict]:
    """Create any planned child job that does not exist yet; verify existing ones.

    Returns ``(created_count, errors)`` where ``errors`` maps a child job id to
    the reason it could not be materialized. Errors are persisted on the parent
    so a later resume surfaces them instead of silently dropping the child.
    """
    packet = jobs.read_input(root, state['job_id'])
    created, errors = 0, {}
    for plan in packet['children']:
        child_id = plan['job_id']
        try:
            try:
                jobs.load(root, child_id)
            except ValueError:
                if snapshot is None:
                    snapshot = read_snapshot(root)
                if snapshot.fingerprint != packet['snapshot_fingerprint']:
                    raise Conflict('尚未建立的子任务所依赖的准备快照已改变，请重新准备批次')
                classification.prepare(root, ids=plan['ids'], job_id=child_id, _snapshot=snapshot,
                                       repair_invalid=bool(packet.get('repair_invalid')))
                created += 1
            else:
                verify_child(root, plan)
        except (ValueError, OSError) as exc:
            errors[child_id] = str(exc)
    jobs.update(root, state['job_id'], materialize_errors=errors)
    return created, errors


def verify_child(root: Path, plan: dict) -> dict:
    """Confirm a materialized child still matches its frozen plan entry."""
    state = jobs.load(root, plan['job_id'])
    if state['kind'] != 'classification':
        raise ValueError(f'子任务{plan["job_id"]}不是分类任务')
    packet = jobs.read_input(root, plan['job_id'])
    actual = [c['id'] for c in packet['cards']]
    if (actual != plan['ids']
            or ('card_hashes' in plan and {c['id']: c['sha256'] for c in packet['cards']} != plan['card_hashes'])
            or ('taxonomy_sha256' in plan and packet['taxonomy_sha256'] != plan['taxonomy_sha256'])
            or ('repair_invalid' in plan and packet.get('repair_invalid') != plan['repair_invalid'])):
        raise Conflict(f'子任务{plan["job_id"]}输入与冻结计划不一致，请重新准备该批次')
    return state


def summarize(root: Path, child: dict) -> dict:
    """Per-child outcome; only an independently bound review counts as reviewed.

    Records the child's live status plus accepted counts by disposition
    (assigned / gap / domainreview) and rejected rows separately, so unresolved
    items are never hidden behind a fabricated completion.
    """
    state = jobs.load(root, child['job_id'])
    verify_child(root, child)
    result = {'job_id': child['job_id'], 'status': state['status'],
              'input_count': len(child['ids']), 'reviewed': False}
    if state.get('escalation_required'):
        result['escalation_required'] = True
    if not (state.get('candidate') and state.get('review')):
        return result
    resolved = classification.reviewed_rows_with_repairs(root, child['job_id'])
    counts = Counter()
    applied = True
    for value in resolved:
        row, finding = value['row'], value['finding']
        source_state = jobs.load(root, value['job_id'])
        if source_state.get('escalation_required'):
            result['escalation_required'] = True
        if finding['decision'] == 'accept':
            counts[row['disposition']] += 1
            if row['disposition'] == 'assigned' and source_state['status'] not in {'applied', 'partial', 'published'}:
                applied = False
        else:
            counts['rejected'] += 1
    result['accepted_assignments_applied'] = applied
    result.update(reviewed=True, decisions=dict(counts),
                  assigned=counts.get('assigned', 0),
                  gap=counts.get('taxonomy_gap', 0),
                  domainreview=counts.get('needs_domain_review', 0),
                  rejected=counts.get('rejected', 0))
    return result


def _run_locked(root: Path, job_id: str, *, producer, reviewer,
        parallel: int = 1, apply: bool = False, max_batches: int | None = None) -> dict:
    """Process the frozen batch; each run touches only children that need work.

    Interrupted preparation is healed first (missing children are created).
    Children that are already fully reviewed are never re-run; a child whose
    input no longer matches its frozen plan, or that fails for any other
    reason, keeps a retryable error outcome instead of being auto-rerun.
    """
    if not 1 <= parallel <= 3:
        raise ValueError('并发批次须为1–3')
    if max_batches is not None and max_batches < 1:
        raise ValueError('max_batches须为正')
    state = jobs.load(root, job_id)
    if state['kind'] != 'classification-batch':
        raise ValueError('该任务不是分类批次计划')
    packet = jobs.read_input(root, job_id)
    children = packet['children']
    _materialize(root, state)
    outcomes = {x['job_id']: x for x in state.get('outcomes', [])}
    missing_now = {}
    for child in children:
        reason = _missing_reason(root, child)
        if reason is not None:
            missing_now[child['job_id']] = reason
            outcomes[child['job_id']] = {'job_id': child['job_id'], 'error': reason,
                                         'status': 'missing', 'reviewed': False, 'missing': True}
    selected = []
    for child in children:
        if child['job_id'] in missing_now:
            continue
        try:
            old = summarize(root, child)
            outcomes[child['job_id']] = old
        except (ValueError, OSError) as exc:
            old = {}
            outcomes[child['job_id']] = {'job_id': child['job_id'], 'error': str(exc), 'reviewed': False}
        if old.get('decisions') and not old.get('error') and (
                not apply or old.get('accepted_assignments_applied')):
            continue
        selected.append(child)
    if max_batches is not None:
        selected = selected[:max_batches]
    if selected:
        jobs.update(root, job_id, status='running')
    try:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(_process_child, root, c, producer, reviewer): c for c in selected}
            for future in as_completed(futures):
                child = futures[future]
                try:
                    outcome = future.result()
                except (ValueError, OSError) as exc:
                    outcome = {'job_id': child['job_id'], 'error': str(exc), 'reviewed': False}
                    try:
                        outcome['status'] = jobs.load(root, child['job_id']).get('status')
                    except ValueError:
                        pass
                outcomes[child['job_id']] = outcome
        if apply:
            # Model jobs can overlap; knowledge/MOC changes consume a fresh
            # snapshot serially after every model writer has exited.
            from .runner import run_job
            for child in selected:
                if outcomes[child['job_id']].get('error'):
                    continue
                try:
                    run_job(root, child['job_id'], producer=producer, reviewer=reviewer, apply=True)
                    outcomes[child['job_id']] = summarize(root, child)
                except (ValueError, OSError) as exc:
                    outcomes[child['job_id']] = {'job_id': child['job_id'], 'error': str(exc), 'reviewed': False}
    finally:
        # Write the parent only after every worker has exited: under the current
        # non-blocking lock semantics the coordinator must not hold the vault
        # lock while workers are running. Child-level durability lives in each
        # child's own state, so a crash here only loses the summary — resume
        # re-derives it without re-dispatching any model.
        _finalize(root, job_id, children, outcomes, apply)
    return jobs.load(root, job_id)


def _finalize(root: Path, job_id: str, children: list[dict], outcomes: dict, apply: bool) -> dict:
    totals = Counter()
    for value in outcomes.values():
        totals.update(value.get('decisions', {}))
    complete = (len(outcomes) == len(children)
                and not any(x.get('error') for x in outcomes.values())
                and all(x.get('reviewed') for x in outcomes.values())
                and (not apply or all(x.get('accepted_assignments_applied') for x in outcomes.values())))
    return jobs.update(root, job_id,
                       status=('processed' if apply else 'reviewed') if complete else 'partial',
                       outcomes=[outcomes[c['job_id']] for c in children if c['job_id'] in outcomes],
                       decision_counts=dict(totals),
                       reviewed_cards=sum(totals.values()),
                       accepted_cards=sum(v for k, v in totals.items() if k != 'rejected'),
                       unresolved_count=sum(totals[k] for k in ('taxonomy_gap', 'needs_domain_review', 'rejected')))


def _process_child(root: Path, child: dict, producer, reviewer) -> dict:
    from .runner import run_job
    verify_child(root, child)
    # The shared write lock is non-blocking: a short contention between
    # parallel children is retried with backoff; the model is never re-run
    # because each run's output is persisted before the next state write.
    last_exc = None
    for attempt in range(4):
        try:
            run_job(root, child['job_id'], producer=producer, reviewer=reviewer, apply=False)
            last_exc = None
            break
        except Conflict as exc:
            if '另一个SecondBrain写入正在进行' not in str(exc):
                raise
            last_exc = exc
            import time
            time.sleep(0.5 + attempt)
    if last_exc is not None:
        raise last_exc
    return summarize(root, child)


def _missing_reason(root: Path, plan: dict) -> str | None:
    try:
        verify_child(root, plan)
    except ValueError as exc:
        return str(exc)
    return None

def run(root: Path, job_id: str, *, producer, reviewer,
        parallel: int = 1, apply: bool = False, max_batches: int | None = None) -> dict:
    """Process a frozen batch under its job lock (one coordinator at a time)."""
    from .locks import job_lock
    with job_lock(root, job_id):
        return _run_locked(root, job_id, producer=producer, reviewer=reviewer,
                           parallel=parallel, apply=apply, max_batches=max_batches)

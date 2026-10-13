"""Command line entry (`sb` / `secondbrain`). Output is JSON; never interactive.

Exit codes: 0 success, 2 user or input error, 3 conflict (retry or re-prepare),
4 executor failure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from . import jobs
from .executors.base import ExecutorFailure
from .service import Service
from .store import Conflict

MODEL_KINDS = ('classify', 'curate', 'relocate', 'domcreate', 'backlog', 'resume')


def _default_vault() -> Path | None:
    value = os.environ.get('SECONDBRAIN_VAULT')
    return Path(value).expanduser() if value else None


def parser():
    p = argparse.ArgumentParser(prog='sb', description='SecondBrain: turn inbox material into reviewable atomic cards')
    p.add_argument('--vault', type=Path, default=_default_vault())
    sub = p.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='Initialize vault metadata from a reviewed catalog')
    init.add_argument('--catalog', type=Path, required=True)
    stat = sub.add_parser('status'); stat.add_argument('--job')
    search = sub.add_parser('search'); search.add_argument('query'); search.add_argument('--limit', type=int, default=12)
    read = sub.add_parser('read'); read.add_argument('ids', nargs='+')
    for name in MODEL_KINDS:
        q = sub.add_parser(name)
        if name == 'classify':
            q.add_argument('--domain'); q.add_argument('--ids', nargs='+'); q.add_argument('--limit', type=int, default=50)
            q.add_argument('--repair-invalid', action='store_true', help='Repair non-empty classifications that are no longer legal')
        if name == 'curate':
            q.add_argument('--domain', nargs='+', required=True); q.add_argument('--ids', nargs='+')
        if name == 'relocate':
            q.add_argument('--source-job', nargs='+', required=True, help='Classification jobs whose reviewed rows suggest a move')
            q.add_argument('--ids', nargs='+', help='Only these reviewed move suggestions')
        if name == 'domcreate':
            q.add_argument('--domain', required=True, help='New domain slug')
            q.add_argument('--moc', required=True, help='Title of the new domain MOC')
            q.add_argument('--question', required=True, help='One-sentence guiding question')
            q.add_argument('--track', required=True, help='Existing track id to join')
            q.add_argument('--evidence-pillar-substr', required=True,
                           help='Collect evidence cards whose pillar contains this text (any domain)')
            q.add_argument('--reason', required=True, help='Why a new domain is warranted')
        if name == 'backlog':
            q.add_argument('--domain', help='Only this domain; default: whole vault')
            q.add_argument('--batch-size', type=int, default=50)
            q.add_argument('--repair-invalid', action='store_true')
            q.add_argument('--include-pending', action='store_true')
        q.add_argument('--job', required=name == 'resume')
        q.add_argument('--prepare-only', action='store_true', help='Freeze the input; no model call')
        q.add_argument('--proposal-only', action='store_true', help='Produce and review, but do not write knowledge')
        q.add_argument('--parallel', type=int, default=1, help='Concurrent batches (1-3)')
        q.add_argument('--max-batches', type=int)
        q.add_argument('--executor', choices=['api', 'host'], help='Override [executor].default')
    refresh = sub.add_parser('refresh', help='Deterministic MOC projection; preview unless --apply')
    refresh.add_argument('--domain', action='append'); refresh.add_argument('--apply', action='store_true'); refresh.add_argument('--job')
    for name in ['today', 'docs', 'daily']:
        q = sub.add_parser(name, help='Review/entry projections; preview unless --apply')
        q.add_argument('--apply', action='store_true'); q.add_argument('--job')
    daily = sub.choices['daily']
    daily.add_argument('--refresh-mocs', action='store_true')
    daily.add_argument('--date', help='ISO date (default: today)')
    recover = sub.add_parser('recover', help='Resume or roll back a frozen transaction')
    recover.add_argument('--job', required=True); recover.add_argument('--action', choices=['resume', 'rollback'], default='resume')
    job = sub.add_parser('job', help='Inspect jobs and runs')
    job_sub = job.add_subparsers(dest='job_command', required=True)
    job_sub.add_parser('list')
    show = job_sub.add_parser('show'); show.add_argument('id')
    serve = sub.add_parser('serve', help='MCP stdio server (read-only unless --writer)')
    serve.add_argument('--writer', action='store_true')
    return p


def make_executors(args, root: Path):
    """Return ``(producer, reviewer)`` executors for this invocation."""
    raise ValueError('No executor is configured for this vault')


def _domcreate_evidence(root: Path, substr: str) -> list[str]:
    from .vault import read_snapshot
    snap = read_snapshot(root)
    ids = sorted(c.id for c in snap.cards.values() if substr in (c.fm.get('pillar') or ''))
    if not ids:
        raise ValueError(f'No card has a pillar containing {substr!r}')
    return ids


def _run_model_command(args, root: Path) -> dict:
    cmd = args.command
    job_id = args.job or f'{cmd}-{uuid.uuid4().hex[:16]}'
    if cmd == 'backlog':
        from .backlog import prepare
        result = prepare(root, job_id=job_id, domain=args.domain, batch_size=args.batch_size,
                         repair_invalid=args.repair_invalid, include_pending=args.include_pending)
    elif cmd == 'resume':
        result = jobs.load(root, job_id)
    elif cmd == 'classify':
        from .classification import prepare
        result = prepare(root, ids=args.ids, domain=args.domain, limit=args.limit, job_id=job_id,
                         repair_invalid=args.repair_invalid)
    elif cmd == 'relocate':
        from .relocate import prepare
        result = prepare(root, source_jobs=args.source_job, ids=args.ids, job_id=job_id)
    elif cmd == 'domcreate':
        from .domcreate import prepare
        result = prepare(root, domain=args.domain, moc=args.moc, question=args.question,
                         track=args.track, evidence_ids=_domcreate_evidence(root, args.evidence_pillar_substr),
                         job_id=job_id, reason=args.reason)
    else:
        from .curation import prepare
        result = prepare(root, args.domain, selected_ids=args.ids, job_id=job_id)
    if args.prepare_only or result.get('status') == 'unchanged':
        return result
    producer, reviewer = make_executors(args, root)
    kind = jobs.load(root, job_id)['kind']
    if kind == 'classification-batch':
        from .backlog import run as run_batch
        return run_batch(root, job_id, producer=producer, reviewer=reviewer, parallel=args.parallel,
                         apply=not args.proposal_only, max_batches=args.max_batches)
    from .runner import run_job
    return run_job(root, job_id, producer=producer, reviewer=reviewer, apply=not args.proposal_only)


def main(argv=None):
    args = parser().parse_args(argv)
    if args.vault is None:
        print(json.dumps({'error': 'No vault: pass --vault or set SECONDBRAIN_VAULT'}), file=sys.stderr)
        return 2
    root = args.vault.expanduser().absolute()
    try:
        cmd = args.command
        service = Service(root)
        if cmd == 'serve':
            from .mcp_server import serve
            serve(root, writer=args.writer)
            return 0
        if cmd == 'init':
            from .service import initialize
            result = initialize(root, json.loads(args.catalog.read_text()))
        elif cmd == 'status':
            result = service.status(args.job)
        elif cmd == 'search':
            result = service.search(args.query, args.limit)
        elif cmd == 'read':
            result = service.read_cards(args.ids)
        elif cmd in MODEL_KINDS:
            result = _run_model_command(args, root)
        elif cmd == 'refresh':
            from .service import refresh
            result = refresh(root, job_id=args.job or f'moc-{uuid.uuid4().hex[:16]}', domains=args.domain, apply=args.apply)
        elif cmd in {'today', 'docs', 'daily'}:
            from .projections import generate
            from datetime import date
            requested_date = date.fromisoformat(args.date) if cmd == 'daily' and args.date else None
            result = generate(root, job_id=args.job or f'{cmd}-{uuid.uuid4().hex[:16]}',
                              apply=args.apply, today=requested_date, reviews=cmd != 'docs', entries=cmd != 'today',
                              refresh_mocs=(args.refresh_mocs if cmd == 'daily' else False))
        elif cmd == 'recover':
            from .store import recover
            if args.action == 'rollback':
                result = recover(root, args.job, 'rollback')
                if _has_state(root, args.job):
                    jobs.update(root, args.job, status='rolled_back')
            else:
                result = (jobs.resume_transaction(root, args.job) if _has_state(root, args.job) else None) \
                    or recover(root, args.job, 'resume')
        elif cmd == 'job':
            if args.job_command == 'list':
                result = jobs.list_jobs(root)
            else:
                result = {**jobs.load(root, args.id), 'run_records': jobs.list_runs(root, args.id)}
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except ExecutorFailure as exc:
        print(json.dumps({'error': str(exc), 'executor_error': exc.error}, ensure_ascii=False), file=sys.stderr)
        return 4
    except Conflict as exc:
        print(json.dumps({'error': str(exc), 'conflict': True}, ensure_ascii=False), file=sys.stderr)
        return 3
    except (ValueError, OSError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


def _has_state(root: Path, job_id: str) -> bool:
    from .store import read_state
    return read_state(root, jobs.location(job_id)) is not None


if __name__ == '__main__':
    raise SystemExit(main())

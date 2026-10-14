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

from dataclasses import replace

from . import config, jobs, labels
from .executors.base import ExecutorFailure
from .service import Service
from .store import Conflict

MODEL_KINDS = ('classify', 'curate', 'relocate', 'domcreate', 'backlog', 'resume')


def _default_vault() -> Path | None:
    value = os.environ.get('SECONDBRAIN_VAULT')
    return Path(value).expanduser() if value else None


def parser():
    p = argparse.ArgumentParser(prog='sb', description='SecondBrain: turn inbox material into reviewable atomic cards')
    p.add_argument('--vault', type=Path, default=_default_vault(),
                   help='Vault folder (default: $SECONDBRAIN_VAULT, then `vault` in the config file)')
    p.add_argument('--config', type=Path, help='Config file (default: $SECONDBRAIN_CONFIG or ~/.config/secondbrain/config.toml)')
    sub = p.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='Initialize vault metadata from a reviewed catalog')
    init.add_argument('--catalog', type=Path, required=True)
    stat = sub.add_parser('status'); stat.add_argument('--job')
    lang = sub.add_parser('language', help="The vault's generated-text language; --set switches it")
    lang.add_argument('--set', choices=['en', 'zh'], dest='set_language',
                      help='Record this language in the catalog (then rewrite pages with refresh and docs)')
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
    for name, text in [('today', "Today's review page"),
                       ('docs', 'HOME and the generated blocks of [layout] entry_docs'),
                       ('daily', 'today and docs together')]:
        q = sub.add_parser(name, help=text + '; preview unless --apply')
        q.add_argument('--apply', action='store_true'); q.add_argument('--job')
    daily = sub.choices['daily']
    daily.add_argument('--refresh-mocs', action='store_true')
    daily.add_argument('--date', help='ISO date (default: today)')
    recover = sub.add_parser('recover', help='Resume or roll back a frozen transaction')
    recover.add_argument('id', nargs='?', help='Job id')
    recover.add_argument('--job', help='Job id (older spelling)')
    mode = recover.add_mutually_exclusive_group()
    mode.add_argument('--resume', action='store_const', const='resume', dest='action')
    mode.add_argument('--rollback', action='store_const', const='rollback', dest='action')
    mode.add_argument('--action', choices=['resume', 'rollback'], dest='action')
    inbox = sub.add_parser('inbox', help='Inbox items and their status (S0)')
    inbox_sub = inbox.add_subparsers(dest='inbox_command')
    inbox_sub.add_parser('list')
    ishow = inbox_sub.add_parser('show'); ishow.add_argument('item'); ishow.add_argument('--head', type=int, default=40)
    iskip = inbox_sub.add_parser('skip'); iskip.add_argument('item'); iskip.add_argument('--reason', required=True)
    ingest = sub.add_parser('ingest', help='Turn sources into reviewed cards (S1-S9)')
    ingest.add_argument('paths', nargs='*', type=Path)
    ingest.add_argument('--all', action='store_true', help='Every new or unfinished inbox item')
    ingest.add_argument('--retry', metavar='JOB', help='Redo only the pending items of a partial job (child job)')
    ingest.add_argument('--executor', choices=['api', 'host'], help='Override [executor].default')
    ingest.add_argument('--prepare-only', action='store_true', help='Freeze input and chunks; no model call')
    ingest.add_argument('--proposal-only', action='store_true', help='Produce and review, but do not write')
    ingest.add_argument('--max-rounds', type=int, help='Override [review] max_repair_rounds for this run')
    review = sub.add_parser('review', help='Review registry: status, daily queue, agent reviews')
    review_sub = review.add_subparsers(dest='review_command', required=True)
    rstatus = review_sub.add_parser('status', help='Study states and due counts, per domain')
    rqueue = review_sub.add_parser('queue', help='Due cards first, then new cards in prerequisite order')
    rqueue.add_argument('--new', type=int, dest='new_per_day', help='New cards per day (default 10)')
    rqueue.add_argument('--domain', action='append', default=[], help='Only new cards of these domains')
    rqueue.add_argument('--limit', type=int)
    rqueue.add_argument('--questions', action='store_true', help='Include each card\'s review questions')
    rqueue.add_argument('--section', help='Review section heading (default from [cards] language)')
    rlog = review_sub.add_parser('log', help='Append one agent review of a card')
    rlog.add_argument('--card', required=True)
    rlog.add_argument('--rating', type=int, choices=[1, 2, 3, 4], help='1 again, 2 hard, 3 good, 4 easy')
    rlog.add_argument('--item', action='append', default=[],
                      help='Per question: "<question text or h:id>=<1-4>[:hit|partial|miss]" (repeatable)')
    rlog.add_argument('--grader', help='Who graded the answers (model or person)')
    rimport = review_sub.add_parser('import-sr', help='One-time import of Spaced Repetition plugin schedules')
    rimport.add_argument('--dry-run', action='store_true')
    for q in (rstatus, rqueue, rlog, rimport):
        q.add_argument('--now', help='ISO 8601 time with offset (default: now)')
    for q in (rlog, rimport):
        q.add_argument('--device', help='Writer id for the log file name (default: cli-<host hash>)')
        q.add_argument('--format', choices=['jsonl', 'md'], default='jsonl', dest='log_format')
    diagrams = sub.add_parser('diagrams', help='Diagram batch tools (python -m secondbrain.diagrams)',
                              add_help=False)
    diagrams.add_argument('args', nargs=argparse.REMAINDER)
    job = sub.add_parser('job', help='Jobs, runs and host-mode hand-off')
    job_sub = job.add_subparsers(dest='job_command', required=True)
    job_sub.add_parser('list')
    show = job_sub.add_parser('show'); show.add_argument('id')
    jpacket = job_sub.add_parser('packet', help='Create the next producer/reviewer packet for a sub-agent')
    jpacket.add_argument('id'); jpacket.add_argument('--role', choices=['producer', 'reviewer'], required=True)
    jpacket.add_argument('--chunk')
    jsubmit = job_sub.add_parser('submit', help='Submit a sub-agent answer for one run')
    jsubmit.add_argument('id'); jsubmit.add_argument('--role', choices=['producer', 'reviewer'], required=True)
    jsubmit.add_argument('--run-id', required=True); jsubmit.add_argument('--file', type=Path, required=True)
    jsubmit.add_argument('--model', help='Model that produced the answer (recorded as self-reported)')
    jadvance = job_sub.add_parser('advance', help='Run every step that can run now')
    jadvance.add_argument('id'); jadvance.add_argument('--executor', choices=['api', 'host'])
    jadvance.add_argument('--proposal-only', action='store_true')
    jreset = job_sub.add_parser('reset', help='Set aside a job that left nothing in the vault (never written or '
                                              'rolled back) so its source can be prepared again')
    jreset.add_argument('id'); jreset.add_argument('--reason', required=True)
    jabandon = job_sub.add_parser('abandon', help='Give up a run that will not be submitted')
    jabandon.add_argument('id'); jabandon.add_argument('--run-id', required=True)
    jabandon.add_argument('--reason', required=True)
    doctor = sub.add_parser('doctor', help='Check configuration, parser, index and endpoints')
    doctor.add_argument('--live', action='store_true', help='Send one tiny request to each endpoint')
    serve = sub.add_parser('serve', help='MCP stdio server (read-only unless --writer)')
    serve.add_argument('--writer', action='store_true')
    return p


def make_executors(args, root: Path):
    """Return ``(producer, reviewer)`` executors for this invocation."""
    from .executors.factory import build
    return build(config.current(), getattr(args, 'executor', None), vault=str(root))


def _ingest(args, root: Path) -> list:
    from . import inbox
    from .pipelines import ingest
    paths = list(args.paths)
    if args.max_rounds is not None:
        if args.max_rounds < 0:
            raise ValueError('--max-rounds must be 0 or more')
        config.set_review_policy(replace(config.review_policy(), max_repair_rounds=args.max_rounds))
    if args.retry:
        if paths or args.all:
            raise ValueError('--retry takes a job id instead of paths or --all')
        child = ingest.retry(root, args.retry)
        if args.prepare_only or child['status'] in {'applied', 'partial', 'failed'}:
            return [child]
        producer, reviewer = make_executors(args, root)
        return [ingest.advance(root, child['job_id'], producer=producer, reviewer=reviewer,
                               apply=not args.proposal_only)]
    if args.all:
        lay_items = [i for i in inbox.scan(root) if i['status'] in {'new', 'in_progress'}]
        paths += [root / i['path'] for i in lay_items]
    if not paths:
        raise ValueError('Give source paths or --all')
    results = []
    executors = None
    for path in paths:
        try:
            prepared = ingest.prepare(root, path)
        except ingest.IngestError as exc:
            results.append({'path': str(path), 'status': 'failed', 'error': str(exc)})
            continue
        finished = prepared.get('status') in {'unchanged', 'skipped', 'applied', 'partial', 'failed', 'published'}
        if args.prepare_only or finished or 'schema' not in prepared:
            results.append(prepared)
            continue
        executors = executors or make_executors(args, root)
        results.append(ingest.advance(root, prepared['job_id'], producer=executors[0],
                                      reviewer=executors[1], apply=not args.proposal_only))
    return results


def _job(args, root: Path):
    service = Service(root)
    if args.job_command == 'list':
        return jobs.list_jobs(root)
    if args.job_command == 'show':
        return service.job_show(args.id)
    if args.job_command == 'submit':
        payload = json.loads(args.file.read_text(encoding='utf-8'))
        return service.submit(args.id, payload, role=args.role, run_id=args.run_id, model=args.model)
    if args.job_command == 'abandon':
        return jobs.abandon_run(root, args.id, args.run_id, reason=args.reason)
    if args.job_command == 'reset':
        return jobs.archive(root, args.id, reason=args.reason)
    if args.job_command == 'packet':
        return service.job_packet(args.id, args.role, args.chunk)
    producer, reviewer = make_executors(args, root)
    return service.job_advance(args.id, producer=producer, reviewer=reviewer, apply=not args.proposal_only)


def _review(args, root: Path):
    from . import reviews
    now = reviews.now_local(args.now)
    command = args.review_command
    if command == 'status':
        return reviews.status(root, now)
    if command == 'queue':
        return reviews.queue(root, now, new_per_day=args.new_per_day, domains=args.domain, limit=args.limit,
                             questions=args.questions, section=args.section)
    if command == 'log':
        items = [reviews.parse_item(text) for text in args.item]
        return reviews.log(root, args.card, now, rating=args.rating, items=items, grader=args.grader,
                           device=args.device, fmt=args.log_format)
    return reviews.import_sr(root, now, device=args.device, fmt=args.log_format, dry_run=args.dry_run)


def _inbox(args, root: Path):
    from . import inbox
    command = args.inbox_command or 'list'
    if command == 'list':
        return inbox.scan(root)
    if command == 'skip':
        return inbox.skip(root, args.item, args.reason)
    item = inbox.find(root, args.item)
    text = (root / item['path']).read_text(encoding='utf-8', errors='replace')
    return {**item, 'head': '\n'.join(text.splitlines()[:max(1, args.head)])}


def _doctor(args, root: Path) -> dict:
    import dataclasses

    import yaml as _yaml
    from . import index
    from . import layout as layouts
    from .store import state_root
    settings = config.current()
    report = {'config_file': str(settings.source) if settings.source else None, 'vault': str(root),
              'vault_exists': root.is_dir(), 'layout': dataclasses.asdict(layouts.of(root)),
              'libyaml': bool(getattr(_yaml, '__with_libyaml__', False)),
              'fts5': index.fts5_available(), 'executor_default': settings.executor_default}
    try:
        report['state_root'] = str(state_root(root))
    except ValueError as exc:
        report['state_root_error'] = str(exc)
    report['catalog'] = _catalog_check(root)
    for role in ('producer', 'reviewer'):
        endpoint = getattr(settings, role)
        if endpoint is None:
            report[role] = None
            continue
        entry = {'kind': endpoint.kind, 'base_url': endpoint.base_url, 'model': endpoint.model}
        try:
            endpoint.api_key()
            entry['key'] = 'available'
        except (ValueError, OSError) as exc:
            entry['key'] = f'unavailable: {exc}'
        report[role] = entry
    if settings.producer and settings.reviewer:
        from .executors.factory import check_families
        try:
            report['families'] = check_families(settings)
        except ValueError as exc:
            report['families_error'] = str(exc)
    if args.live:
        report['live'] = live_check(settings)
    return report


def _catalog_check(root: Path) -> str:
    """``valid``, ``missing``, or why the catalog file (for example after a hand edit) is not valid."""
    from . import layout as layouts
    from .catalog import validate_catalog
    path = root / layouts.of(root).catalog
    if not path.is_file():
        return 'missing (sb init writes it)'
    try:
        validate_catalog(json.loads(path.read_text(encoding='utf-8')))
    except (ValueError, UnicodeDecodeError) as exc:
        return f'invalid: {exc}'
    return 'valid'


def live_check(settings, transport=None) -> dict:
    """One minimal request per endpoint; reports the model each provider names."""
    from .executors.api import ApiExecutor
    from .executors.base import Task
    out = {}
    schema = {'type': 'object', 'required': ['ok'], 'properties': {'ok': {'type': 'boolean'}}}
    for role in ('producer', 'reviewer'):
        endpoint = getattr(settings, role)
        if endpoint is None:
            out[role] = 'not configured'
            continue
        result = ApiExecutor(endpoint, transport=transport).run(Task(
            job_id='doctor', run_id='doctor', role=role, step='produce',
            prompt='Health check. Return {"ok": true}.', packet={}, packet_sha256='', output_schema=schema))
        out[role] = {'status': result.status, 'model_reported': result.model_reported,
                     'output': result.output, 'error': result.error}
    return out


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


def _activate(args) -> Path:
    """Load the config file and make it apply to the vault this command uses."""
    settings = config.load(args.config) if args.config else config.load()
    vault = args.vault.expanduser().absolute() if args.vault else settings.vault
    if vault is None:
        raise ValueError('No vault: pass --vault, set SECONDBRAIN_VAULT, or set `vault` in the config file')
    if not settings.card_language_set:
        # [cards] language not in the config file: generated text follows the vault.
        settings = replace(settings, card_language=labels.vault_language(vault) or settings.card_language)
    config.activate(replace(settings, vault=vault))
    return vault


def main(argv=None):
    p = parser()
    args, extra = p.parse_known_args(argv)
    if args.command == 'diagrams':
        from .diagrams.__main__ import main as diagrams_main
        return diagrams_main(args.args + extra)
    if extra:
        p.error(f'unrecognized arguments: {" ".join(extra)}')
    try:
        root = _activate(args)
    except (ValueError, OSError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
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
        elif cmd == 'language':
            from .service import set_language
            result = set_language(root, args.set_language) if args.set_language else labels.describe(root)
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
            job_id = args.id or args.job
            if not job_id:
                raise ValueError('recover needs a job id')
            if args.action == 'rollback':
                from .pipelines.ingest import check_rollback
                check_rollback(root, job_id)
                result = recover(root, job_id, 'rollback')
                if _has_state(root, job_id):
                    jobs._internal_update(root, job_id, status='rolled_back')
            else:
                result = (jobs.resume_transaction(root, job_id) if _has_state(root, job_id) else None) \
                    or recover(root, job_id, 'resume')
        elif cmd == 'job':
            result = _job(args, root)
        elif cmd == 'inbox':
            result = _inbox(args, root)
        elif cmd == 'review':
            result = _review(args, root)
        elif cmd == 'ingest':
            result = _ingest(args, root)
        elif cmd == 'doctor':
            result = _doctor(args, root)
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

"""Ingest chain S1–S9: one source file in, reviewed atomic cards out.

S1 read and freeze the source text  → S2 chunk  → S3 produce per chunk
(cards, classification, new-topic proposals, relations, coverage)  → S4
deterministic validation  → S5 near-duplicate candidates  → S6 independent
review  → S7 gate (+ bounded repair rounds)  → S8 one transaction that
writes cards, inverse edges, accepted topics, the source copy, registries,
MOCs and the entry page, and optionally archives the inbox original  → S9
optional git commit.

The job id is content derived (``ingest-<sha256[:16]>``): the same file
always lands in the same job and resumes after its last finished step. Each
chunk keeps one candidate/review slot per round (``c01``, ``c01.r1`` ...);
accepted cards keep the review that accepted them, so a repair round only
re-produces and re-reviews what was rejected. Cards still rejected when the
rounds run out stay *pending* and the rest of the source is written
(``partial``).
"""
from __future__ import annotations

import base64
import copy
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import yaml

from dataclasses import asdict, dataclass
from typing import Callable

from .. import cards as cardfmt
from .. import budget, config, decisions, inbox, jobs, labels, overlap, review_notes, schemas
from .. import layout as layouts
from ..catalog import category_pairs, open_relation_types, relation_meanings, taxonomy_packet, validate_catalog
from ..classification import projection_changes, snapshot_checks, taxonomy_fingerprint, writable_snapshot
from ..evidence import check_verbatim
from ..executors.base import CHUNK_TOO_LARGE, INVALID_OUTPUT, ExecutorFailure
from ..locks import job_lock
from ..store import Change, Conflict, Halt, digest, read_bytes
from ..textnorm import plain, same_text
from ..vault import Card, append_list_items, parse_note, read_snapshot
from .. import retrieval
from . import amend as amends
from . import answerfix, cardlint, card_diagrams, chunking, notecheck, notes, source_check, split
from .chunking import chunk_text
from .routing import route

KIND = 'ingest'
METHOD = 'ingest/4'
TEXT_SUFFIXES = {'.md', '.markdown', '.txt', '.text', '.rst', '.org', ''}
_SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,59}$')
DISPOSITIONS = set(overlap.COVERAGE)


class IngestError(ValueError):
    """Input or validation problem in the ingest chain."""


class WriteStopped(IngestError, Halt):
    """The check before writing finds that the write would not hold what an answer gives a card: nothing is
    written, and the job stops for the owner (a stop on purpose, :class:`~secondbrain.store.Halt`)."""


class IngestAnswerError(IngestError, schemas.AnswerError):
    """An ingest answer the check refuses (any other ``IngestError`` while an answer is taken is the program's)."""


# --------------------------------------------------------------------------
# S1 read source, S2 chunk, prepare (freeze input)
# --------------------------------------------------------------------------
def _safe_filename(name: str) -> str:
    name = (name or '').strip()
    if not name or name in {'.', '..'} or '/' in name or '\\' in name or name.startswith('.') \
            or '\x00' in name:
        return 'source.txt'
    return name


def read_source(path: Path, *, max_bytes: int, converter: str) -> tuple[bytes, str]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise IngestError(f'Source must be a regular file (no symlink): {path}')
    size = path.stat().st_size
    if size > max_bytes:
        raise IngestError(f'Source is larger than {max_bytes} bytes; split it instead of truncating it')
    raw = path.read_bytes()
    try:
        return raw, raw.decode('utf-8')
    except UnicodeDecodeError:
        pass
    if converter == 'markitdown':
        try:
            from markitdown import MarkItDown
        except ImportError as exc:
            raise IngestError('converter = "markitdown" needs `pip install markitdown`') from exc
        text = MarkItDown().convert(str(path)).text_content
        if not text.strip():
            raise IngestError('The converter produced no text')
        return raw, text
    raise IngestError('Only UTF-8 text or Markdown is supported without a converter '
                      '(set [ingest] converter = "markitdown" for PDF/DOCX/HTML)')


def _topic_counts(snapshot) -> dict:
    counts: dict = {}
    for c in snapshot.cards.values():
        p, t = c.fm.get('pillar'), c.fm.get('topic')
        if isinstance(p, str) and isinstance(t, str):
            counts.setdefault(c.domain, {}).setdefault(f'{p}\x1f{t}', 0)
            counts[c.domain][f'{p}\x1f{t}'] += 1
    return counts


def _placement(snapshot, place: dict) -> dict:
    """``place`` ({domain, pillar, topic}) checked against the catalog and taxonomy; only the given keys are kept."""
    place = {k: v for k, v in place.items() if v}
    domain, pillar, topic = place.get('domain'), place.get('pillar'), place.get('topic')
    if not domain or (topic and not pillar):
        raise IngestError('A placement names a domain, then optionally a pillar, then optionally a topic')
    if domain not in snapshot.catalog.get('domains', {}):
        raise IngestError(f'Placement domain {domain!r} is not a catalog domain: '
                          + ', '.join(sorted(snapshot.catalog.get('domains', {}))))
    pillars = {p.get('name'): p for p in _current_taxonomy(snapshot, domain).get('pillars') or [] if isinstance(p, dict)}
    if pillar and pillar not in pillars:
        raise IngestError(f'Placement pillar {pillar!r} is not a pillar of {domain}: ' + ', '.join(sorted(pillars)))
    if topic and topic not in (pillars[pillar].get('topics') or []):
        raise IngestError(f'Placement topic {topic!r} is not a topic of {domain} / {pillar}; add it to the pillar\'s '
                          '`topics` in the taxonomy file first: ' + ', '.join(pillars[pillar].get('topics') or []))
    return place


def _with_placement(domains: list, packet: dict) -> list:
    """A chunk's candidate domains, with the owner's placement domain first."""
    place = (packet.get('placement') or {}).get('domain')
    return [place] + [d for d in domains if d != place] if place else domains


def prepare(root: Path, source: Path, *, settings: config.Settings | None = None, placement: dict | None = None) -> dict:
    """Freeze one source into its ingest job (or report it as already done).

    ``placement`` ({domain, pillar, topic}; pillar and topic optional) is where
    the owner wants the source's cards: producers file them there unless a
    card's subject has its own topic elsewhere, and reviewers check that. It is
    frozen with the job, so a retry keeps it.
    """
    settings = settings or config.current()
    if settings.taxonomy_extra_reviewers:
        raise IngestError('[taxonomy] extra_reviewers > 0 is not supported yet; set it to 0')
    root = Path(root)
    lay = layouts.of(root)
    raw, text = read_source(source, max_bytes=settings.ingest.max_source_bytes, converter=settings.ingest.converter)
    sha = digest(raw)
    job_id = inbox.job_id_for(sha)
    done = inbox.done_index(root).get(sha)
    if done and done['status'] in {'applied', 'done', 'partial'}:
        out = {'status': 'unchanged', 'result': done['status'], 'job_id': done.get('job_id') or job_id,
               'source_sha256': sha, 'via': done['via']}
        if done['status'] == 'partial' and done.get('stalled'):
            out.update(pending=done.get('pending'), message=inbox._partial_note(out['job_id'], True))
        elif done['status'] == 'partial':
            out.update(pending=done.get('pending'), hint=f'sb ingest --retry {out["job_id"]}')
        return out
    if done and done['status'] == 'skipped':
        return {'status': 'skipped', 'source_sha256': sha, 'reason': done.get('reason')}
    try:
        state = jobs.load(root, job_id)
    except ValueError:
        state = None
    if state is not None and state['status'] == 'rolled_back':
        # the vault holds nothing of the old job: keep its records aside and start again
        jobs.archive(root, job_id, reason='rolled back; the source was ingested again')
    elif state is not None:
        frozen = jobs.read_input(root, job_id).get('placement')
        note = {'placement_note': f'{job_id} was frozen before with placement {frozen}; it keeps that placement, '
                                  'not the one given now'} \
            if placement and {k: v for k, v in placement.items() if v} != (frozen or {}) else {}
        return {**state, 'resumed': True, **note}
    source_path = Path(source).absolute()
    in_inbox = source_path.parent.resolve() == (root / lay.inbox).resolve()
    inbox_path = f'{lay.inbox}/{source_path.name}' if in_inbox else None
    snapshot = read_snapshot(root)
    writable_snapshot(snapshot)
    if not snapshot.catalog:
        raise IngestError('The vault has no catalog; run `sb init` first')
    validate_catalog(snapshot.catalog)
    _require_some_taxonomy(snapshot, lay)
    place = {'placement': _placement(snapshot, placement)} if placement and any(placement.values()) else {}
    chunks = []
    for chunk in chunk_text(text, settings.ingest.chunk_chars):
        routed = route(root, snapshot, text[chunk['start']:chunk['end']], domains=settings.ingest.candidate_domains,
                       k=settings.ingest.candidates_k)
        chunks.append({**chunk, 'candidate_domains': _with_placement(routed['domains'], place),
                       **_producer_candidates(snapshot, routed, settings.ingest.candidates_chars,
                                              settings.ingest.candidates_shelf),
                       'routing': {'votes': routed['votes'], 'filled': routed['filled'],
                                   'passages': routed['passages']}})
    domains = sorted(snapshot.catalog['domains'])
    packet = {
        'kind': KIND, 'method_version': METHOD,
        'source': {'filename': _safe_filename(source_path.name), 'inbox_path': inbox_path,
                   'sha256': sha, 'size': len(raw), 'text': text,
                   'raw_b64': base64.b64encode(raw).decode('ascii')},
        'chunks': chunks,
        'catalog': snapshot.catalog,
        'taxonomy': taxonomy_packet(snapshot, domains),
        'taxonomy_sha256': taxonomy_fingerprint(snapshot),
        'topic_counts': _topic_counts(snapshot),
        'settings': {'chunk_chars': settings.ingest.chunk_chars,
                     'new_topics_per_source': settings.ingest.new_topics_per_source,
                     'id_prefix': settings.ingest.id_prefix,
                     'language': labels.effective_language(snapshot, settings),
                     'diagrams': settings.card_diagrams,
                     'after_ingest': settings.ingest.after_ingest,
                     **_later_settings(settings.ingest),
                     # the whole review policy (with a --max-rounds given now) and the folder layout: every
                     # later step reads these, never the configuration (see secondbrain.decisions)
                     'review': asdict(config.review_policy()), 'layout': _layout_record(lay)},
        'snapshot_fingerprint': snapshot.fingerprint,
        **place,
    }
    state = jobs.create(root, job_id, KIND, packet)
    return {**state, 'source_sha256': sha, 'chunks': [c['chunk_id'] for c in chunks],
            'inbox_path': inbox_path}


def _later_settings(ingest: config.IngestConfig) -> dict:
    """The ingest settings added after ``ingest/3`` that a job freezes (candidate cards, review packet, merging,
    the context note)."""
    return {key: getattr(ingest, key) for key in _FROZEN_DEFAULTS}


def _layout_record(lay) -> dict:
    """The vault layout as the job freezes it (``[layout]`` keys)."""
    return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(lay).items()}


def _frozen_run(root: Path, packet: dict):
    """``with _frozen_run(root, packet):`` a job step reads the settings the job froze (``config.active``)."""
    return config.job_settings(jobs.frozen_settings(root, packet))


#: Candidate settings of a job frozen before they existed: the values that job ran with (no named cards).
#: What a job frozen before a setting existed did instead (``_frozen``).
_FROZEN_DEFAULTS = {'candidates_k': 'auto', 'candidates_chars': 40000, 'candidates_hop': 0,
                    'candidates_shelf': False, 'review_named': False, 'review_words_view': 'full',
                    'merge_into_existing': False, 'context_note': 'off', 'context_note_chars': 2000}


def _frozen(packet: dict, key: str):
    """A frozen setting of the job, or what a job frozen before the setting existed used."""
    settings = packet.get('settings') or {}
    return settings[key] if key in settings else _FROZEN_DEFAULTS[key]


#: The view each route's candidates get in a producer packet: enough to link and to see what exists; a shelf
#: by title (design section 2.3).
PRODUCER_LEVELS = {'words': 'brief', 'shelf': 'title'}
#: The key of a producer packet's ``candidates`` that lists one shelf: ``shelf <domain>/<pillar>/<topic>``.
SHELF_KEY = 'shelf '


def _producer_shelves(snapshot, routed: dict) -> dict:
    """The shelves of the topics the chunk's first-route cards are filed under, ``{key: [{id, rank, via}]}``.

    A topic comes in the order of its best-ranked hit; each lists at most :data:`retrieval.SHELF_MAX` cards,
    those the search ranked first leading.
    """
    order: dict = {}
    for rows in routed['candidates'].values():
        for e in rows:
            order[e['id']] = min(order.get(e['id'], e['rank']), e['rank'])
    topics: dict = {}
    for cid in sorted(order, key=lambda c: (order[c], c)):
        card = snapshot.cards.get(cid)
        if card is not None and card.fm.get('pillar') and card.fm.get('topic'):
            topics.setdefault((card.domain, card.fm['pillar'], card.fm['topic']), None)
    return {f'{SHELF_KEY}{d}/{p}/{t}': [{'id': cid, 'rank': n, 'via': ['shelf']}
                                        for n, cid in enumerate(retrieval.shelf(snapshot, d, p, t, rank=order), 1)]
            for d, p, t in topics}


def _producer_candidates(snapshot, routed: dict, budget: int, shelf: bool = False) -> dict:
    """``{candidates, candidate_cards}`` of a chunk: its routed cards, and with ``shelf`` the shelves of the topics
    they are filed under (packed last), at the views that fit ``budget``."""
    lists = {**routed['candidates'], **(_producer_shelves(snapshot, routed) if shelf else {})}
    table, _ = retrieval.fit(snapshot, retrieval.merge(lists.values()), budget, PRODUCER_LEVELS)
    return {'candidates': {key: rows for key, rows in ((k, retrieval.keep(r, table)) for k, r in lists.items())
                           if rows or not key.startswith(SHELF_KEY)},
            'candidate_cards': table}


def _require_some_taxonomy(snapshot, lay) -> None:
    """Refuse an ingest no card could come out of: no domain has a pillar with a topic yet.

    A card must be filed under a domain, pillar and topic, so the producer's
    model call would be wasted (a vault right after `sb init`).
    """
    domains = snapshot.catalog.get('domains', {})
    if any(category_pairs(snapshot, d) for d in domains):
        return
    files = sorted({lay.taxonomy_path(meta['moc']) for meta in domains.values()})
    shown = ', '.join(files[:5]) + (f' (and {len(files) - 5} more)' if len(files) > 5 else '')
    raise IngestError(f'No domain has a taxonomy with a topic yet, so no card could be filed; nothing was sent to '
                      f'a model. Write the taxonomy files first: {shown} (docs/new-vault.md, step 3), then run '
                      '`sb status`.')


def retry(root: Path, job_id: str, *, settings: config.Settings | None = None) -> dict:
    """Start a child job that redoes only the pending items of a ``partial`` ingest job.

    The child (``<first job>-retry<N>``) reads the same frozen source and
    chunks, but a fresh catalog, taxonomy and routing from the vault as it is
    now; its packets carry the open items with the reviewer's reasons, the
    cards of each chunk that are already written, and the passages earlier
    jobs of the source settled without a card (duplicates of existing cards,
    material not worth keeping), so the child neither writes those again nor
    reads them as missing. It has its own transaction, so it can be rolled
    back on its own. Calling it again on the same parent returns the existing
    child with a ``message`` naming the job to look at next. A child that was
    rolled back, reset with `sb job reset`, or made for an earlier run of the
    parent (the parent was rolled back and the source ingested again) does
    not block a new child: it is set aside like `sb job reset` does, and the
    new child takes its id.
    """
    settings = settings or config.current()
    root = Path(root)
    parent = jobs.load(root, job_id)
    if parent['kind'] != KIND or parent['status'] != 'partial':
        raise IngestError(f'Only a partial ingest job can be retried; {job_id} is {parent["status"]}')
    if parent.get('retried_by'):
        live = _live_child(root, job_id, parent, parent['retried_by'])
        if live is not None:
            return _resumed_child(root, job_id, live)
    old = jobs.read_input(root, job_id)
    lineage = old.get('retry') or {}
    first, number = lineage.get('root', job_id), lineage.get('round', 0) + 1
    open_items: dict = {}
    for item in list(parent.get('pending') or []) + list(parent.get('needs_taxonomy') or []):
        open_items.setdefault(item['chunk'], []).append(item)
    if not open_items:
        raise IngestError(f'{job_id} has no pending items to retry')
    written = {cid: list(cards) for cid, cards in (lineage.get('written') or {}).items()}
    for card in parent.get('produced') or []:
        written.setdefault(card['chunk'], []).append({'id': card['id'], 'title': card['title'], 'slug': card.get('slug')})
    settled = {cid: {key: list(rows) for key, rows in kinds.items()}
               for cid, kinds in (lineage.get('settled') or {}).items() if cid in open_items}
    # links to cards that were still pending; restored when a later job writes the target. The parent lists
    # every link it could not settle, its own and those carried to it, so its result is the whole list
    written_now = {card.get('slug') for card in parent.get('produced') or []}
    skipped = []
    for row in [r for r in parent.get('skipped_relations') or [] if r.get('reason') == 'target card was not written']:
        entry = {k: row.get(k) for k in ('from', 'type', 'target_slug')}
        if entry['target_slug'] not in written_now and entry not in skipped:
            skipped.append(entry)
    for cid in open_items:
        for key, rows in _settled_rows(root, parent, cid).items():
            bucket = settled.setdefault(cid, {'duplicates': [], 'out_of_scope': []}).setdefault(key, [])
            bucket += [row for row in rows if row not in bucket]
    snapshot = read_snapshot(root)
    writable_snapshot(snapshot)
    validate_catalog(snapshot.catalog)
    chunks = []
    for cid in [c['chunk_id'] for c in effective_chunks(old, parent) if c['chunk_id'] in open_items]:
        chunk = {k: v for k, v in _chunk(old, cid).items()
                 if k not in {'candidate_domains', 'neighbors', 'candidates', 'candidate_cards', 'routing'}}
        routed = route(root, snapshot, old['source']['text'][chunk['start']:chunk['end']],
                       domains=settings.ingest.candidate_domains, k=_frozen(old, 'candidates_k'))
        # the domains the parent's reviewers pointed to, and where the open cards last were
        named = [d for d in _reviewed_domains(root, parent, old, cid, open_items[cid])
                 if d in snapshot.catalog['domains'] and d not in routed['domains']]
        chunks.append({**chunk, 'candidate_domains': _with_placement(routed['domains'] + named, old),
                       **_producer_candidates(snapshot, routed, _frozen(old, 'candidates_chars'),
                                              _frozen(old, 'candidates_shelf')),
                       'routing': {'votes': routed['votes'], 'filled': routed['filled'],
                                   'passages': routed['passages'], 'named_by_review': named}})
    for card_list in written.values():
        for card in card_list:
            if card['id'] in snapshot.cards:
                card['essence'] = cardfmt.view(snapshot.cards[card['id']], 'brief')['essence']
                quote = _source_locator(snapshot.cards[card['id']], old['source']['sha256'])
                if quote:
                    card['source_quote'] = quote
    domains = sorted(snapshot.catalog['domains'])
    frozen = {**old['settings'], 'review': asdict(config.review_policy())}
    frozen.setdefault('layout', _layout_record(layouts.of(root)))
    # the child is a job of this version, whatever version froze the parent: it reads the parent's records
    # (an earlier scale's levels read as this one's, _settled_rows) and judges everything again itself
    packet = {**old, 'method_version': METHOD, 'settings': frozen, 'chunks': chunks, 'catalog': snapshot.catalog,
              'taxonomy': taxonomy_packet(snapshot, domains), 'taxonomy_sha256': taxonomy_fingerprint(snapshot),
              'topic_counts': _topic_counts(snapshot), 'snapshot_fingerprint': snapshot.fingerprint,
              'retry': {'of': job_id, 'root': first, 'round': number,
                        'pending': {cid: open_items[cid] for cid in open_items},
                        'written': written, 'settled': settled, 'skipped_relations': skipped}}
    child = f'{first}-retry{number}'
    live = _live_child(root, job_id, parent, child)
    if live is not None:  # made before an interruption, before the parent recorded it
        jobs._internal_update(root, job_id, retried_by=child)
        return _resumed_child(root, job_id, live)
    state = jobs.create(root, child, KIND, packet)
    jobs._internal_update(root, job_id, retried_by=child)
    return {**state, 'retry_of': job_id, 'chunks': [c['chunk_id'] for c in chunks]}


def _live_child(root: Path, parent_id: str, parent: dict, child_id: str) -> dict | None:
    """The retry child at ``child_id`` if it still counts for this run of the parent.

    None when there is no such job (never made, or reset). A child that was
    rolled back, or that was made for an earlier run of the parent, is
    archived (renamed to ``<id>.reset-<UTC>``) and None is returned; one
    that wrote and was not rolled back makes the archive refuse.
    """
    try:
        state = jobs.load(root, child_id)
    except ValueError:
        return None
    if state['status'] == 'rolled_back':
        jobs.archive(root, child_id, reason=f'rolled back; the pending items of {parent_id} are retried again')
        return None
    of = (jobs.read_input(root, child_id).get('retry') or {}).get('of')
    if of == parent_id and state.get('created_at', '') >= parent.get('created_at', ''):
        return state
    jobs.archive(root, child_id, reason=f'made for an earlier run of {parent_id}, which was rolled back')
    return None


def _reviewed_domains(root: Path, state: dict, packet: dict, base: str, items: list) -> list:
    """Domains the reviews of chunk ``base`` named, then the last domain of each of its open cards."""
    open_slugs = {i.get('slug') for i in items if i.get('slug')}
    named, last = [], {}
    for slot, info in _slots(state, base):
        if info.get('review'):
            named += _domains_named(packet, _findings(_review(root, state, slot)))
        if info.get('candidate'):
            for card in jobs.artifact(root, state, 'candidate', slot).get('cards', []):
                if isinstance(card, dict) and card.get('slug') in open_slugs:
                    last[card['slug']] = card.get('domain')
    return [d for d in dict.fromkeys(named + list(last.values())) if isinstance(d, str)]


def _settled_rows(root: Path, state: dict, base: str, upto_round: int | None = None) -> dict:
    """Passages of chunk ``base`` that this job settled without writing a card (rounds before ``upto_round``).

    ``duplicates``: the producers' ``same`` coverage rows, and the cards a
    reviewer accepted as ``same`` as existing cards (the last verdict on a slug
    counts, its reason as ``review``), each with the existing ids that hold the
    knowledge (``with``). ``out_of_scope``: rows the producers marked as not
    worth keeping. A later round, and a retry child, are shown these so they do
    not write those cards again or read the passages as missing. The
    producers' own reasons are left out, as a reviewer must not see them. Jobs
    of an earlier version (``duplicate`` rows and decisions) read the same.
    """
    dups, skipped, verdicts = [], [], {}
    for slot, info in _slots(state, base)[:upto_round]:
        if not info.get('candidate'):
            continue
        cand = jobs.artifact(root, state, 'candidate', slot)
        for row in cand.get('coverage') or []:
            if not isinstance(row, dict):
                continue
            if row.get('disposition') in ('same', 'duplicate'):
                dups.append({'source_quote': row.get('source_quote'),
                             'with': list(row.get('with') or row.get('duplicate_of') or [])})
            elif row.get('disposition') == 'out_of_scope':
                skipped.append({'source_quote': row.get('source_quote')})
        if not info.get('review'):
            continue
        reviewed = {r.get('slug'): r for r in _review(root, state, slot).get('cards', [])
                    if isinstance(r, dict)}
        for card in cand.get('cards') or []:
            if not isinstance(card, dict) or card.get('slug') not in reviewed:
                continue
            held = _held_by(reviewed[card['slug']])
            verdicts[card['slug']] = None if held is None else {
                'source_quote': (card.get('source_quotes') or [None])[0], 'title': card.get('title'),
                'with': held, 'review': reviewed[card['slug']].get('reason')}
    out = {'duplicates': [], 'out_of_scope': []}
    for row in dups + [v for v in verdicts.values() if v]:
        if row not in out['duplicates']:
            out['duplicates'].append(row)
    for row in skipped:
        if row not in out['out_of_scope']:
            out['out_of_scope'].append(row)
    return out


def _held_slugs(root: Path, state: dict, base: str, upto_round: int) -> set:
    """Slugs of chunk ``base`` whose latest verdict before ``upto_round`` accepted them as the same as existing cards."""
    latest = {}
    for slot, info in _slots(state, base)[:upto_round]:
        if info.get('review'):
            for row in _review(root, state, slot).get('cards', []):
                if isinstance(row, dict):
                    latest[row.get('slug')] = _held_by(row) is not None
    return {slug for slug, held in latest.items() if held}


def _held_by(verdict: dict) -> list | None:
    """The existing cards that hold an accepted ``same`` card's knowledge (old reviews: ``duplicate``), else None."""
    if verdict.get('decision') == 'duplicate':
        return [verdict.get('duplicate_of')]
    if verdict.get('decision') == 'accept' and verdict.get('overlap') == 'same':
        return list(verdict.get('with') or [])
    return None


def _source_locator(card, sha256: str) -> str | None:
    """The passage of source ``sha256`` that ``card`` was made from (its first source quote)."""
    for src in card.fm.get('sources') or []:
        if isinstance(src, dict) and src.get('sha256') == sha256 and isinstance(src.get('locator'), str):
            return src['locator']
    return None


def check_rollback(root: Path, job_id: str) -> None:
    """Refuse to roll back a job whose retry child still has its writes in the vault.

    The child appended to the same registries after the parent, so the
    parent's rollback would meet the child's bytes; roll back the child first.
    """
    try:
        child = jobs.load(root, job_id).get('retried_by')
    except ValueError:
        return
    if not child:
        return
    raw = jobs.read_state(root, jobs.location(child, 'transaction.json'))
    journal = json.loads(raw) if raw is not None else None
    if journal is not None and journal.get('status') != 'rolled_back':
        raise Conflict(f'{job_id} was retried by {child}, which wrote to the vault after it. '
                       f'Roll back the retry child first: sb recover {child} --rollback')


def _resumed_child(root: Path, parent_id: str, state: dict) -> dict:
    """The existing child of ``parent_id``, led by a ``message`` that names the job to look at next.

    The child may have been retried in turn, so the message follows ``retried_by``
    to the newest job of the chain. A finished child comes back as a short
    summary (the whole state is in `sb job show`); an unfinished one whole, so
    the caller can advance it.
    """
    latest = state
    while latest.get('retried_by'):
        try:
            newer = jobs.load(root, latest['retried_by'])
        except ValueError:
            break
        if newer['status'] == 'rolled_back':
            break
        latest = newer
    name, status = latest['job_id'], latest['status']
    head = f'{parent_id} was retried already' + (f' (by {state["job_id"]})' if latest is state else
                                                  f'; the newest job of the chain is {name}')
    extra = {}
    if status == 'partial' and _stalled(latest):
        message = (f'{head}. {name} is partial and made no progress, so another retry would likely repeat it. '
                   + _STALLED_ADVICE.format(job=name))
    elif status == 'partial':
        message = f'{head}. {name} is partial; retry that job: sb ingest --retry {name}'
        extra['hint'] = f'sb ingest --retry {name}'
    elif status == 'applied':
        message = f'{head}. {name} is applied; nothing is left to retry.'
    elif status in {'failed', 'published'}:
        message = f'{head}. {name} is {status}; see sb job show {name}.'
    else:
        message = f'{head}. {name} is not finished ({status}); continue it: sb job advance {name}'
    lead = {'message': message, **extra, 'resumed': True, 'retry_of': parent_id, 'latest_job': name}
    if state['status'] not in {'applied', 'partial', 'failed', 'published'}:
        return {**lead, **state}
    return {**lead, **{k: state.get(k) for k in ('job_id', 'kind', 'status', 'created_at', 'updated_at',
                                                 'new_card_count', 'retry_progress')},
            'open_items': len(state.get('pending') or []) + len(state.get('needs_taxonomy') or [])}


#: What to do when a retry made no progress (``{job}`` is the stalled job).
_STALLED_ADVICE = ('Read the reasons in `pending` (sb job show {job}). Retry again only after changing what blocked '
                   'them, such as the taxonomy for a new topic or pillar; to leave them open and close the source, '
                   'run sb inbox skip <file> --reason "<why>".')


def _stalled(state: dict) -> bool:
    """True for a retry child that wrote no card and settled none of the items it was given."""
    return (state.get('retry_progress') or {}).get('progress') is False


# --------------------------------------------------------------------------
# Packets (S3 producer, S6 reviewer)
# --------------------------------------------------------------------------
def _base(slot: str) -> str:
    return slot.split('.', 1)[0]


def _round(slot: str) -> int:
    return int(slot.split('.r', 1)[1]) if '.r' in slot else 0


def _slot_name(base: str, rnd: int) -> str:
    return base if rnd == 0 else f'{base}.r{rnd}'


#: A chunk the endpoint refused as too large is split in halves, down to this size.
MIN_SPLIT_CHARS = 500


def _chunk(packet: dict, base: str) -> dict:
    """A frozen chunk, or a sub-chunk ``<parent>-<n>`` derived from it deterministically."""
    for chunk in packet['chunks']:
        if chunk['chunk_id'] == base:
            return chunk
    parent_id, sep, index = base.rpartition('-')
    if sep and index.isdigit():
        parent = _chunk(packet, parent_id)
        parts = _halves(packet, parent)
        if 1 <= int(index) <= len(parts):
            return parts[int(index) - 1]
    raise ValueError(f'Unknown chunk {base}')


def _halves(packet: dict, parent: dict) -> list[dict]:
    text = packet['source']['text'][parent['start']:parent['end']]
    parts = chunk_text(text, max(1, -(-len(text) // 2)))
    return [{**parent, 'chunk_id': f'{parent["chunk_id"]}-{i}', 'start': parent['start'] + p['start'],
             'end': parent['start'] + p['end'], 'split_reason': 'too_large'} for i, p in enumerate(parts, 1)]


def effective_chunks(packet: dict, state: dict) -> list[dict]:
    """Chunks in source order, with the ones split after ``CHUNK_TOO_LARGE`` expanded."""
    out = []

    def walk(chunk):
        if ((state.get('chunks') or {}).get(chunk['chunk_id']) or {}).get('split'):
            for part in _halves(packet, chunk):
                walk(part)
        else:
            out.append(chunk)
    for chunk in packet['chunks']:
        walk(chunk)
    return out


def _chunk_text(packet: dict, base: str) -> str:
    chunk = _chunk(packet, base)
    return packet['source']['text'][chunk['start']:chunk['end']]


def _taxonomy_of(packet: dict, domain: str) -> dict:
    moc = packet['taxonomy']['domain_mocs'].get(domain)
    return packet['taxonomy']['taxonomies'].get(moc) or {}


def _legal_pairs(packet: dict, domain: str) -> set:
    tax = _taxonomy_of(packet, domain)
    return {(p['name'], t) for p in tax.get('pillars', []) if isinstance(p, dict)
            for t in p.get('topics', []) if isinstance(t, str)}


def _written_slugs(packet: dict) -> dict:
    """slug -> id of the cards earlier jobs of this source wrote (retry jobs only)."""
    written = (packet.get('retry') or {}).get('written') or {}
    return {c['slug']: c['id'] for cards in written.values() for c in cards if c.get('slug')}


def _written_ids(packet: dict) -> list[str]:
    """Ids of the cards earlier jobs of this source wrote, every chunk (retry jobs only)."""
    written = (packet.get('retry') or {}).get('written') or {}
    return list(dict.fromkeys(c['id'] for cards in written.values() for c in cards if c.get('id')))


def _legal_listing(packet: dict, domain: str) -> str:
    """``Pillar: topic, topic; Pillar: topic`` for error messages a repair round can act on."""
    tax = _taxonomy_of(packet, domain)
    rows = [f'{p["name"]}: {", ".join(t for t in p.get("topics", []) if isinstance(t, str))}'
            for p in tax.get('pillars', []) if isinstance(p, dict)]
    return '; '.join(rows) or '(no curated topics yet; propose one in new_topics)'


def _relation_types(catalog: dict) -> list:
    from ..catalog import relation_types
    return relation_types(catalog)


def _relation_views(catalog: dict, domains) -> dict:
    """The relation types a packet shows: ``relation_types`` with the types open to every domain, and
    ``domain_relation_types`` with the extra types each of ``domains`` may use (only domains that have some)."""
    general = list(catalog.get('relation_types') or [])
    extra = {}
    for domain in dict.fromkeys(d for d in domains if isinstance(d, str)):
        own = [t for t in open_relation_types(catalog, domain) if t not in general]
        if own:
            extra[domain] = relation_meanings(catalog, types=own)
    return {'relation_types': relation_meanings(catalog, types=general), 'domain_relation_types': extra}


def _closed_relation(catalog: dict, kind: str, domain, target_domain) -> str | None:
    """Why a ``kind`` link from a ``domain`` card to a ``target_domain`` card may not be written, or None.

    A type in ``domain_relations`` is open only to the domains listed there, and
    the target gets the inverse type as its back link, so that must be open to
    the target's domain.
    """
    if kind not in open_relation_types(catalog, domain):
        return f'{kind!r} is not open to domain {domain}'
    inverse = (catalog.get('inverse_relations') or {}).get(kind)
    if inverse and target_domain and inverse not in open_relation_types(catalog, target_domain):
        return f'its back link {inverse!r} is not open to the target\'s domain {target_domain}'
    return None


def _candidate_taxonomies(packet: dict, domains: list) -> dict:
    out = {}
    counts = packet.get('topic_counts', {})
    for d in domains:
        tax = _taxonomy_of(packet, d)
        out[d] = {'moc': packet['taxonomy']['domain_mocs'].get(d),
                  'narrative': tax.get('narrative'),
                  'pillars': [{'name': p['name'], 'why': p.get('why'),
                               'topics': [{'name': t, 'cards': counts.get(d, {}).get(f'{p["name"]}\x1f{t}', 0)}
                                          for t in p.get('topics', [])]}
                              for p in tax.get('pillars', []) if isinstance(p, dict)]}
    return out


def _slots(state: dict, base: str) -> list[tuple[str, dict]]:
    chunks = state.get('chunks') or {}
    out, rnd = [], 0
    while _slot_name(base, rnd) in chunks:
        out.append((_slot_name(base, rnd), chunks[_slot_name(base, rnd)]))
        rnd += 1
    return out


def _other_cards(root: Path, state: dict, packet: dict, base: str, named=frozenset()) -> list[dict]:
    """Cards proposed for the other chunks of this source (latest per slug): each by its title, domain and essence;
    a card whose title is in ``named`` (the cards a reviewed card names by ``[[title]]``) with its propositions too,
    so the reviewer can check what such a card is cited for."""
    out = {}
    for chunk in effective_chunks(packet, state):
        if chunk['chunk_id'] == base:
            continue
        for slot, info in _slots(state, chunk['chunk_id']):
            if info.get('candidate'):
                for c in jobs.artifact(root, state, 'candidate', slot).get('cards', []):
                    if isinstance(c, dict) and isinstance(c.get('slug'), str):
                        out[c['slug']] = {'slug': c['slug'], 'title': c.get('title'),
                                          'domain': c.get('domain'), 'essence': c.get('essence'),
                                          'chunk': chunk['chunk_id'],
                                          **({'propositions': list(c.get('propositions') or [])}
                                             if c.get('title') in named else {})}
    return list(out.values())


_NAMED = re.compile(r'\[\[([^\[\]\n|#]+)')


def named_titles(cards) -> set:
    """The titles the text of ``cards`` names by ``[[title]]`` (an alias or a heading after it left out)."""
    return {m.strip() for c in cards or [] if isinstance(c, dict) for t in _text_fields(c) for m in _NAMED.findall(t)}


def _aux_slot(name: str) -> bool:
    """True for a slot that is not a chunk's: diagrams, the merge check, a dispute, a split card, a model note, the
    check of a review's notes."""
    return card_diagrams.is_slot(name) or source_check.is_slot(name) or _is_rejudge(name) or \
        split.is_slot(_base(name)) or notes.is_slot(name) or notecheck.is_slot(name)


def _note_due(state: dict, packet: dict, base: str) -> str | None:
    """The model note chunk ``base`` waits for before it is produced (the ``summary`` and ``concepts`` arms), or None
    when there is none or it is done or given up."""
    slot = notes.slot_for(_frozen(packet, 'context_note'), effective_chunks(packet, state), base)
    info = ((state.get('chunks') or {}).get(slot) or {}) if slot else {}
    return None if slot is None or info.get('candidate') or info.get('skipped') else slot


def _note_part(root: Path, state: dict, packet: dict, base: str) -> dict:
    """``{"context_note": ...}`` for chunk ``base`` (nothing when off, or for a source of one chunk)."""
    mode = _frozen(packet, 'context_note')
    if mode == 'off':
        return {}
    chunks = effective_chunks(packet, state)
    model = notes.slot_for(mode, chunks, base)
    answer = jobs.artifact(root, state, 'candidate', model) if model and \
        ((state.get('chunks') or {}).get(model) or {}).get('candidate') else None
    note = chunking.context_note(packet['source']['text'], chunks, base, _frozen(packet, 'context_note_chars'),
                                 summary=(answer or {}).get('summary') if mode == 'summary' else None,
                                 concepts=(answer or {}).get('concepts', []) if mode == 'concepts' else None)
    return {'context_note': note} if note else {}


def _note_step(root: Path, job_id: str, state: dict, packet: dict, slot: str, producer) -> None:
    """Ask the producer for a model note; an answer that keeps failing validation is given up (the chunk gets the
    program's note alone)."""
    from ..runner import call_executor
    chunks = effective_chunks(packet, state)
    text, filename = packet['source']['text'], packet['source']['filename']
    if slot == notes.CONCEPTS_SLOT:
        name, shown, schema = 'context_concepts.md', notes.concepts_packet(text, chunks, filename), notes.CONCEPTS_SCHEMA
    else:
        base = slot[len('nt-'):]
        name, shown, schema = ('context_summary.md', notes.summary_packet(text, chunks, base, filename),
                               notes.SUMMARY_SCHEMA)
    info = (state.get('chunks') or {}).get(slot) or {}
    prompt_text = _prompt_only(name)
    if info.get('last_validation_error'):
        prompt_text += '\n## Your previous answer was rejected by the validator\n\n' + info['last_validation_error'] + '\n'
    try:
        call_executor(root, job_id, producer, role='producer', step='produce', prompt_text=prompt_text, packet=shown,
                      output_schema=schema, chunk=slot)
    except ExecutorFailure as failure:
        _failed_answer(root, job_id, slot, 'producer', failure)


# --------------------------------------------------------------------------
# The check of a review's minor notes (notecheck)
# --------------------------------------------------------------------------
def _note_check(state: dict, slot: str) -> dict:
    """The job state of the check of the minor notes of the review in chunk slot ``slot``, when it checks the review
    the slot holds now; else empty (no check, or one made for a review the program set aside since)."""
    chunks = state.get('chunks') or {}
    info = chunks.get(notecheck.slot_for(slot)) or {}
    review = (chunks.get(slot) or {}).get('review') or {}
    return info if info and review and info.get('of_review') == review.get('sha256') else {}


def _note_check_due(root: Path, state: dict, slot: str) -> bool:
    """Whether the review of chunk slot ``slot`` waits for the check of its minor notes: a note to ask about, and the
    check neither answered nor given up. A check made for a review set aside since is not asked again."""
    info = _note_check(state, slot)
    if info:
        return not (info.get('review') or info.get('skipped'))
    if notecheck.slot_for(slot) in (state.get('chunks') or {}):
        return False
    return bool(notecheck.items(jobs.artifact(root, state, 'review', slot)))


def _review(root: Path, state: dict, slot: str) -> dict:
    """The review of chunk slot ``slot`` as every step reads it (:func:`_with_check`)."""
    return _with_check(root, state, slot, jobs.artifact(root, state, 'review', slot))


def _with_check(root: Path, state: dict, slot: str, review: dict) -> dict:
    """``review``, the reviewer's answer in chunk slot ``slot``, with the cards the check of its minor notes sent back
    put in (:func:`notecheck.applied`)."""
    if not _note_check(state, slot).get('review'):
        return review
    return notecheck.applied(review, jobs.artifact(root, state, 'review', notecheck.slot_for(slot)))


def _note_answers(root: Path, state: dict, slot: str) -> list[dict]:
    """The reviewer's answers about the minor notes of the review of chunk slot ``slot``, each with its card and
    note."""
    if not _note_check(state, slot).get('review'):
        return []
    asked = {i['id']: i for i in notecheck.items(jobs.artifact(root, state, 'review', slot))}
    check = jobs.artifact(root, state, 'review', notecheck.slot_for(slot))
    return [{'chunk': _base(slot), 'slot': slot, 'slug': asked[a['id']]['slug'], 'note': asked[a['id']]['note'], **a}
            for a in check.get('answers') or [] if a.get('id') in asked]


def _note_check_input(root: Path, state: dict, packet: dict, reviewed: str) -> dict:
    """What the reviewer answers the check on: each card asked about as accepted (its mechanical fixes made), the
    notes asked about, the chunk and its note, and the cards of the source the cards name."""
    from ..runner import withhold_reasons
    review = jobs.artifact(root, state, 'review', reviewed)
    rows = {r['slug']: r for r in review.get('cards') or []}
    cards = {c['slug']: c for c in jobs.artifact(root, state, 'candidate', reviewed).get('cards') or []}
    asked = notecheck.items(review)
    withhold = jobs.policy_of(packet).withhold_producer_reasons
    shown = []
    for slug in dict.fromkeys(i['slug'] for i in asked):
        card = {k: v for k, v in amends.apply(cards[slug], rows[slug].get('amend')).items() if not k.startswith('_')}
        shown.append({'slug': slug, 'card': withhold_reasons(card) if withhold else card,
                      'notes': [{'id': i['id'], 'text': i['note']} for i in asked if i['slug'] == slug]})
    base = _base(reviewed)
    return {'chunk': {'id': base, 'text': _chunk_text(packet, base)}, **_note_part(root, state, packet, base),
            'cards': shown,
            'other_chunks_cards': _other_cards(root, state, packet, base, named_titles([c['card'] for c in shown]))}


def note_check_prompt(job_id: str) -> str:
    """The instructions of the check of a review's minor notes."""
    return _prompt('note_check.md', {'job_id': job_id})


def _note_check_step(root: Path, job_id: str, state: dict, packet: dict, slot: str, reviewer) -> None:
    """Ask the chunk's reviewer about the minor notes of its review that may name a blocking problem; the check's
    input is made once, from the review the chunk slot holds (``of_review``, marked before the input is stored)."""
    from ..runner import call_executor
    reviewed = notecheck.reviewed(slot)
    if not _note_check(state, reviewed).get('candidate'):
        with jobs.reading('note_check_packet', (reviewed, 'reviewer')):  # made from that review
            made = _note_check_input(root, state, packet, reviewed)
        _mark_slot(root, job_id, slot, of_review=state['chunks'][reviewed]['review']['sha256'])
        jobs.put_program_candidate(root, job_id, slot, made)
        state = jobs.load(root, job_id)
    text = note_check_prompt(job_id)
    feedback = state['chunks'][slot].get('last_review_error')
    if feedback:
        text += '\n## Your previous answer was rejected by the validator\n\n' + feedback + '\n'
    with jobs.reading('note_check_packet'):  # the program's own input: no answer to set aside
        shown = jobs.artifact(root, state, 'candidate', slot)
    try:
        call_executor(root, job_id, reviewer, role='reviewer', step='review', prompt_text=text, packet=shown,
                      output_schema=notecheck.SCHEMA, chunk=slot)
    except ExecutorFailure as failure:
        _failed_answer(root, job_id, slot, 'reviewer', failure)


def producer_packet(root: Path, state: dict, packet: dict, slot: str) -> dict:
    base, rnd = _base(slot), _round(slot)
    chunk = _chunk(packet, base)
    order = [c['chunk_id'] for c in effective_chunks(packet, state)]
    domains = [{'slug': d, 'moc': m['moc'], 'question': m.get('question')}
               for d, m in sorted(packet['catalog']['domains'].items())]
    out = {'source': {'filename': packet['source']['filename'], 'sha256': packet['source']['sha256']},
           'chunk': {'id': base, 'index': order.index(base) + 1 if base in order else None,
                     'total': len(order), 'text': _chunk_text(packet, base)},
           'domains': domains,
           'candidate_taxonomies': _candidate_taxonomies(packet, chunk['candidate_domains']),
           **_relation_views(packet['catalog'], chunk['candidate_domains']),
           **_chunk_candidates(chunk),
           'other_chunks_cards': _other_cards(root, state, packet, base),
           **_note_part(root, state, packet, base),
           **({'placement': packet['placement']} if packet.get('placement') else {}),
           'rules': {'new_topics_per_source': packet['settings']['new_topics_per_source'],
                     'flashcards_per_card': [2, 4], 'id_prefix': packet['settings']['id_prefix']}}
    if packet.get('retry'):
        out['retry'] = _retry_view(packet, base)
    if rnd:
        prev_slot = _slot_name(base, rnd - 1)
        previous = jobs.artifact(root, state, 'candidate', prev_slot)
        findings = _findings(_review(root, state, prev_slot))
        held = _held_slugs(root, state, base, rnd)  # accepted as the same as existing cards: settled, not kept
        kept = {s: c for s, c in _accepted_so_far(root, state, base, rnd).items() if s not in held}
        out['previous_candidate'] = jobs.as_given(previous)  # what the program wrote into it is not shown back
        out['review_findings'] = findings
        out['kept_slugs'] = sorted(kept)
        out['already_settled'] = _settled_rows(root, state, base, rnd)
        refused = _unaccepted_topics(root, state, base, rnd)
        out['refile_cards'] = [{**c, 'topic_review': refused[(c['domain'], c['pillar'], c['topic'])]}
                               for c in kept.values() if (c['domain'], c['pillar'], c['topic']) in refused]
        wanted = list(chunk['candidate_domains'])
        for domain in _domains_named(packet, findings) + [c.get('domain') for c in previous.get('cards', [])]:
            if domain in packet['catalog']['domains'] and domain not in wanted:
                wanted.append(domain)
        out['candidate_taxonomies'] = _candidate_taxonomies(packet, wanted)
        out['domain_relation_types'] = _relation_views(packet['catalog'], wanted)['domain_relation_types']
    return out


def _chunk_candidates(chunk: dict) -> dict:
    """A frozen chunk's ``{candidates, candidate_cards}``; a chunk frozen with ``neighbors`` reads as one key."""
    if 'candidates' in chunk:
        return {'candidates': chunk['candidates'], 'candidate_cards': chunk['candidate_cards']}
    old = [n for n in chunk.get('neighbors') or [] if isinstance(n, dict) and n.get('id')]
    return {'candidates': {'chunk': [{'id': n['id'], 'rank': i, 'via': ['words']} for i, n in enumerate(old, 1)]}
            if old else {}, 'candidate_cards': {n['id']: n for n in old}}


def _domains_named(packet: dict, findings: dict) -> list:
    """Domains a reviewer asked for: ``suggested_domain`` fields, or domain names in the reasons."""
    names = sorted(packet['catalog']['domains'], key=len, reverse=True)
    found = []
    for row in findings.get('cards', []) + findings.get('new_topics', []) + [findings.get('coverage') or {}]:
        if isinstance(row.get('suggested_domain'), str):
            found.append(row['suggested_domain'])
        text = str(row.get('reason') or '')
        found += [d for d in names if re.search(r'(?<![\w-])' + re.escape(d) + r'(?![\w-])', text)]
    return list(dict.fromkeys(found))


def _findings(review: dict) -> dict:
    return {'cards': [x for x in review.get('cards', []) if x.get('decision') != 'accept'],
            'new_topics': [x for x in review.get('new_topics', []) if x.get('decision') != 'accept'],
            'coverage': review.get('coverage') if (review.get('coverage') or {}).get('decision') != 'accept' else None}


#: The view each route's candidates get in a reviewer packet: what a repeat or a conflict is judged on
#: (``words`` takes the frozen ``review_words_view``).
REVIEWER_LEVELS = {'named': 'full', 'written': 'full', 'words': 'full', 'hop': 'brief', 'shelf': 'title'}


def named_ids(card: dict, existing: set, aliases: dict | None = None) -> list[str]:
    """The existing cards a proposed card names: relation targets, conflicts and its overlap hint, in that order.

    ``aliases`` maps a slug to the id of a card earlier jobs of the source wrote (a retry card may link one by slug).
    """
    aliases = aliases or {}
    out = [aliases.get(r.get('target'), r.get('target')) for r in card.get('relations') or [] if isinstance(r, dict)]
    out += [r.get('with') for r in card.get('conflicts') or [] if isinstance(r, dict)]
    hint = card.get('overlap_hint')
    out += list(hint.get('with') or []) if isinstance(hint, dict) else []
    return [i for i in dict.fromkeys(out) if isinstance(i, str) and i in existing]


def coverage_named(candidate: dict, existing: set) -> list[str]:
    """The existing cards the coverage rows say already hold a passage."""
    out = []
    for row in candidate.get('coverage') or []:
        if isinstance(row, dict):
            out += list(row.get('with') or row.get('duplicate_of') or [])
    return [i for i in dict.fromkeys(out) if isinstance(i, str) and i in existing]


def _review_candidates(root: Path, snapshot, packet: dict, cards: list, coverage: list | None = None
                       ) -> tuple[dict, dict]:
    """``({slug: [{id, rank, via}]}, {id: view})`` for the reviewer packet.

    Each proposed card gets the existing cards it names (``via: named``, whole,
    outside the budget) when ``review_named`` is on, then the first route's
    ``k`` cards, the second route's and its topic's shelf when those are on.
    ``coverage`` (ids the coverage rows name) is listed under the key
    ``"#coverage"``. Each existing card appears once in the view table, however
    many proposed cards it is near; past ``candidates_chars`` the lower-ranked
    retrieved cards are shown at a shorter view (:func:`secondbrain.retrieval.fit`).
    """
    k = retrieval.k_for(_frozen(packet, 'candidates_k'), 'reviewer')
    named = _frozen(packet, 'review_named')
    existing = set(snapshot.cards)
    # a retry: the cards earlier jobs of the source wrote are judged here too (via: written), ranked among themselves
    written = replace(snapshot, cards={i: snapshot.cards[i] for i in _written_ids(packet) if i in snapshot.cards})
    written_k = retrieval.k_for(_frozen(packet, 'candidates_k'), 'written')
    per_card = {}
    for c in cards:
        if not (isinstance(c, dict) and isinstance(c.get('slug'), str)):
            continue
        shelf_of = (c.get('domain'), c.get('pillar'), c.get('topic')) if _frozen(packet, 'candidates_shelf') else None
        linked_written = [i for i in named_ids(c, existing, _written_slugs(packet)) if i in written.cards]
        per_card[c['slug']] = retrieval.candidates(
            root, snapshot, retrieval.card_query(c), k=k, hop_from=_frozen(packet, 'candidates_hop'),
            named=named_ids(c, existing, _written_slugs(packet)) if named else linked_written, shelf_of=shelf_of,
            written=written if written.cards else None, written_k=written_k)
    if named and coverage:
        per_card[COVERAGE_KEY] = [{'id': i, 'rank': n, 'via': ['named']} for n, i in enumerate(coverage, 1)]
    levels = {**REVIEWER_LEVELS, 'words': _frozen(packet, 'review_words_view')}
    table, _ = retrieval.fit(snapshot, retrieval.merge(per_card.values()), _frozen(packet, 'candidates_chars'), levels)
    return {slug: retrieval.keep(rows, table) for slug, rows in per_card.items()}, table


#: The key of the reviewer packet's ``candidates`` that lists the cards the coverage rows name (never a slug).
COVERAGE_KEY = '#coverage'


#: what the program adds to a producer's answer it passes on: the mechanical repairs it made, and the cards the
#: slot's last answer left out because they failed the checks (see :mod:`secondbrain.pipelines.answerfix`)
PROGRAM_FIELDS = ('program_repairs', 'held_cards')


def _fence_record(run: dict) -> dict:
    """What the program set aside to read a run's answer (``run.unwrapped``), as a repair record."""
    if run.get('unwrapped') == 'around':
        return {'rule': 'around', 'run_id': run['run_id'],
                'what': 'words stood before or after the JSON object of the answer; the program read the object alone'}
    return {'rule': 'fence', 'run_id': run['run_id'],
            'what': 'the answer was wrapped in one Markdown code fence; the program took it off before reading it'}


def _program_view(root: Path, state: dict, slot: str, candidate: dict) -> dict:
    """The program's repairs of a candidate and the cards it left out, for the reviewer: a fence taken off its
    answer first, then the repairs of its cards."""
    ref = ((state.get('chunks') or {}).get(slot) or {}).get('candidate') or {}
    run = jobs.load_run(root, state['job_id'], ref['run_id']) if ref.get('run_id') else {}
    repairs = ([_fence_record(run)] if run.get('unwrapped') else []) + list(candidate.get('program_repairs') or [])
    return {**({'program_repairs': repairs} if repairs else {}),
            # the reviewer is told which cards were held back and why, not their text: it does not judge them
            **({'held_cards': [{k: v for k, v in h.items() if k != 'card'} for h in candidate['held_cards']]}
               if candidate.get('held_cards') else {})}


def program_repairs(root: Path, state: dict) -> list[dict]:
    """Every repair the program made to an answer of this job, writing no knowledge text, by slot: a code fence or
    words around the answer taken off, and fields only the program writes taken out (any role), and the mechanical
    repairs of the cards of the chunk and split producers (:mod:`.answerfix`)."""
    runs = jobs.list_runs(root, state['job_id'])
    out = [{'slot': run.get('chunk'), 'role': run['role'], **_fence_record(run)} for run in runs if run.get('unwrapped')]
    out += [{'slot': run.get('chunk'), 'role': run['role'], 'rule': 'program_fields', 'run_id': run['run_id'],
             'removed': run['program_fields_removed'],
             'what': 'the answer carried fields only the program writes; the program took them out before checking it'}
            for run in runs if run.get('program_fields_removed')]
    for slot, info in sorted((state.get('chunks') or {}).items()):
        if (info.get('candidate') or {}).get('path') and (not _aux_slot(slot) or split.is_slot(_base(slot))):
            out += [{'slot': slot, **r}
                    for r in jobs.artifact(root, state, 'candidate', slot).get('program_repairs') or []]
    return out


def review_packet(root: Path, state: dict, packet: dict, slot: str) -> dict:
    from ..runner import withhold_reasons
    base, rnd = _base(slot), _round(slot)
    candidate = jobs.artifact(root, state, 'candidate', slot)
    shown = withhold_reasons(candidate) if jobs.policy_of(packet).withhold_producer_reasons else candidate
    shown = {k: v for k, v in shown.items() if k not in PROGRAM_FIELDS}
    snapshot = read_snapshot(root)
    domains = sorted({c['domain'] for c in candidate.get('cards', []) if c.get('domain') in packet['catalog']['domains']}
                     | {t['domain'] for t in candidate.get('new_topics', []) if t.get('domain') in packet['catalog']['domains']})
    near, near_cards = _review_candidates(root, snapshot, packet, candidate.get('cards', []),
                                          coverage_named(candidate, set(snapshot.cards)))
    out = {'chunk': {'id': base, 'text': _chunk_text(packet, base)},
           'candidate': shown,
           'candidates': near, 'candidate_cards': near_cards,
           'taxonomies': _candidate_taxonomies(packet, domains),
           'domains': [{'slug': d, 'question': m.get('question')}
                       for d, m in sorted(packet['catalog']['domains'].items())],
           **_relation_views(packet['catalog'], domains),
           'other_chunks_cards': _other_cards(root, state, packet, base, named_titles(candidate.get('cards'))),
           **_note_part(root, state, packet, base),
           **({'placement': packet['placement']} if packet.get('placement') else {}),
           # what the program changed in the answer, writing no knowledge text, and the cards it left out
           **_program_view(root, state, slot, candidate)}
    asked = cardlint.of_cards(candidate.get('cards'))
    if asked:  # the program's questions on the cards, for the reviewer to answer yes or no (pipelines.cardlint)
        out['program_questions'] = asked
    if rnd:
        kept = _accepted_so_far(root, state, base, rnd)
        refiled = {c.get('slug') for c in candidate.get('cards', []) if isinstance(c, dict)}
        held = _held_slugs(root, state, base, rnd)  # accepted as the same as existing cards: settled, not kept
        out['already_accepted_cards'] = [withhold_reasons(c) for s, c in kept.items()
                                         if s not in refiled and s not in held]
        out['already_settled'] = _settled_rows(root, state, base, rnd)
        if jobs.policy_of(packet).focused_review:  # what the round before sent back, to be judged fixed or not
            out['previous_findings'] = _findings(_review(root, state, _slot_name(base, rnd - 1)))
    if packet.get('retry'):
        out['retry'] = _retry_view(packet, base, reviewer=True)
    return out


def _retry_view(packet: dict, base: str, *, reviewer: bool = False) -> dict:
    """What earlier jobs of this source left for chunk ``base``: open findings, written cards, settled passages.

    A reviewer does not see the producer's reason on a ``needs_taxonomy`` finding;
    the other findings carry reviewers' reasons.
    """
    retry = packet['retry']
    settled = (retry.get('settled') or {}).get(base) or {}
    findings = retry['pending'].get(base, [])
    if reviewer:
        findings = [{k: v for k, v in f.items() if not (k == 'reason' and 'suggested_pillar' in f)} for f in findings]
    return {'findings': findings, 'written_cards': retry['written'].get(base, []),
            'duplicates': settled.get('duplicates', []), 'out_of_scope': settled.get('out_of_scope', [])}


# --------------------------------------------------------------------------
# S4 validation (pure on the frozen packet + current vault titles/ids)
# --------------------------------------------------------------------------
def _text_fields(card: dict) -> list[str]:
    out = [card.get('title'), card.get('essence'), card.get('intuition')]
    out += list(card.get('propositions') or []) + list(card.get('confusions') or [])
    for f in card.get('flashcards') or []:
        if isinstance(f, dict):
            out += [f.get('q'), f.get('a')]
    out += [r.get('note') for r in card.get('relations') or [] if isinstance(r, dict)]
    return [x for x in out if isinstance(x, str)]


def _vault_ids_titles(root) -> tuple[set, set, dict]:
    """Existing card ids, titles as :func:`secondbrain.textnorm.plain` gives them, and ``{id: domain}``."""
    snap = read_snapshot(root)
    return (set(snap.cards), {plain(c.title) for c in snap.cards.values() if c.title},
            {cid: c.domain for cid, c in snap.cards.items()})


@schemas.shape_checked(schemas.CARDS, what='candidate')
def validate_candidate(packet: dict, payload: dict, *, slot: str, other_cards: list[dict],
                       kept_slugs: set, existing_ids: set, existing_titles: set,
                       known_topics: set = frozenset(), target_domains: dict | None = None) -> dict:
    """Raise :class:`IngestError` listing every problem; return the payload.

    ``target_domains`` maps the ids and slugs a relation may point to (existing
    cards, cards of other chunks and rounds) to their domain, so a relation
    whose back link the target's domain does not use is refused.
    """
    errors: list[str] = []
    base = _base(slot)
    text = _chunk_text(packet, base)
    catalog = packet['catalog']
    prefix = packet['settings']['id_prefix']
    bare_id = re.compile(re.escape(prefix) + r'[A-Za-z0-9]') if prefix else None
    relation_types = set(_relation_types(catalog))
    if not isinstance(payload, dict):
        raise IngestAnswerError('candidate must be a JSON object')
    cards = payload.get('cards')
    topics = payload.get('new_topics', [])
    coverage = payload.get('coverage')
    if not isinstance(cards, list) or not isinstance(topics, list):
        raise IngestAnswerError('cards and new_topics must be lists')
    if not isinstance(coverage, list) or not coverage:
        raise IngestAnswerError('coverage must list how every part of the chunk is handled')
    other_slugs = {c['slug'] for c in other_cards}
    other_titles = {plain(c.get('title')) for c in other_cards}
    domain_of = dict(target_domains or {})
    domain_of.update({c['slug']: c.get('domain') for c in cards if isinstance(c, dict) and isinstance(c.get('slug'), str)})
    proposed = {}
    for i, t in enumerate(topics):
        where = f'new_topics[{i}]'
        if not isinstance(t, dict):
            errors.append(f'{where} must be an object')
            continue
        domain, pillar, name = t.get('domain'), t.get('pillar'), t.get('name')
        if domain not in catalog['domains']:
            errors.append(f'{where}.domain {domain!r} is not a catalog domain')
            continue
        tax = _taxonomy_of(packet, domain)
        pillars = {p['name']: p for p in tax.get('pillars', []) if isinstance(p, dict)}
        if pillar not in pillars:
            errors.append(f'{where}.pillar {pillar!r} does not exist in {domain} (pillars: '
                          f'{", ".join(sorted(pillars)) or "none yet"}); a new topic must sit under an existing '
                          'pillar. For material that needs a new pillar or domain, write a coverage row with '
                          'disposition needs_taxonomy instead; the vault owner adds pillars and domains '
                          '(sb curate, sb domcreate)')
            continue
        if not isinstance(name, str) or not name.strip() or '\n' in name:
            errors.append(f'{where}.name must be a one-line string')
            continue
        existing = [x for p in pillars.values() for x in p.get('topics', []) if isinstance(x, str)]
        n = plain(name)  # the same words, or one name inside the other
        clash = [x for x in existing if same_text(x, name) or (len(n) >= 2 and len(plain(x)) >= 2 and
                                                               (n in plain(x) or plain(x) in n))]
        if clash:
            errors.append(f'{where}.name {name!r} duplicates or overlaps existing topic {clash[0]!r}; use it instead')
        for key in ('boundary', 'reason'):
            if not isinstance(t.get(key), str) or not t[key].strip():
                errors.append(f'{where}.{key} must be a non-empty string')
        if (domain, pillar, name) in proposed:
            errors.append(f'{where} repeats another proposal')
        proposed[(domain, pillar, name)] = t
    limit = packet['settings']['new_topics_per_source']
    if len(set(proposed) | set(known_topics)) > limit:
        errors.append(f'at most {limit} new topics per source, got {len(set(proposed) | set(known_topics))} '
                      'counting the other chunks and rounds')
    slugs, titles, used_topics = set(), set(), set()
    for i, c in enumerate(cards):
        where = f'cards[{i}]'
        if not isinstance(c, dict):
            errors.append(f'{where} must be an object')
            continue
        slug = c.get('slug')
        if not isinstance(slug, str) or not _SLUG.match(slug):
            errors.append(f'{where}.slug must match [a-z0-9][a-z0-9-]{{0,59}}')
        elif slug in slugs or slug in other_slugs:  # a kept slug files that card again (repair rounds)
            errors.append(f'{where}.slug {slug!r} is already used in this source')
        elif f'{prefix}{slug}' in existing_ids:
            # the card would get another id ("-2"), and its slug reads like the existing card's (drill 2, P4)
            errors.append(f'{where}.slug {slug!r} would make the id {prefix}{slug}, which an existing card has; '
                          'choose another slug, or if it is that card\'s knowledge, write a `same` coverage row')
        else:
            slugs.add(slug)
        where = f'cards[{slug or i}]'
        problem = cardfmt.title_problem(c.get('title'))
        if problem:
            errors.append(f'{where}: {problem}')
        else:
            folded = plain(c['title'])  # titles that say the same words clash (same_text)
            if folded in titles or folded in other_titles:
                errors.append(f'{where}: title repeats another card of this source')
            elif folded in existing_titles:
                errors.append(f'{where}: a card titled {c["title"]!r} already exists; '
                              'if it is the same knowledge, mark the coverage row as `same`')
            titles.add(folded)
        if c.get('kind') not in cardfmt.KINDS:
            errors.append(f'{where}.kind must be one of {", ".join(cardfmt.KINDS)}')
        if c.get('status', 'confirmed') not in cardfmt.STATUSES:
            errors.append(f'{where}.status must be confirmed or needs-review')
        domain = c.get('domain')
        if domain not in catalog['domains']:
            errors.append(f'{where}.domain {domain!r} is not a catalog domain; '
                          f'legal domains: {", ".join(sorted(catalog["domains"]))}')
        else:
            pair = (c.get('pillar'), c.get('topic'))
            if pair not in _legal_pairs(packet, domain):
                if (domain, *pair) in proposed:
                    used_topics.add((domain, *pair))
                elif (domain, *pair) not in known_topics:
                    errors.append(f'{where}: ({pair[0]!r}, {pair[1]!r}) is neither a topic of {domain} '
                                  'nor a new topic proposed for this source; legal topics of '
                                  f'{domain}: {_legal_listing(packet, domain)}')
        if not isinstance(c.get('essence'), str) or not c['essence'].strip():
            errors.append(f'{where}.essence must be one non-empty sentence')
        props = c.get('propositions')
        if not isinstance(props, list) or not 1 <= len(props) <= 12 or \
                any(not isinstance(p, str) or not p.strip() for p in props):
            errors.append(f'{where}.propositions must be 1-12 non-empty strings')
        if c.get('intuition') is not None and not isinstance(c.get('intuition'), str):
            errors.append(f'{where}.intuition must be a string or null')
        conf = c.get('confusions', [])
        if not isinstance(conf, list) or any(not isinstance(x, str) or not x.strip() for x in conf):
            errors.append(f'{where}.confusions must be a list of strings')
        cards_qa = c.get('flashcards')
        if not isinstance(cards_qa, list) or not 2 <= len(cards_qa) <= 4:
            errors.append(f'{where}.flashcards must hold 2-4 question/answer pairs')
        else:
            for qa in cards_qa:
                if not isinstance(qa, dict) or not all(isinstance(qa.get(k), str) and qa[k].strip() for k in ('q', 'a')):
                    errors.append(f'{where}.flashcards entries need non-empty q and a')
                elif '::' in qa['q'] or '::' in qa['a']:
                    errors.append(f'{where}.flashcards must not contain "::" (it separates question and answer)')
        aliases = c.get('aliases', [])
        if not isinstance(aliases, list) or any(not isinstance(a, str) or not a.strip() or '\n' in a for a in aliases):
            errors.append(f'{where}.aliases must be one-line strings')
        quotes = c.get('source_quotes')
        if not isinstance(quotes, list) or not quotes:
            errors.append(f'{where}.source_quotes must list verbatim passages from the chunk')
        else:
            for q in quotes:
                problem = check_verbatim(text, q)
                if problem:
                    errors.append(f'{where}.source_quotes: {problem}')
        rels = c.get('relations', [])
        if not isinstance(rels, list):
            errors.append(f'{where}.relations must be a list')
            rels = []
        open_types = open_relation_types(catalog, domain) if domain in catalog['domains'] else relation_types
        for r in rels:
            if not isinstance(r, dict) or r.get('type') not in open_types:
                got = r.get('type') if isinstance(r, dict) else r
                errors.append(f'{where}.relations: type must be one of {sorted(open_types)} (the types open to '
                              f'{domain}; got {got!r})')
                continue
            target = r.get('target')
            closed = _closed_relation(catalog, r['type'], domain, domain_of.get(target))
            if closed:
                errors.append(f'{where}.relations: {r["type"]} to {target!r}: {closed}; choose a type both '
                              'domains use')
            if not isinstance(target, str) or target == slug or not (
                    target in existing_ids or target in other_slugs or target in kept_slugs
                    or target in _written_slugs(packet)
                    or any(isinstance(x, dict) and x.get('slug') == target for x in cards)):
                errors.append(f'{where}.relations: target {target!r} is neither an existing card id '
                              'nor a slug of this source')
            if r.get('tier') not in (None, 'extension'):
                errors.append(f'{where}.relations: tier must be null or "extension"')
            if r.get('note') is not None and not isinstance(r.get('note'), str):
                errors.append(f'{where}.relations: note must be a string')
        errors += _conflict_errors(where, c.get('conflicts'), existing_ids)
        hint = c.get('overlap_hint')
        if hint is not None:
            problem = overlap.with_problem(hint.get('overlap'), hint.get('with') or [],
                                           overlap.KnownIds(existing_ids, 'existing')) \
                if isinstance(hint, dict) else 'must be {"overlap": ..., "with": [...]}'
            if problem:
                errors.append(f'{where}.overlap_hint: {problem}')
        if any(isinstance(r, dict) and r.get('type') == 'contrasts_with' for r in rels) and not conf:
            errors.append(f'{where}: a card with a contrasts_with relation states the contrast in confusions')
        if bare_id:
            for field_text in _text_fields(c):
                if bare_id.search(field_text):
                    errors.append(f'{where}: refer to other cards by [[title]], not by id ({prefix}...)')
                    break
        if not isinstance(c.get('reason'), str) or not c['reason'].strip():
            errors.append(f'{where}.reason must explain why this is one recallable unit')
    unused = set(proposed) - used_topics
    for key in sorted(unused):
        errors.append(f'new topic {key[2]!r} is not used by any card')
    errors += _reversed_relations(catalog, cards)
    errors += _repeated_targets(catalog, cards)
    covered = set()
    for i, row in enumerate(coverage):
        where = f'coverage[{i}]'
        if not isinstance(row, dict):
            errors.append(f'{where} must be an object')
            continue
        problem = check_verbatim(text, row.get('source_quote'))
        if problem:
            errors.append(f'{where}.source_quote: {problem}')
        disp = row.get('disposition')
        if disp not in DISPOSITIONS:
            errors.append(f'{where}.disposition must be one of {sorted(DISPOSITIONS)}')
            continue
        listed = row.get('cards') or []
        if disp == 'card':
            if not listed or not set(listed) <= slugs:
                errors.append(f'{where}: card rows must list slugs of this candidate')
            covered.update(x for x in listed if isinstance(x, str))
        elif listed:
            errors.append(f'{where}: only card rows may list cards')
        if disp == 'same':
            held = row.get('with') or []
            if not held or any(x not in existing_ids for x in held):
                errors.append(f'{where}: a `same` row names the existing cards that hold the passage, by id, in `with`')
        elif row.get('with'):
            errors.append(f'{where}: only `same` rows name cards in `with`')
        if disp == 'needs_taxonomy' and (not isinstance(row.get('suggested_pillar'), str) or not row['suggested_pillar'].strip()):
            errors.append(f'{where}: needs_taxonomy rows must give suggested_pillar')
        if not isinstance(row.get('reason'), str) or not row['reason'].strip():
            errors.append(f'{where}.reason must be a non-empty string')
    missing = slugs - covered
    if missing:
        errors.append(f'every card needs a coverage row; missing {sorted(missing)}')
    if errors:
        raise IngestAnswerError('Candidate rejected by validation:\n- ' + '\n- '.join(errors))
    return payload


def _reversed_relations(catalog: dict, cards: list) -> list[str]:
    """Relations of one answer that contradict each other's direction.

    With ``inverse_relations`` saying ``tool_for`` and ``used_by`` are inverses,
    a card cannot hold both to one target, and two cards cannot each claim
    ``tool_for`` of the other: one of the two is written the wrong way round.
    """
    inverse = catalog.get('inverse_relations') or {}
    meanings = relation_meanings(catalog)
    held = set()
    for c in cards:
        if isinstance(c, dict) and isinstance(c.get('slug'), str) and isinstance(c.get('relations'), list):
            held |= {(c['slug'], r['type'], r['target']) for r in c['relations']
                     if isinstance(r, dict) and isinstance(r.get('type'), str) and isinstance(r.get('target'), str)}
    errors = []
    for a, kind, b in sorted(held):
        other = inverse.get(kind)
        if not other or other == kind:
            continue
        if (a, other, b) in held and kind < other:
            errors.append(f'cards[{a}].relations: {kind} and {other} to {b!r} say opposite things '
                          f'({meanings.get(kind, kind)}); keep the one that matches the meaning')
        if (b, kind, a) in held and a < b:
            errors.append(f'cards[{a}] and cards[{b}]: "{a} {kind} {b}" and "{b} {kind} {a}" cannot both hold '
                          f'({meanings.get(kind, kind)}); one of them points the wrong way')
    return errors


def _conflict_errors(where: str, rows, existing_ids: set) -> list[str]:
    if rows is None:
        return []
    if not isinstance(rows, list):
        return [f'{where}.conflicts must be a list']
    return [f'{where}.conflicts[{i}] needs "with" (an existing card id) and "detail" (what differs)'
            for i, row in enumerate(rows)
            if not isinstance(row, dict) or row.get('with') not in existing_ids
            or not isinstance(row.get('detail'), str) or not row['detail'].strip()]


def _repeated_targets(catalog: dict, cards: list) -> list[str]:
    """Two cards joined by more than one relation, counting the back link the program writes.

    "A has_part B" with "A depends_on B", or with "B required_by A", leave both
    cards with two links to each other (drill 2, cards 12 and 14); one is kept.
    """
    inverse = catalog.get('inverse_relations') or {}
    slugs = {c['slug'] for c in cards if isinstance(c, dict) and isinstance(c.get('slug'), str)}
    pairs: dict = {}
    for c in cards:
        if not (isinstance(c, dict) and c.get('slug') in slugs and isinstance(c.get('relations'), list)):
            continue
        for r in c['relations']:
            if isinstance(r, dict) and isinstance(r.get('type'), str) and isinstance(r.get('target'), str):
                pairs.setdefault((c['slug'], r['target']), set()).add(r['type'])
                if r['target'] in slugs and inverse.get(r['type']):
                    pairs.setdefault((r['target'], c['slug']), set()).add(inverse[r['type']])
    errors = []
    for (a, b), kinds in sorted(pairs.items()):
        if a not in slugs or (b in slugs and b < a) or len(kinds) < 2:
            continue
        if len(kinds) == 2 and inverse.get(min(kinds)) == max(kinds):
            continue  # opposite directions: _reversed_relations says so
        errors.append(f'cards[{a}].relations: {len(kinds)} relations between {a!r} and {b!r} '
                      f'({", ".join(sorted(kinds))}, counting the back links); keep the one that fits best')
    return errors


@schemas.shape_checked(schemas.REVIEW_CARDS, what='review')
def validate_review(candidate: dict, review: dict, existing_ids: set, domains=(), asked: dict | None = None) -> dict:
    """Shape checks of a review, and the values the program requires.

    A card is accepted (its content is right) or revised. Every accepted card
    gets its level against the vault's cards (:data:`secondbrain.overlap.LEVELS`)
    with ``with`` naming existing cards only: the reviewer is the final judge of
    that value, and the write step looks its outcome up (``same``: not written;
    ``extends``: goes into the existing card; ``distinct``: written). No
    word-overlap score separated near-duplicates from other cards in drill 2,
    so the reviewer decides, every time. An ``extends`` card links the card it
    extends, so the pair is linked when that card cannot take it.
    """
    errors = []
    asked = cardlint.of_cards(candidate.get('cards')) if asked is None else asked
    if not isinstance(review, dict):
        raise IngestAnswerError('review must be a JSON object')
    slugs = [c['slug'] for c in candidate.get('cards', [])]
    by_slug = {c['slug']: c for c in candidate.get('cards', []) if isinstance(c, dict)}
    rows = review.get('cards')
    if not isinstance(rows, list):
        raise IngestAnswerError('review.cards must be a list')
    known = overlap.KnownIds(existing_ids, 'existing')
    seen = []
    for row in rows:
        if not isinstance(row, dict) or row.get('slug') not in slugs:
            errors.append(f'review row {row!r} does not name a candidate slug')
            continue
        seen.append(row['slug'])
        if row.get('decision') not in {'accept', 'revise'}:
            errors.append(f'{row["slug"]}: decision must be accept or revise')
        if row.get('suggested_domain') is not None and row['suggested_domain'] not in domains:
            errors.append(f'{row["slug"]}: suggested_domain must be one of {", ".join(sorted(domains))}')
        if not isinstance(row.get('reason'), str) or not row['reason'].strip():
            errors.append(f'{row["slug"]}: every decision needs a concrete reason')
        level, held = row.get('overlap'), row.get('with')
        if row.get('decision') == 'accept':
            problem = overlap.with_problem(level, held if held is not None else [], known)
            if level is None:
                errors.append(f'{row["slug"]}: an accepted card needs overlap ({", ".join(overlap.LEVELS)}) and '
                              '`with`: the existing cards it is compared with (ids)')
            elif problem:
                errors.append(f'{row["slug"]}: {problem}')
            elif level == 'extends':
                linked = {r.get('target') for r in by_slug[row['slug']].get('relations') or [] if isinstance(r, dict)}
                linked |= {a.get('target') for a in row.get('amend') or []
                           if isinstance(a, dict) and a.get('op') == 'add_relation'}
                if held[0] not in linked:
                    errors.append(f'{row["slug"]}: an `extends` card links the card it extends ({held[0]}); revise '
                                  'and ask for the link')
        # a revised card's level is not read: it is judged again once the card is right
        errors += _conflict_errors(row['slug'], row.get('conflicts'), existing_ids)
        errors += review_notes.problems(row.get('notes'), row['slug'], review_notes.CARD_CATEGORIES)
        errors += cardlint.answer_problems(row, asked.get(row['slug'], []))
    if sorted(seen) != sorted(slugs):
        errors.append('the review must judge every candidate card exactly once')
    keys = [(t.get('domain'), t.get('pillar'), t.get('name')) for t in candidate.get('new_topics', [])]
    judged = []
    for row in review.get('new_topics', []) or []:
        key = (row.get('domain'), row.get('pillar'), row.get('name')) if isinstance(row, dict) else None
        if key not in keys or row.get('decision') not in {'accept', 'revise'} or \
                not isinstance(row.get('reason'), str) or not row['reason'].strip():
            errors.append(f'new topic review {row!r} must name a proposal, accept|revise and a reason')
            continue
        judged.append(key)
    if sorted(map(str, judged)) != sorted(map(str, keys)):
        errors.append('the review must judge every new topic exactly once')
    cov = review.get('coverage')
    if not isinstance(cov, dict) or cov.get('decision') not in {'accept', 'revise'} or \
            not isinstance(cov.get('reason'), str) or not cov['reason'].strip():
        errors.append('coverage must be {decision: accept|revise, reason}')
    if errors:
        raise IngestAnswerError('Review rejected by validation:\n- ' + '\n- '.join(errors))
    return review


def _check_focused(packet: dict, state: dict, chunk: str, review: dict) -> None:
    """In a focused repair round (``[review] focused_review``) a card is revised only for an earlier finding that
    is not fixed or for a blocking problem (:data:`secondbrain.schemas.BLOCKING`); anything else goes to ``notes``."""
    if not jobs.policy_of(packet).focused_review or not _round(chunk):
        return
    errors = focused_problems(review)
    if errors:
        raise IngestAnswerError('Review rejected by validation:\n- ' + '\n- '.join(errors))


def focused_problems(review: dict) -> list[str]:
    """What a focused repair round's review may not do: send a card back for anything but an earlier finding that
    is not fixed or a blocking problem (:data:`secondbrain.schemas.BLOCKING`)."""
    errors = []
    for row in review.get('cards') or []:
        if row.get('decision') != 'revise':
            continue
        unfixed = any(isinstance(f, dict) and f.get('fixed') is False for f in row.get('fixed') or [])
        if not unfixed and row.get('blocking') not in schemas.BLOCKING:
            errors.append(f'{row["slug"]}: in a repair round a card is revised only for an earlier finding that is '
                          f'not fixed (fixed: [{{"finding": ..., "fixed": false}}]) or a blocking problem (blocking: '
                          f'{", ".join(schemas.BLOCKING)}); put other problems in notes')
    return errors


def _made_amends(root: Path, packet: dict, state: dict, chunk: str, candidate: dict, review: dict,
                 existing_domains: dict) -> dict:
    """The review with each accepted card's mechanical fixes checked (:mod:`amend`); a card whose fixes are not legal
    is revised instead. A malformed ``amend``, one on a revised card, or any when the job takes none, is refused."""
    rows = review.get('cards') or []
    given = [r for r in rows if r.get('amend')]
    if not given:
        return review
    errors = [p for r in given for p in amends.shape_problems(r['slug'], r['amend'])]
    if not jobs.policy_of(packet).amend:
        errors.append('this job takes no mechanical fixes ([review] amend is off): revise the card instead')
    errors += [f'{r["slug"]}: a mechanical fix comes only with accept' for r in given if r.get('decision') != 'accept']
    if errors:
        raise IngestAnswerError('Review rejected by validation:\n- ' + '\n- '.join(errors))
    cards = {c['slug']: c for c in candidate.get('cards') or []}
    others = {c['slug']: c.get('domain') for c in _other_cards(root, state, packet, _base(chunk))}
    domains = {**existing_domains, **{s: c.get('domain') for s, c in cards.items()}, **others,
               **{s: existing_domains.get(i) for s, i in _written_slugs(packet).items()}}

    def legal(domain, pillar, topic):
        return domain in packet['catalog']['domains'] and (pillar, topic) in _legal_pairs(packet, domain)
    out = []
    for row in rows:
        if not row.get('amend'):
            out.append(row)
            continue
        card = cards[row['slug']]
        why = amends.problems(card, row['amend'], closed=lambda kind, domain, target: _closed_relation(
            packet['catalog'], kind, domain, target), legal=legal, domain_of=domains.get,
            keep_link=(row.get('with') or [None])[0] if row.get('overlap') == 'extends' else None)
        out.append(amends.refused(row, why) if why else row)
    return {**review, 'cards': out}


@dataclass(frozen=True)
class AnswerRole:
    """A role that asks a model for an answer in an ingest job: the slots it answers in, its answer's schema, and
    how its answers the program refuses (``INVALID_OUTPUT``) are counted (:func:`record_refusal`).

    ``count`` and ``error`` are the slot fields of the job state that keep the count and the last refusal (a
    producer and a reviewer may answer in the same slot). Once the count is past ``max_invalid_retries``,
    ``used_up`` says what follows: ``pending`` (what the answer was for waits for the owner, with the reason) or
    ``without`` (the job goes on without it; ``skip`` says how, and is recorded on the slot). ``retries`` is the
    budget of one slot: ``max_invalid_retries``, except where the role asks again in a slot of its own. ``repairs``
    is the role's budget of content repair rounds (a slot sent back with a reason and answered again in a slot of
    its own, ``c01.r1``; for the merge check, a round after a dispute, ``xc01.r1``), read by :func:`_rounds_left`.
    ``cards`` says where the answer of a role that writes cards holds them (``cards``, a list; ``card``, one), and
    ``repair_rules`` which repairs of :mod:`.answerfix` the program makes on them (``None``: every rule of the table),
    in one place for every such role, before they are checked (:func:`_repaired`).
    """
    name: str
    role: str
    owns: Callable[[str], bool]
    schema: Callable[[str], dict]
    count: str = 'invalid_outputs'
    error: str = 'last_validation_error'
    used_up: str = 'pending'
    skip: str = ''
    rounds: bool = False  # a slot with repair rounds (``c01.r1``): its chunk shows the latest refusal too
    retries: Callable[[object], int] = lambda rules: rules.max_invalid_retries
    repairs: Callable[[object], int] = lambda rules: 0
    cards: str = ''
    repair_rules: tuple | None = ()


def _repair_rounds(rules) -> int:
    """The content repair rounds of a role whose slot is sent back with a reason: ``max_repair_rounds``."""
    return rules.max_repair_rounds


#: Every role that asks a model in an ingest job; a slot and a role name exactly one. An answer's schema is read
#: from here (:func:`_answer_schema`), so a new kind of slot cannot be asked for without its count and budget.
ANSWER_ROLES = (
    AnswerRole('chunk producer', 'producer', lambda s: not _aux_slot(s), lambda s: schemas.CARDS, rounds=True,
               repairs=_repair_rounds, cards='cards', repair_rules=None),
    AnswerRole('chunk reviewer', 'reviewer', lambda s: not _aux_slot(s), lambda s: schemas.REVIEW_CARDS,
               count='invalid_reviews', error='last_review_error', repairs=_repair_rounds),
    AnswerRole('merge check', 'reviewer', lambda s: source_check.is_slot(s), lambda s: source_check.SCHEMA,
               used_up='without', skip='the cards were written as the chunk reviews left them',
               repairs=_repair_rounds),
    AnswerRole('dispute re-judge', 'reviewer', lambda s: _is_rejudge(s), lambda s: REJUDGE_SCHEMA,
               count='invalid_reviews', error='last_review_error'),
    # the chunk reviewer asked once about its minor notes that may name a blocking problem; no repair round
    AnswerRole('note check', 'reviewer', lambda s: notecheck.is_slot(s), lambda s: notecheck.SCHEMA,
               count='invalid_reviews', error='last_review_error', used_up='without',
               skip='its cards stay as the review accepted them'),
    # a split card's check does not refuse two links to one card and asks for a typed link to the card it narrows,
    # which a relation repair could drop: its text and quotes are repaired, its relations left to the check
    AnswerRole('split producer', 'producer', lambda s: split.is_slot(_base(s)), lambda s: split.CARD_SCHEMA,
               rounds=True, repairs=_repair_rounds, cards='card', repair_rules=('title', 'card_id', 'quote')),
    AnswerRole('split reviewer', 'reviewer', lambda s: split.is_slot(_base(s)), lambda s: split.REVIEW_SCHEMA,
               count='invalid_reviews', error='last_review_error', repairs=_repair_rounds),
    AnswerRole('context note', 'producer', lambda s: notes.is_slot(s),
               lambda s: notes.CONCEPTS_SCHEMA if s == notes.CONCEPTS_SLOT else notes.SUMMARY_SCHEMA,
               used_up='without', skip="the chunk has the program's note alone"),
    # a diagram answer that fails the checks is asked again once, in its rewrite slot (dg01.r1), with the errors
    AnswerRole('diagram', 'producer', lambda s: card_diagrams.is_slot(s), lambda s: card_diagrams.answer_schema(),
               used_up='without', skip='its cards are written without a diagram', retries=lambda rules: 0),
)


def answer_role(slot: str, role: str) -> AnswerRole:
    """The role that answers as ``role`` (producer or reviewer) in ``slot``."""
    found = [r for r in ANSWER_ROLES if r.role == role and r.owns(slot)]
    if len(found) != 1:
        raise IngestError(f'no {role} answers in slot {slot!r}')
    return found[0]


def _rounds_left(slot: str, role: str, rules, used: int) -> int:
    """The content repair rounds the slot of ``role`` has left after ``used`` (the one test,
    :func:`secondbrain.budget.rounds_left`, of the role's own budget, ``AnswerRole.repairs``)."""
    return budget.rounds_left(used, answer_role(slot, role).repairs(rules))


def _refusals(info: dict, spec: AnswerRole) -> int:
    """How many answers of ``spec``'s role in one slot failed the program's checks."""
    return int((info or {}).get(spec.count, 0))


def _retries_left(info: dict, rules, spec: AnswerRole | None = None) -> bool:
    """Whether a slot whose answers failed the program's checks may be asked again (``max_invalid_retries``); the
    chunk producer's count unless ``spec`` names another role."""
    spec = spec or ANSWER_ROLES[0]
    return not budget.used_up(_refusals(info, spec), spec.retries(rules))


def _count_refusal(state: dict, packet: dict, slot: str, spec: AnswerRole, message: str) -> int:
    """Count, in ``state``, one answer of ``spec``'s role in ``slot`` that failed the program's checks; returns the
    count so far. Past the role's budget the slot records why it was given up (``given_up``; and ``skipped`` for a
    role the job goes on without)."""
    chunks = state.setdefault('chunks', {})
    fresh = {'candidate': None, 'review': None, 'status': 'prepared', 'round': 0}
    info = chunks.setdefault(slot, dict(fresh))
    failed = budget.count(info, message, field=spec.count, error=spec.error)
    if spec.rounds and slot != _base(slot):  # the chunk shows its latest error too
        chunks.setdefault(_base(slot), dict(fresh))[spec.error] = message[:8000]
    if budget.used_up(failed, spec.retries(jobs.policy_of(packet))):
        info['given_up'] = f"the {spec.name}'s answer failed validation {failed} times: {message[:500]}"
        if spec.used_up == 'without':
            info['skipped'] = f"the {spec.name}'s answer failed validation {failed} times; {spec.skip}"
    return failed


def record_refusal(packet: dict, *, role: str, root, chunk, state: dict, run: dict, message: str,
                   keep=None) -> bool:
    """jobs.submit hook: an answer the validator refused, in either mode, counted against its slot's budget
    (:func:`_count_refusal`); True when the slot is now given up. ``keep``: the cards of a chunk producer's answer
    that passed, and the ones to ask for again (:func:`_producer_answer`), kept on the slot for the next ask."""
    try:
        spec = answer_role(chunk, role)
    except IngestError:
        return False
    if keep is not None and role == 'producer' and not _aux_slot(chunk):
        state.setdefault('chunks', {}).setdefault(chunk, {'candidate': None, 'review': None, 'status': 'prepared',
                                                          'round': 0})['draft'] = keep
    return _count_refusal(state, packet, chunk, spec, message) > spec.retries(jobs.policy_of(packet))


def _failed_answer(root: Path, job_id: str, slot: str, role: str, failure: ExecutorFailure) -> None:
    """An executor failure of a model call: an answer that failed the program's checks was counted at the gate
    (:func:`secondbrain.jobs.receive`, by :func:`record_refusal`), and the planning reads the count; any other
    failure is raised again."""
    if failure.code != INVALID_OUTPUT:
        raise failure


def _answer_schema(chunk: str, role: str) -> dict:
    """The output schema the slot ``chunk`` asked ``role`` for."""
    return answer_role(chunk, role).schema(chunk)


def validate_submission(packet: dict, payload: dict, *, role: str, candidate=None,
                        root=None, chunk=None, state=None) -> dict:
    """jobs.submit hook: S4 for producers, shape checks for reviewers. A producer's answer it refuses carries what
    it found (``rejected``: the answer before and after the repairs, the repairs, each error line with its path and
    kind, a quote's own text and where it comes closest, the coverage rows as given), for the entry point to keep
    with the reply (:func:`secondbrain.jobs._keep_rejected`)."""
    seen: dict = {'given': copy.deepcopy(payload)} if role == 'producer' else {}
    try:
        return _validate_submission(packet, payload, role=role, candidate=candidate, root=root, chunk=chunk,
                                    state=state, seen=seen)
    except schemas.AnswerError as exc:
        if role == 'producer' and chunk is not None:
            exc.rejected = _rejected_report(seen, chunk, str(exc))
        raise


def _rejected_report(seen: dict, slot: str, message: str) -> dict:
    """What a refused producer answer is kept with, from the check (:func:`validate_submission`)."""
    fixed, text = seen.get('fixed', seen.get('given')), seen.get('text')
    given = seen.get('given')
    rows = given.get('coverage') if isinstance(given, dict) else None
    return {'base_chunk': _base(slot), 'round': _round(slot),
            'chunk_sha256': jobs.digest(text.encode('utf-8')) if isinstance(text, str) else None,
            'payload_before_repair': given, 'payload_after_repair': fixed, 'repairs': seen.get('records') or [],
            'errors': answerfix.error_rows(message, fixed, text),
            'coverage': [{k: r.get(k) for k in ('disposition', 'cards', 'with', 'source_quote')}
                         for r in rows if isinstance(r, dict)] if isinstance(rows, list) else rows}


def _validate_submission(packet: dict, payload: dict, *, role: str, candidate=None, root=None, chunk=None,
                         state=None, seen: dict) -> dict:
    if chunk is None:
        raise IngestError('ingest runs are always bound to a chunk slot')
    draft = _draft(state, chunk) if role == 'producer' else None
    wrong = schemas.type_problems(payload, schemas.CARD_FIX if draft else _answer_schema(chunk, role))
    if wrong:  # before anything puts a value in a set or sorts it: a model may send an object for a name
        raise IngestAnswerError(('Review' if role == 'reviewer' else 'Candidate') + ' rejected by validation:\n- '
                          + '\n- '.join(wrong))
    spec = answer_role(chunk, role)
    records: list = []
    if spec.cards:  # a role that writes cards: the program repairs them here, first, for every such role
        payload, records = _repaired(packet, state, root, chunk, spec, payload, draft)
        seen.update(fixed=payload, records=records, text=_chunk_text(packet, _split_item(state, chunk)['chunk']
                                                                      if split.is_slot(chunk) else _base(chunk)))
    if card_diagrams.is_slot(chunk):
        return card_diagrams.validate_answer(payload)
    if source_check.is_slot(chunk):
        closed = lambda kind, domain, target: _closed_relation(packet['catalog'], kind, domain, target)  # noqa: E731
        try:
            return source_check.validate_answer(candidate, payload, closed=closed)
        except ValueError:
            # the last answer the check may give: the patches that pass go in, the others are held (D-20)
            saved = source_check.salvage(candidate, payload, closed) if _last_merge_answer(
                root, state, packet, chunk) else None
            if saved is None:
                raise
            return saved
    if _is_rejudge(chunk):
        return _validate_rejudge(candidate, payload, _vault_ids_titles(root)[0])
    if notecheck.is_slot(chunk):
        return notecheck.validate(candidate, payload, jobs.artifact(root, state, 'review', notecheck.reviewed(chunk)))
    if notes.is_slot(chunk):
        if chunk == notes.CONCEPTS_SLOT:
            return notes.validate_concepts(payload, [c['chunk_id'] for c in effective_chunks(packet, state)])
        return notes.validate_summary(payload)
    if split.is_slot(chunk):
        item = _split_item(state, chunk)
        if role == 'reviewer':
            return split.validate_review(payload, card=(candidate or {}).get('card'),
                                         existing=len(item['existing_propositions']))
        taken = {c['slug'] for c in _other_cards(root, state, packet, None)} | set(_vault_ids_titles(root)[0])
        checked = split.validate_card(item, payload, text=_chunk_text(packet, item['chunk']), taken=taken,
                                      catalog_types=set(_relation_types(packet['catalog'])),
                                      siblings=_split_siblings(root, state, item))
        return {**checked, **({'program_repairs': records} if records else {})}
    existing_ids, existing_titles, existing_domains = _vault_ids_titles(root)
    if role == 'reviewer':
        review = validate_review(candidate, payload, existing_ids, packet['catalog']['domains'])
        _check_focused(packet, state, chunk, review)
        review = _made_amends(root, packet, state, chunk, candidate, review, existing_domains)
        # an accepted card with a note of a blocking kind is sent back, the note its reason (D-18 3); it is stored
        # so, with what the reviewer answered under `turned`, and every later step reads a revise
        # and one that answers yes to a question of the program on it, as unreadable (pipelines.cardlint)
        asked = cardlint.of_cards(candidate.get('cards'))
        return {**review, 'cards': [cardlint.turned(review_notes.turned(r), asked.get(r.get('slug'), []))
                                    for r in review.get('cards') or []]}
    base, rnd = _base(chunk), _round(chunk)
    kept = _accepted_so_far(root, state, base, rnd) if rnd else {}
    others = _other_cards(root, state, packet, base)
    domains = {**existing_domains, **{s: existing_domains.get(i) for s, i in _written_slugs(packet).items()},
               **{c['slug']: c.get('domain') for c in others}, **{s: c.get('domain') for s, c in kept.items()}}

    def check(answer, also=frozenset()):
        return validate_candidate(packet, answer, slot=chunk, other_cards=others, kept_slugs=set(kept) | set(also),
                                  existing_ids=existing_ids, existing_titles=existing_titles,
                                  known_topics=_known_topics(root, state, chunk, answer, kept), target_domains=domains)
    return _producer_answer(packet, state, chunk, payload, records, check)


def _repaired(packet: dict, state: dict, root, slot: str, spec: AnswerRole, payload: dict, draft) -> tuple:
    """``(answer, records)``: the cards of a card-writing role's answer repaired by its rules
    (``AnswerRole.repair_rules``, :func:`answerfix.repair`), the one place this is done. A chunk producer's answer is
    first put back with what its last refused answer kept (``draft``), so the repairs see the whole answer; the
    records then follow the draft's. A split card is repaired as an answer of one card, against its chunk. An answer
    that is not an object is given back as it is, for the role's check to refuse by name."""
    if not isinstance(payload, dict):
        return payload, []
    existing = set(_vault_ids_titles(root)[0])
    if spec.cards == 'cards':
        base, rnd = _base(slot), _round(slot)
        kept = _accepted_so_far(root, state, base, rnd) if rnd else {}
        text, targets = _chunk_text(packet, base), existing | {c['slug'] for c in _other_cards(root, state, packet, base)} \
            | set(kept)
        whole = answerfix.merge(draft['payload'], payload) if draft else payload
    else:
        item = _split_item(state, slot)
        text = _chunk_text(packet, item['chunk'])
        targets = existing | {c['slug'] for c in _other_cards(root, state, packet, None)} | {item['with']}
        if not isinstance(payload.get('card'), dict):
            return payload, []
        whole = {'cards': [payload['card']]}
    written = _written_slugs(packet)
    context = answerfix.Context(text=text, catalog=packet['catalog'], prefix=packet['settings']['id_prefix'],
                                title_of=_existing_titles(root), targets=targets | set(written), written=written)
    fixed, records = answerfix.repair(whole, context, rules=spec.repair_rules)
    if spec.cards == 'card':
        return {**payload, 'card': fixed['cards'][0]}, records
    return fixed, list((draft or {}).get('repairs') or []) + records


def _draft(state: dict | None, slot: str):
    """What a chunk producer's last refused answer left to keep (:func:`_producer_answer`), while the slot has no
    candidate: the answer, the cards that failed the checks with their lines, the repairs made."""
    info = ((state or {}).get('chunks') or {}).get(slot) or {}
    return info.get('draft') if not info.get('candidate') and not _aux_slot(slot) else None


def _existing_titles(root) -> dict:
    """``{id: title}`` of the vault's cards: a card named by its id in an answer is named by this title."""
    return {cid: c.title for cid, c in read_snapshot(root).cards.items() if c.title}


def _producer_answer(packet: dict, state: dict, slot: str, fixed: dict, records: list, check) -> dict:
    """The one way a chunk producer's answer is taken (decision D-34, API and host mode alike): put back with what
    the last refused answer kept and repaired (``fixed``, ``records``: :func:`_repaired`), then checked. What passes
    is the candidate, with the repairs recorded (``program_repairs``). When only some cards fail, the answer is
    refused for them alone (``keep`` carries the rest to the next ask, which asks for those cards); the last answer
    the slot may give keeps the cards that pass, and the others wait for the owner (``held_cards``). A problem of the
    whole chunk refuses the answer as before."""
    try:
        check(fixed)
        return {**fixed, **({'program_repairs': records} if records else {})}
    except IngestError as exc:
        lines = answerfix.problem_lines(str(exc))
        by_card, block = answerfix.split_problems(lines, fixed)
        if block or not by_card:
            raise
        spec = answer_role(slot, 'producer')
        if _refusals(((state or {}).get('chunks') or {}).get(slot) or {}, spec) >= spec.retries(jobs.policy_of(packet)):
            # the last answer the slot may give: the cards that pass go on, the others wait for the owner
            kept, dropped = answerfix.held_back(fixed, set(by_card))
            try:
                check(kept, set(by_card))
            except IngestError:
                raise exc from None
            given = {c.get('slug'): c for c in fixed.get('cards') or [] if isinstance(c, dict)}
            # each card held back keeps what it says (as repaired), so the owner can read it without a new run
            return {**kept, 'program_repairs': records + dropped,
                    'held_cards': [{'slug': s, 'title': (given.get(s) or {}).get('title'), 'problems': by_card[s],
                                    **({'card': given[s]} if s in given else {})} for s in sorted(by_card)]}
        refused = IngestAnswerError(f'{len(by_card)} card(s) failed the checks; the others passed and are kept. '
                              f'Answer again with these cards only, fixed: {", ".join(sorted(by_card))}\n'
                              + '\n'.join(f'- {line}' for line in lines))
        refused.keep = {'payload': fixed, 'held': by_card, 'repairs': records}
        raise refused from exc


def _merge_check_failures(root: Path, state: dict, slot: str) -> int:
    """The answers of merge-check slot ``slot`` that failed the checks (the one count of every role, both modes:
    :func:`_count_refusal`), and its runs given up without an answer (abandoned).

    A run that ended without an answer for another reason (a transport or gateway error, a timeout, a key that
    could not be read) is not counted: it is no answer (decision D-23)."""
    info = (state.get('chunks') or {}).get(slot) or {}
    return _refusals(info, answer_role(slot, 'reviewer'))  # a run given up without an answer is counted there too


def _last_merge_answer(root: Path, state: dict, packet: dict, slot: str) -> bool:
    """Whether an answer to ``slot`` now is the last one the merge check may give (``max_invalid_retries``)."""
    return _merge_check_failures(root, state, slot) >= jobs.policy_of(packet).max_invalid_retries


def _known_topics(root, state, slot: str, payload, kept: dict) -> set:
    """New topics proposed by other slots of this source that ``payload`` may use; they count toward the limit.

    A proposal of another chunk always counts. A proposal of an earlier round
    of this chunk counts only while a card is filed under it: a kept card the
    answer does not file again, or a card of the answer. So a repair round can
    replace a topic the review did not accept (rename it, or use a curated
    one) by filing the kept cards again under the replacement.
    """
    own = {name for name, _ in _slots(state, _base(slot))}
    cards = [c for c in payload.get('cards') or [] if isinstance(c, dict)] if isinstance(payload, dict) else []
    refiled = {c.get('slug') for c in cards}
    in_use = {(c.get('domain'), c.get('pillar'), c.get('topic')) for c in cards}
    in_use |= {(c['domain'], c['pillar'], c['topic']) for s, c in kept.items() if s not in refiled}
    out = set()
    for other, info in (state.get('chunks') or {}).items():
        if other == slot or not info.get('candidate') or _aux_slot(other):
            continue
        for t in jobs.artifact(root, state, 'candidate', other).get('new_topics', []):
            key = (t['domain'], t['pillar'], t['name'])
            if other not in own or key in in_use:
                out.add(key)
    return out


# --------------------------------------------------------------------------
# The decision record (secondbrain.decisions): every final value, written when its answer is submitted
# --------------------------------------------------------------------------
class _Facts:
    """What the job's slots show happened, for a reason to supersede an earlier value (``decisions.Facts``)."""

    def __init__(self, root: Path, state: dict):
        self.root, self.state = root, state

    def _objects(self, slot: str) -> set:
        cand = jobs.artifact(self.root, self.state, 'candidate', slot)
        out = {c.get('slug') for c in cand.get('cards') or [] if isinstance(c, dict)}
        return out | {_topic_object(_base(slot), t) for t in cand.get('new_topics') or [] if isinstance(t, dict)}

    def reappears(self, obj: str, earlier_slot: str, slot: str) -> bool:
        """``obj`` (a card, a chunk's coverage, a topic proposal) is judged again in a later round of its chunk."""
        earlier_slot = _judged_in(earlier_slot)
        if _base(earlier_slot) != _base(slot) or _round(slot) <= _round(earlier_slot):
            return False
        if split.is_slot(slot):  # a split slot rewrites one card, its plan's
            return obj == _split_item(self.state, slot)['slug']
        return obj == _base(slot) or obj in self._objects(slot)

    def refiled(self, obj: str, earlier_slot: str, slot: str) -> bool:
        """``obj`` is filed again after its review refused the new topic it was filed under."""
        if not self.reappears(obj, earlier_slot, slot):
            return False
        earlier_slot = _judged_in(earlier_slot)
        cards = {c.get('slug'): c for c in jobs.artifact(self.root, self.state, 'candidate', earlier_slot)
                 .get('cards') or [] if isinstance(c, dict)}
        card = cards.get(obj)
        refused = _unaccepted_topics(self.root, self.state, _base(slot), _round(slot))
        return card is not None and (card.get('domain'), card.get('pillar'), card.get('topic')) in refused

    def checked(self, obj: str, earlier_slot: str, slot: str) -> bool:
        """``slot`` is the check of the notes of the review ``earlier_slot`` gave, and it asked about card ``obj``."""
        if not notecheck.is_slot(slot) or notecheck.reviewed(slot) != earlier_slot:
            return False
        review = jobs.artifact(self.root, self.state, 'review', earlier_slot)
        return obj in {i['slug'] for i in notecheck.items(review)}


def _judged_in(slot: str) -> str:
    """The chunk slot a verdict recorded in ``slot`` belongs to: the reviewed slot for the check of its notes."""
    return notecheck.reviewed(slot) if notecheck.is_slot(slot) else slot


def _topic_object(base: str, topic: dict) -> str:
    """The decision record's name of a chunk's proposal of a new topic."""
    return json.dumps([base, topic.get('domain'), topic.get('pillar'), topic.get('name')], ensure_ascii=False)


def _again(entries: list, value: str, obj: str, slot: str, facts: _Facts, *, chain: bool = False) -> str | None:
    """Why ``slot`` judges ``obj`` again (``refile``, ``repair_round``, or ``note_check`` for the check of a review's
    notes), or None when nothing is there to supersede."""
    if chain:
        live = decisions.chain(entries, value, obj)
        earlier = live[-1] if live else None
    else:
        earlier = decisions.current(entries, value, obj)
    if earlier is None:
        return None
    if notecheck.is_slot(slot):
        return 'note_check'
    return 'refile' if facts.refiled(obj, earlier['slot'], slot) else 'repair_round'


def record_decisions(packet: dict, payload: dict, *, role: str, candidate, root, chunk, state, run,
                     entries: list) -> None:
    """jobs.submit hook: the final values of a reviewer's answer go to the decision record (``entries``).

    A chunk review gives each card's verdict, each accepted card's level
    against the vault cards, the chunk's coverage and each topic proposal's
    verdict, and the first steps of each accepted card's relations and
    taxonomy place (the producer's, then the reviewer's acceptance). The merge
    check gives each card's level against the source, each topic's, and its
    links. Producers give no final values; a producer's answer taken clears the cards its slot kept from a refused
    answer (they are in the candidate now).
    """
    if role == 'producer' and chunk is not None:
        ((state.get('chunks') or {}).get(chunk) or {}).pop('draft', None)
    if role != 'reviewer' or chunk is None or card_diagrams.is_slot(chunk):
        return
    if source_check.is_slot(chunk):
        source_check.record(candidate, payload, entries, slot=chunk, run=run['run_id'])
        return
    if notecheck.is_slot(chunk):  # a card the check of a minor note sent back: its verdict is a revise now
        reviewed, facts = notecheck.reviewed(chunk), _Facts(root, state)
        for row in payload.get('turned') or []:
            decisions.record(entries, 'verdict', row['slug'],
                             {'decision': row['decision'], 'chunk': _base(reviewed), 'reason': row['reason'],
                              'turned': row['turned']},
                             role='reviewer', slot=chunk, run=run['run_id'],
                             reason=_again(entries, 'verdict', row['slug'], chunk, facts), facts=facts)
        return
    if _is_rejudge(chunk):  # the chunk reviewer's level again, after the merge check disputed it
        decisions.record(entries, 'overlap.existing', payload['slug'],
                         {'overlap': payload['overlap'], 'with': list(payload.get('with') or []),
                          'reason': payload['reason']}, role='reviewer', slot=chunk, run=run['run_id'],
                         reason='dispute', facts=_Facts(root, state))
        return
    if split.is_slot(chunk):
        producer_run = ((state.get('chunks') or {}).get(chunk) or {}).get('candidate', {}).get('run_id')
        split.record(_split_item(state, chunk), candidate['card'], payload, entries, slot=chunk, run=run['run_id'],
                     producer_run=producer_run, facts=_Facts(root, state))
        return
    facts, base = _Facts(root, state), _base(chunk)
    producer_run = ((state.get('chunks') or {}).get(chunk) or {}).get('candidate', {}).get('run_id')
    rows = {r['slug']: r for r in payload.get('cards') or []}
    for card in candidate.get('cards') or []:
        slug, row = card['slug'], rows[card['slug']]
        decisions.record(entries, 'verdict', slug, {'decision': row['decision'], 'chunk': base, 'reason': row['reason'],
                                                    **({'amend': row['amend']} if row.get('amend') else {}),
                                                    **({'turned': row['turned']} if row.get('turned') else {})},
                         role='reviewer', slot=chunk, run=run['run_id'],
                         reason=_again(entries, 'verdict', slug, chunk, facts), facts=facts)
        if row['decision'] != 'accept':
            continue
        decisions.record(entries, 'overlap.existing', slug,
                         {'overlap': row['overlap'], 'with': list(row.get('with') or []), 'reason': row['reason']},
                         role='reviewer', slot=chunk, run=run['run_id'],
                         reason=_again(entries, 'overlap.existing', slug, chunk, facts), facts=facts)
        relations = [{k: v for k, v in r.items() if k in ('type', 'target', 'note', 'tier') and v}
                     for r in card.get('relations') or [] if isinstance(r, dict)]
        decisions.step(entries, 'relations', slug, 'create', role='producer', slot=chunk, run=producer_run,
                       data={'relations': relations}, facts=facts,
                       reason=_again(entries, 'relations', slug, chunk, facts, chain=True))
        fixes = row.get('amend') or []
        for item in fixes:  # the reviewer's mechanical fixes, after the producer's relations
            if item['op'] == 'add_relation':
                decisions.step(entries, 'relations', slug, 'add_relation', role='reviewer', slot=chunk,
                               run=run['run_id'], data={'relation': {k: item[k] for k in ('type', 'target', 'note',
                                                                                          'tier') if item.get(k)}})
            elif item['op'] in amends.RELATION_OPS:
                decisions.step(entries, 'relations', slug, item['op'], role='reviewer', slot=chunk, run=run['run_id'],
                               data={k: item[k] for k in ('type', 'target', 'note') if item.get(k)})
        place = {k: card.get(k) for k in ('domain', 'pillar', 'topic')}
        again = _again(entries, 'classification', slug, chunk, facts, chain=True)
        if packet.get('placement') and again is None:
            decisions.step(entries, 'classification', slug, 'constrain', role='owner', slot=chunk,
                           data=dict(packet['placement']))
        decisions.step(entries, 'classification', slug, 'propose', role='producer', slot=chunk, run=producer_run,
                       data=place, reason=again, facts=facts)
        if any(i['op'] == 'refile' for i in fixes):  # where a card is filed is the reviewer's decision
            fixed = amends.apply(card, fixes)
            decisions.step(entries, 'classification', slug, 'refile', role='reviewer', slot=chunk, run=run['run_id'],
                           data={k: fixed[k] for k in ('domain', 'pillar', 'topic')})
        else:
            decisions.step(entries, 'classification', slug, 'accept', role='reviewer', slot=chunk, run=run['run_id'],
                           data=place)
    coverage = payload.get('coverage') or {}
    decisions.record(entries, 'coverage', base, {'decision': coverage.get('decision'), 'reason': coverage.get('reason')},
                     role='reviewer', slot=chunk, run=run['run_id'],
                     reason=_again(entries, 'coverage', base, chunk, facts), facts=facts)
    judged = {(t.get('domain'), t.get('pillar'), t.get('name')): t for t in payload.get('new_topics') or []}
    for topic in candidate.get('new_topics') or []:
        key = _topic_object(base, topic)
        verdict = judged.get((topic.get('domain'), topic.get('pillar'), topic.get('name'))) or {}
        decisions.record(entries, 'topic.verdict', key, {'decision': verdict.get('decision'),
                                                         'reason': verdict.get('reason')},
                         role='reviewer', slot=chunk, run=run['run_id'],
                         reason=_again(entries, 'topic.verdict', key, chunk, facts), facts=facts)


def _question_of(row: dict) -> dict:
    """The question a reviewer found an accepted card answers (``_question``, shown to the merge check), when it
    wrote one."""
    q = row.get('question')
    return {'_question': q.strip()} if isinstance(q, str) and q.strip() else {}


def _routed(state: dict, settled: list[dict]) -> list[dict]:
    """The settled chunks with each accepted card sent where the chunk reviewer's level against the vault puts it.

    ``same``: not written, listed under ``duplicates`` (``by: reviewer``).
    ``extends``: ``repeats`` (the patch set), with the existing card it extends.
    ``distinct``: stays in ``accepted``, the merge check's decision set.
    """
    entries = state.get('decisions') or []
    # a disputed card whose judgement again kept failing the program's checks: it waits for the owner, out of the
    # merge check's patch set (:func:`_for_merge`), with the dispute and the last refusal
    given_up = {name.split('-', 1)[1]: info for name, info in (state.get('chunks') or {}).items()
                if _is_rejudge(name) and info.get('given_up') and not info.get('review')}
    out = []
    for o in settled:
        accepted, repeats, dups = {}, {}, list(o['duplicates'])
        for slug, card in o['accepted'].items():
            first = overlap.route(entries, slug)
            if first['outcome'] == 'drop':
                dups.append({'slug': slug, 'title': card.get('title'), 'with': first['with'],
                             'reason': first['reason'], 'chunk': o['chunk'], 'by': 'reviewer'})
            elif first['outcome'] == 'patch':
                repeats[slug] = {**card, '_with': first['with'][0], '_reason': first['reason']}
                if slug in given_up:
                    repeats[slug]['_given_up'] = (
                        f'the merge check could not add it to {first["with"][0]}: '
                        f'{given_up[slug].get("dispute") or "disputed"}; {given_up[slug]["given_up"]}')
            else:
                accepted[slug] = card
        out.append({**o, 'accepted': accepted, 'repeats': repeats, 'duplicates': dups})
    return out


def _for_merge(root: Path, packet: dict, routed: list[dict]) -> list[dict]:
    """``routed`` with ``repeats`` cut to the patch set the merge check composes patches for.

    A card that extends an existing card is in it only when the job merges
    (``merge_into_existing``) and that card exists and can take a patch
    (:func:`secondbrain.cards.patchable`); any other is written next to it,
    needs-review (:func:`_repeat_fates`).
    """
    if not _merging(packet):
        return [{**o, 'repeats': {}} for o in routed]
    if not any(o.get('repeats') for o in routed):
        return routed
    snapshot = read_snapshot(root)

    def takes(card):
        old = snapshot.cards.get(card['_with'])
        return old is not None and cardfmt.patchable(old.body) is None and not card.get('_given_up')
    return [{**o, 'repeats': {s: c for s, c in (o.get('repeats') or {}).items() if takes(c)}} for o in routed]


def _unaccepted_topics(root, state, base: str, upto_round: int) -> dict:
    """``{(domain, pillar, name): reason}`` of new topics not accepted by their latest review before ``upto_round``."""
    verdicts = {}
    for slot, info in _slots(state, base)[:upto_round]:
        if not (info.get('candidate') and info.get('review')):
            break
        review = _review(root, state, slot)
        for t in jobs.artifact(root, state, 'candidate', slot).get('new_topics', []):
            key = (t['domain'], t['pillar'], t['name'])
            row = next((r for r in review.get('new_topics') or []
                        if (r.get('domain'), r.get('pillar'), r.get('name')) == key), None)
            verdicts[key] = None if row and row.get('decision') == 'accept' else (row or {}).get('reason', 'not judged')
    return {k: v for k, v in verdicts.items() if v is not None}


# --------------------------------------------------------------------------
# S7 merge per chunk
# --------------------------------------------------------------------------
def _accepted_so_far(root, state, base: str, upto_round: int) -> dict:
    accepted = {}
    for rnd in range(upto_round):
        slot = _slot_name(base, rnd)
        info = (state.get('chunks') or {}).get(slot) or {}
        if not (info.get('candidate') and info.get('review')):
            break
        cand = jobs.artifact(root, state, 'candidate', slot)
        review = _review(root, state, slot)
        decisions = {r['slug']: r for r in review.get('cards', [])}
        for c in cand.get('cards', []):
            if decisions.get(c['slug'], {}).get('decision') == 'accept':
                accepted[c['slug']] = amends.apply(c, decisions[c['slug']].get('amend'))
            else:
                accepted.pop(c['slug'], None)
    return accepted


def _newest(base: str):
    """For the guard (:func:`jobs.reading`): the newest answer chunk ``base`` holds, as ``(slot, role)``, or None."""
    def newest(state: dict):
        for slot, info in reversed(_slots(state, base)):
            if info.get('candidate'):
                if info.get('review') and _note_check(state, slot).get('review'):
                    return notecheck.slot_for(slot), 'reviewer'
                return slot, 'reviewer' if info.get('review') else 'producer'
        return None
    return newest


def chunk_outcome(root: Path, state: dict, packet: dict, base: str) -> dict:
    """Merge rounds of one chunk; ``next`` names the step still needed, if any."""
    slots = _slots(state, base)
    info0 = (state.get('chunks') or {}).get(base) or {}
    rules = jobs.policy_of(packet)
    used = len(slots) - 1  # content repair rounds begun; format retries are counted per slot (_retries_left)
    out = {'chunk': base, 'settled': False, 'next': None, 'accepted': {}, 'pending': [], 'amended': [], 'notes': [],
           'turned': [],
           'duplicates': [], 'topics': {}, 'needs_taxonomy': [], 'skipped': [], 'conflicts': []}
    if not slots or not slots[0][1].get('candidate'):
        if not _retries_left(info0, rules):
            out.update(settled=True, pending=[{'scope': 'chunk', 'chunk': base, 'reason':
                       f'the producer output failed validation {_refusals(info0, ANSWER_ROLES[0])} times: '
                       + str(info0.get('last_validation_error') or '')[:2000]}])
            return out
        note = _note_due(state, packet, base)
        out['next'] = ('note', note) if note else ('produce', base)
        return out
    accepted: dict = {}
    held: set = set()
    topic_state: dict = {}
    open_cards: dict = {}
    open_coverage = None
    dups: dict = {}
    needs_tax, skipped = [], []
    conflicts: dict = {}
    notes: list = []
    turned: list = []  # accepted cards the program sent back for a blocking note (D-18 3)
    checked: list = []  # the reviewer's answers about its minor notes (notecheck)
    unreviewed = None
    merged_rounds = 0
    for slot, info in slots:
        if not info.get('candidate'):
            # a repair round whose output failed validation: ask again while its retries last
            if _retries_left(info, rules):
                out['next'] = ('repair', slot)
                return out
            break
        if not info.get('review'):
            if _retries_left(info, rules, answer_role(slot, 'reviewer')):
                out['next'] = ('review', slot)
                return out
            unreviewed = (slot, info)  # its review kept failing the checks: its cards stay pending
            break
        if _note_check_due(root, state, slot):  # a minor note that may name a blocking problem: asked about first
            out['next'] = ('notecheck', notecheck.slot_for(slot))
            return out
        merged_rounds += 1
        review = _with_check(root, state, slot, jobs.require_independent_review(root, state, slot))
        checked += _note_answers(root, state, slot)
        cand = jobs.artifact(root, state, 'candidate', slot)
        decisions = {r['slug']: r for r in review.get('cards', [])}
        # A rejected card this round does not repeat was split, merged or dropped by the repair;
        # this round's coverage verdict, on the whole chunk, says whether its knowledge is missing.
        open_cards = {}
        for c in cand.get('cards', []):
            d = decisions[c['slug']]
            accepted.pop(c['slug'], None)
            # the producer's flags are kept even when the reviewer does not repeat them: a conflict is only shown
            # to the owner, never acted on, and must not pass silently; the reviewer's word wins on the same pair
            for by, rows in (('producer', c.get('conflicts')), ('reviewer', d.get('conflicts'))):
                for row in rows or []:
                    conflicts[(c['slug'], row['with'])] = {'slug': c['slug'], 'title': c.get('title'),
                                                          'with': row['with'], 'detail': row['detail'],
                                                          'by': by, 'chunk': base}
            notes += [{'chunk': base, 'slot': slot, 'slug': c['slug'], 'category': n['category'], 'note': n['text']}
                      for n in d.get('notes') or []]
            if d.get('turned'):
                turned.append({'chunk': base, 'slot': slot, 'round': _round(slot), 'slug': c['slug'],
                               'category': d['turned']['note']['category'], 'note': d['turned']['note']['text'],
                               **({'check': d['turned']['check']} if d['turned'].get('check') else {})})
            if d['decision'] == 'accept':  # whether it is written is the outcome of its level (secondbrain.overlap)
                accepted[c['slug']] = {**amends.apply(c, d.get('amend')), '_slot': slot,
                                       **({'_amend': d['amend'], '_amended': amends.changes(c, d['amend'])}
                                          if d.get('amend') else {}),
                                       **_question_of(d)}
                held.discard(c['slug'])
                if _held_by(d) is not None:  # not written: its taxonomy place does not matter
                    held.add(c['slug'])
            else:
                open_cards[c['slug']] = {'slug': c['slug'], 'title': c.get('title'), 'reason': d['reason'],
                                         'chunk': base}
        for t in cand.get('new_topics', []):
            key = (t['domain'], t['pillar'], t['name'])
            verdict = next((r for r in review.get('new_topics', []) or []
                            if (r.get('domain'), r.get('pillar'), r.get('name')) == key), None)
            topic_state[key] = {**t, 'decision': verdict['decision'] if verdict else 'revise',
                                'review_reason': verdict['reason'] if verdict else 'not judged'}
        for row in cand.get('coverage', []):
            if row.get('disposition') == 'same':  # the producer's row; the reviewer's coverage accept confirms it
                dups.setdefault((row.get('source_quote'), tuple(row.get('with') or [])), {
                    'source_quote': row.get('source_quote'), 'with': list(row.get('with') or []),
                    'reason': row.get('reason'), 'chunk': base, 'by': 'producer'})
            elif row.get('disposition') == 'needs_taxonomy':
                needs_tax.append({'chunk': base, 'source_quote': row.get('source_quote'),
                                  'suggested_pillar': row.get('suggested_pillar'), 'reason': row.get('reason')})
            elif row.get('disposition') == 'out_of_scope':
                skipped.append({'chunk': base, 'source_quote': row.get('source_quote'), 'reason': row.get('reason')})
        cov = review.get('coverage') or {}
        open_coverage = None if cov.get('decision') == 'accept' else {'scope': 'coverage', 'chunk': base,
                                                                       'reason': cov.get('reason')}
    # a new topic the review did not accept matters while an accepted card is filed under it
    unaccepted = {k for k, t in topic_state.items() if t['decision'] != 'accept'}
    findings = bool(open_cards) or open_coverage is not None or \
        any((c['domain'], c['pillar'], c['topic']) in unaccepted for s, c in accepted.items() if s not in held)
    if findings and _rounds_left(base, 'producer', rules, used) and merged_rounds == len(slots):
        out['next'] = ('repair', _slot_name(base, len(slots)))
        return out
    pending = list(open_cards.values())
    # a card the slot's last answer left out, failing the program's checks, while its other cards went on, is
    # pending unless another round gave it again
    given = [jobs.artifact(root, state, 'candidate', s) for s, i in slots if i.get('candidate')]
    again = {c['slug'] for cand in given for c in cand.get('cards', [])}
    left = {h['slug']: h for cand in given for h in cand.get('held_cards') or [] if h['slug'] not in again}
    pending += [{'slug': slug, 'title': h.get('title'), 'chunk': base,
                 'reason': 'its card failed the program\'s checks in the last answer the producer could give: '
                           + '; '.join(h.get('problems') or []), **({'card': h['card']} if h.get('card') else {})}
                for slug, h in left.items()]
    if open_coverage:
        pending.append(open_coverage)
    if unreviewed:
        slot, info = unreviewed
        why = (f'the review of {slot} failed validation {info.get("invalid_reviews")} times: '
               + str(info.get('last_review_error') or '')[:500])
        for c in jobs.artifact(root, state, 'candidate', slot).get('cards', []):
            if c['slug'] not in open_cards and c['slug'] not in accepted:
                pending.append({'slug': c['slug'], 'title': c.get('title'), 'reason': why, 'chunk': base})
    refused: dict = {}
    for slug, card in list(accepted.items()):
        key = (card['domain'], card['pillar'], card['topic'])
        if key in topic_state and topic_state[key]['decision'] != 'accept' and slug not in held:
            accepted.pop(slug)
            pending.append({'slug': slug, 'title': card['title'], 'chunk': base,
                            'reason': f'its new topic {key[2]!r} was not accepted: '
                                      f'{topic_state[key]["review_reason"]}'})
            refused.setdefault(key, {'domain': key[0], 'pillar': key[1], 'name': key[2], 'chunk': base,
                                     'reason': topic_state[key]['review_reason'], 'cards': 0})['cards'] += 1
    out['refused_topics'] = list(refused.values())  # for the owner's list (needs_taxonomy), with its cards
    out['amended'] = [{'slug': s, 'chunk': base, 'slot': c['_slot'], **row} for s, c in accepted.items()
                      for row in c.get('_amended') or []]
    out['notes'] = notes
    out['turned'] = turned
    out['notes_checked'] = checked
    out.update(settled=True, accepted=accepted, pending=pending, duplicates=list(dups.values()),
               topics={k: v for k, v in topic_state.items() if v['decision'] == 'accept'},
               needs_taxonomy=needs_tax, skipped=skipped, conflicts=list(conflicts.values()))
    return out


# --------------------------------------------------------------------------
# Driving the chain
# --------------------------------------------------------------------------
def _prompt(name: str, state: dict) -> str:
    from ..runner import prompt
    return prompt('common_cards.md').replace('{job_id}', state['job_id']) + '\n' + prompt(name)


def _step(root: Path, job_id: str, action: tuple, producer, reviewer) -> None:
    from ..runner import call_executor
    kind, slot = action
    state = jobs.load(root, job_id)
    packet = jobs.read_input(root, job_id)
    if kind == 'note':
        _note_step(root, job_id, state, packet, slot, producer)
        return
    if kind == 'notecheck':
        _note_check_step(root, job_id, state, packet, slot, reviewer)
        return
    if kind == 'review':
        rules = jobs.policy_of(packet)
        text = review_prompt(state['job_id'], amend=rules.amend, focused=bool(rules.focused_review and _round(slot)),
                             retry=bool(packet.get('retry')))
        feedback = ((state.get('chunks') or {}).get(slot) or {}).get('last_review_error')
        if feedback:
            text += '\n## Your previous answer was rejected by the validator\n\n' + feedback + '\n'
        try:
            with jobs.reading('review_packet', (slot, 'producer')):
                shown = review_packet(root, state, packet, slot)
        except retrieval.TooLarge as exc:
            # the cards the producer named would crowd out the chunk: produce its halves instead
            if slot == _base(slot) and len(_chunk_text(packet, slot)) >= 2 * MIN_SPLIT_CHARS:
                _record_split(root, job_id, slot, str(exc))
                return
            raise
        try:
            call_executor(root, job_id, reviewer, role='reviewer', step='review', prompt_text=text,
                          packet=shown, output_schema=schemas.REVIEW_CARDS, chunk=slot)
        except ExecutorFailure as failure:
            _failed_answer(root, job_id, slot, 'reviewer', failure)
        return
    text = _prompt('produce_cards.md', state)
    if packet.get('retry'):
        text += '\n' + _prompt_only('retry_cards.md')
    if kind == 'repair':
        text += '\n' + _prompt_only('repair_cards.md')
    base = _base(slot)
    info = (state.get('chunks') or {}).get(slot) or {}
    feedback = info.get('last_validation_error') if not info.get('candidate') else None
    with jobs.reading('produce_packet', _newest(base)):  # a repair round shows the last round's answers
        shown, schema, draft = producer_packet(root, state, packet, slot), schemas.CARDS, _draft(state, slot)
    if feedback:
        text += '\n## Your previous answer was rejected by the validator\n\n' + feedback + '\n'
    if draft:  # some cards of the last answer failed: ask for those, as they were given, with their lines
        text += '\n' + _prompt_only('fix_cards.md')
        shown, schema = {**shown, 'fix': answerfix.fix_packet(draft)}, schemas.CARD_FIX
    try:
        call_executor(root, job_id, producer, role='producer', step='repair' if kind == 'repair' else 'produce',
                      prompt_text=text, packet=shown, output_schema=schema, chunk=slot)
    except ExecutorFailure as failure:
        if failure.code == CHUNK_TOO_LARGE and slot == base and not info.get('candidate') \
                and len(_chunk_text(packet, base)) >= 2 * MIN_SPLIT_CHARS:
            _record_split(root, job_id, base, failure.error.get('message', ''))
            return
        _failed_answer(root, job_id, slot, 'producer', failure)


def review_prompt(job_id: str, *, amend: bool, focused: bool, retry: bool) -> str:
    """The instructions of a chunk's review: the review prompt, with the part on mechanical fixes, on a focused
    repair round and on a retry when they apply (the one assembly; ``tools/review_replay.py`` uses it too)."""
    text = _prompt('review_cards.md', {'job_id': job_id})
    for wanted, part in ((amend, 'review_amend.md'), (focused, 'review_focused.md'), (retry, 'retry_review_cards.md')):
        if wanted:
            text += '\n' + _prompt_only(part)
    return text


def split_review_prompt(job_id: str) -> str:
    """The instructions of a split card's review."""
    return _prompt('review_split.md', {'job_id': job_id})


def merge_check_prompt(job_id: str) -> str:
    """The instructions of the merge check."""
    return _prompt_only('source_check.md').replace('{job_id}', job_id)


def _prompt_only(name: str) -> str:
    from ..runner import prompt
    return prompt(name)


def _record_split(root: Path, job_id: str, base: str, message: str) -> None:
    from ..store import vault_lock, write_json
    with vault_lock(root):
        state = jobs.load(root, job_id)
        info = state.setdefault('chunks', {}).setdefault(
            base, {'candidate': None, 'review': None, 'status': 'prepared', 'round': 0})
        info.update(split=True, status='split', split_error=message[:2000])
        state['updated_at'] = jobs.now()
        write_json(root, jobs.location(job_id), state)


def outcomes(root: Path, job_id: str) -> list[dict]:
    with _frozen_run(root, jobs.read_input(root, job_id)):
        return _outcomes(root, job_id)


def _outcomes(root: Path, job_id: str) -> list[dict]:
    state = jobs.load(root, job_id)
    packet = jobs.read_input(root, job_id)
    return [chunk_outcome(root, state, packet, c['chunk_id']) for c in effective_chunks(packet, state)]


def _earlier_version(job_id: str, packet: dict) -> str:
    """Why this version does not run a job an earlier one froze, and the way out."""
    why = (f'{job_id} was frozen by {packet.get("method_version")}, which judged repeats on another scale; this '
           f'version ({METHOD}) cannot finish it. ')
    parent = (packet.get('retry') or {}).get('of')
    if parent:  # a retry child: the parent's records still hold, a new child is made by this version
        return why + (f'Reset it (sb job reset {job_id} --reason "made by an earlier version"), then retry its '
                      f'parent again (sb ingest --retry {parent})')
    source = (packet.get('source') or {}).get('inbox_path') or (packet.get('source') or {}).get('filename')
    return why + (f'Reset it (sb job reset {job_id} --reason "made by an earlier version"), then ingest the '
                  f'source again (sb ingest {source})')


def advance(root: Path, job_id: str, *, producer, reviewer, apply: bool = True) -> dict:
    """Run every step that can run now; returns the job state plus hand-offs.

    Every step reads the settings the job froze (:func:`_frozen_run`), never the configuration. An error while it
    reads the answers it took goes to the one guard (:func:`secondbrain.jobs.guarded`).
    """
    def once():
        with _frozen_run(root, jobs.read_input(root, job_id)):
            return _advance(root, job_id, producer=producer, reviewer=reviewer, apply=apply)
    return jobs.guarded(root, job_id, once)


def _advance(root: Path, job_id: str, *, producer, reviewer, apply: bool = True) -> dict:
    from ..runner import AwaitingSubmission
    with job_lock(root, job_id):
        state = jobs.load(root, job_id)
        if state.get('commit_intent'):
            if not apply:
                return state
            with jobs.reading('resume'):  # a write under way: no one answer to blame
                return jobs.resume_transaction(root, job_id) or jobs.load(root, job_id)
        if state['status'] in {'applied', 'partial', 'published', 'rolled_back', 'failed'}:
            return state
        packet = jobs.read_input(root, job_id)
        if packet.get('method_version') != METHOD:
            raise IngestError(_earlier_version(job_id, packet))
        handoffs, visited = [], set()
        while True:
            state = jobs.load(root, job_id)
            todo = [c['chunk_id'] for c in effective_chunks(packet, state) if c['chunk_id'] not in visited]
            if not todo:
                break
            base = todo[0]
            with jobs.reading('outcome', _newest(base)):
                out = chunk_outcome(root, state, packet, base)
            if out['settled'] or out['next'][1] in {h['chunk'] for h in handoffs}:  # one note serves many chunks
                visited.add(base)
                continue
            try:
                _step(root, job_id, out['next'], producer, reviewer)
            except AwaitingSubmission as waiting:
                handoffs.append({**waiting.handoff, 'chunk': out['next'][1], 'step': out['next'][0]})
                visited.add(base)
        state = jobs.load(root, job_id)
        settled = []
        for c in effective_chunks(packet, state):
            with jobs.reading('outcome', _newest(c['chunk_id'])):
                settled.append(chunk_outcome(root, state, packet, c['chunk_id']))
        if handoffs or not all(o['settled'] for o in settled):
            state = jobs.update(root, job_id, status='awaiting_submission' if handoffs else 'producing')
            return {**state, 'handoffs': handoffs}
        # the stages below read the answers of many slots at once: an error there parks the job
        with jobs.reading('merge_check'):
            handoffs = _source_check_stage(root, job_id, packet, settled, reviewer)
        if not handoffs:
            with jobs.reading('split'):
                handoffs = _split_stage(root, job_id, packet, settled, producer, reviewer)
        if handoffs:
            state = jobs.update(root, job_id, status='awaiting_submission')
            return {**state, 'handoffs': handoffs}
        with jobs.reading('checked'):
            checked, _ = _checked(root, jobs.load(root, job_id), packet, settled)
        if not apply:
            state = jobs.update(root, job_id, status='reviewed', proposal=_summary(checked))
            return state
        with jobs.reading('diagrams'):
            _, handoffs = _diagram_stage(root, job_id, packet, checked, producer)
        if handoffs:
            state = jobs.update(root, job_id, status='awaiting_submission')
            return {**state, 'handoffs': handoffs}
        with jobs.reading('apply'):
            return apply_job(root, job_id, settled)


# --------------------------------------------------------------------------
# Source check (after every chunk settled, before diagrams; see source_check)
# --------------------------------------------------------------------------
def _merge_round(state: dict) -> int:
    """The number of the merge check's latest round that has a slot (-1 before the first)."""
    chunks = state.get('chunks') or {}
    n = -1
    while source_check.round_slot(n + 1) in chunks:
        n += 1
    return n


def _merge_due(state: dict, routed: list[dict]) -> bool:
    """Whether the merge check runs (or ran) for the cards of ``routed``.

    It runs when :func:`source_check.needed` says so. Once a round has answered, it stays due while the source has
    cards to judge, even when a dispute took the cards that made it needed out of the patch set (a card judged
    again ``same``, ``distinct`` or given up): that round's answer is in the decision record (two cards of the source
    merged, say), and only a later round replaces it, so the write and the record never differ.
    """
    return source_check.needed(routed) or (_merge_round(state) >= 0 and any(o['accepted'] for o in routed))


def _rejudge_slot(number: int, slug: str) -> str:
    """The slot where the chunk reviewer judges ``slug`` again after round ``number - 1`` of the merge check disputed it."""
    return f'xr{number}-{slug}'


def _is_rejudge(name) -> bool:
    return isinstance(name, str) and re.fullmatch(r'xr[1-9]\d*-[a-z0-9][a-z0-9-]*', name) is not None


#: The chunk reviewer's answer when it judges a disputed card again.
REJUDGE_SCHEMA = {'type': 'object', 'required': ['slug', 'overlap', 'with', 'reason'],
                  'properties': {'slug': {'type': 'string'}, 'overlap': {'type': 'string', 'enum': list(overlap.LEVELS)},
                                 'with': {'type': 'array', 'items': {'type': 'string'}}, 'reason': {'type': 'string'}}}


@schemas.shape_checked(lambda: REJUDGE_SCHEMA, what='judgement again')
def _validate_rejudge(frozen: dict, payload, existing_ids: set) -> dict:
    if not isinstance(payload, dict) or payload.get('slug') != frozen['card']['slug']:
        raise IngestAnswerError(f'the answer must judge {frozen["card"]["slug"]}')
    problem = overlap.with_problem(payload.get('overlap'), payload.get('with') or [],
                                   overlap.KnownIds(existing_ids, 'existing'))
    if problem:
        raise IngestAnswerError(f'{payload["slug"]}: {problem}')
    if not isinstance(payload.get('reason'), str) or not payload['reason'].strip():
        raise IngestAnswerError(f'{payload["slug"]}: give a concrete reason')
    return payload


def _disputed(state: dict, slot: str) -> dict:
    """``{slug: reason}`` of the cards round ``slot`` of the merge check disputed (and nothing superseded since)."""
    return {s: e['data']['reason'] for s, e in decisions.values(state.get('decisions') or [], 'dispute').items()
            if e['slot'] == slot}


def _source_check_stage(root: Path, job_id: str, packet: dict, settled: list[dict], executor) -> list:
    """Run the merge check when it is due and not done, round after round; returns host hand-offs.

    A round whose answer disputes cards of the patch set sends each of them
    back to its chunk reviewer (a run of its own, :func:`_rejudge_slot`), then
    runs again as a new round (``xc01.r1`` ...) that replaces the whole round
    before, at most ``max_repair_rounds`` times; cards still disputed then stay
    pending. An answer that fails validation is asked for again while the
    repair budget lasts; after that the check is recorded as skipped and the
    cards are written as the chunk reviews left them.
    """
    from ..runner import AwaitingSubmission, call_executor
    rules = jobs.policy_of(packet)
    while True:
        state = jobs.load(root, job_id)
        routed = _for_merge(root, packet, _routed(state, settled))
        if not _merge_due(state, routed):
            return []
        n = max(0, _merge_round(state))
        slot = source_check.round_slot(n)
        info = (state.get('chunks') or {}).get(slot) or {}
        if info.get('skipped'):
            return []
        if info.get('review'):
            disputed = _disputed(state, slot)
            if not disputed or not _rounds_left(slot, 'reviewer', rules, n):
                return []
            for slug in sorted(disputed):
                rj = _rejudge_slot(n + 1, slug)
                while True:  # asked again while its budget lasts; past it the card waits for the owner (_routed)
                    rinfo = (state.get('chunks') or {}).get(rj) or {}
                    if rinfo.get('review') or rinfo.get('given_up'):
                        break
                    if not rinfo.get('candidate'):
                        jobs.put_program_candidate(root, job_id, rj, _rejudge_input(root, state, packet, routed,
                                                                                     slug, disputed[slug]))
                        _mark_slot(root, job_id, rj, dispute=disputed[slug])
                        state = jobs.load(root, job_id)
                        rinfo = state['chunks'][rj]
                    text = _prompt('review_dispute.md', state)
                    if rinfo.get('last_review_error'):
                        text += ('\n## Your previous answer was rejected by the validator\n\n'
                                 + rinfo['last_review_error'] + '\n')
                    try:
                        call_executor(root, job_id, executor, role='reviewer', step='review', prompt_text=text,
                                      packet=jobs.artifact(root, state, 'candidate', rj),
                                      output_schema=REJUDGE_SCHEMA, chunk=rj)
                    except AwaitingSubmission as waiting:
                        return [{**waiting.handoff, 'chunk': rj, 'step': 'dispute'}]
                    except ExecutorFailure as failure:
                        _failed_answer(root, job_id, rj, 'reviewer', failure)
                    state = jobs.load(root, job_id)
            slot = source_check.round_slot(n + 1)
            info = {}
            routed = _for_merge(root, packet, _routed(state, settled))
            if not _merge_due(state, routed):
                return []
        if not info.get('candidate'):
            jobs.put_program_candidate(root, job_id, slot, _source_check_input(root, packet, routed))
            state = jobs.load(root, job_id)
            info = state['chunks'][slot]
        text = merge_check_prompt(job_id)
        if info.get('last_validation_error'):
            text += '\n## Your previous answer was rejected by the validator\n\n' + info['last_validation_error'] + '\n'
        try:
            call_executor(root, job_id, executor, role='reviewer', step='review', prompt_text=text,
                          packet=jobs.artifact(root, state, 'candidate', slot), output_schema=source_check.SCHEMA,
                          chunk=slot)
        except AwaitingSubmission as waiting:
            return [{**waiting.handoff, 'chunk': slot, 'step': source_check.STEP}]
        except ExecutorFailure as failure:
            _failed_answer(root, job_id, slot, 'reviewer', failure)


def _rejudge_input(root: Path, state: dict, packet: dict, routed: list[dict], slug: str, reason: str) -> dict:
    """What the chunk reviewer judges a disputed card on: the card, its chunk, the dispute, the existing cards."""
    from ..runner import withhold_reasons
    chunk, card = next((o['chunk'], c) for o in routed for s, c in (o.get('repeats') or {}).items() if s == slug)
    snapshot = read_snapshot(root)
    near, near_cards = _review_candidates(root, snapshot, packet, [card])
    shown = {k: v for k, v in card.items() if not k.startswith('_')}
    return {'card': withhold_reasons(shown) if jobs.policy_of(packet).withhold_producer_reasons else shown,
            'chunk': {'id': chunk, 'text': _chunk_text(packet, chunk)}, 'dispute': reason,
            'level': {'overlap': 'extends', 'with': [card['_with']]},
            'candidates': near, 'candidate_cards': near_cards}


# --------------------------------------------------------------------------
# Split slots (see split): an extends card whose existing card cannot take its patch
# --------------------------------------------------------------------------
def _split_item(state: dict, slot: str) -> dict:
    base = _base(slot)
    return next(i for i in state.get('split_plan') or [] if i['slot'] == base)


def _patch_fits(old, patch: dict) -> tuple[str | None, list, list]:
    """``(why it does not fit or None, propositions to add, review cards to add)`` for a patch to existing card ``old``.

    Only a repeat of what the card (or the patch) holds is left out (:func:`source_check.repeat`: a proposition in
    the same words once spaces are put right, a review card whose question and answer both are); what the
    comparison only finds the same went back to the merge check at validation (decision D-21).
    """
    problem = cardfmt.patchable(old.body)
    if problem:
        return problem, [], []
    props, flashcards = cardfmt.propositions_of(old.body), cardfmt.flashcards_of(old.body)
    replaced = {int(r['proposition'][1:]): r['by'] for r in patch.get('replace') or []}
    have = [replaced.get(n, p) for n, p in enumerate(props, 1)]  # the card once replaced: a proposition swapped out
    # is no longer there to hold a carried one
    try:
        flashcards, _ = source_check.swap_flashcards(flashcards, patch.get('flashcard_replace') or [])  # D-26
    except ValueError as exc:
        return str(exc), [], []
    add = []
    for text in patch.get('propositions') or []:
        if not any(source_check.repeat(text, x) for x in have + add):
            add.append(text)
    add_f = []
    for f in patch.get('flashcards') or []:
        if not any(source_check.repeat(f, x) for x in flashcards + add_f):
            add_f.append(f)
    if len(props) + len(add) > source_check.MAX_PROPOSITIONS or len(flashcards) + len(add_f) > source_check.MAX_FLASHCARDS:
        return 'too_large', add, add_f
    return None, add, add_f


def _split_plan(root: Path, job_id: str, packet: dict, settled: list[dict]) -> list[dict]:
    """The split slots of the job, fixed in its state the first time they are needed.

    One per card of the patch set whose existing card's patch would go past 12
    propositions or 8 review cards. A card whose propositions the existing card all
    says (``already``) opens none: when it carries nothing more it is covered, and when
    it carries only review cards they go into the existing card as a patch of their
    own (``split_qa_only``, by existing card), held when they would take it past 8.
    """
    state = jobs.load(root, job_id)
    if state.get('split_plan') is not None:
        return state['split_plan']
    checked, report = _checked(root, state, packet, settled)
    patches = (report or {}).get('patches') or {}
    disputed = set((report or {}).get('disputes') or {})
    snapshot = read_snapshot(root)
    plan, covered, qa_only = [], [], {}
    frozen = jobs.artifact(root, state, 'candidate', report['round']) if (report or {}).get('round') else {}
    source_cards = {c['slug']: c for c in frozen.get('cards') or []}
    merged_into = (report or {}).get('merged_into') or {}
    for o in checked:
        for slug, card in (o.get('repeats') or {}).items():
            target = card['_with']
            old = snapshot.cards.get(target)
            if slug in disputed or target not in patches or old is None or not _merging(packet) or \
                    card.get('_given_up'):
                continue
            problem, _, _ = _patch_fits(old, patches[target])
            if problem != 'too_large':
                continue
            # D-22: each slot gets what its own card adds; a card the existing card holds all of gets none
            own = _own_carry(card, patches[target], [source_cards[s] for s, into in merged_into.items()
                                                     if into == slug and s in source_cards])
            if not any(own.get(k) for k in ('propositions', 'flashcards', 'replace')):
                covered.append(slug)
                continue
            if not own.get('propositions') and not own.get('replace'):  # only review cards left (D-24): no slot
                row = qa_only.setdefault(target, {'cards': [], 'flashcards': [], 'already': []})
                row['cards'].append(slug)
                row['flashcards'] += own.get('flashcards') or []
                row['already'] += own.get('already') or []
                continue
            plan.append({'slot': split.slot_name(len(plan) + 1), 'slug': slug, 'chunk': o['chunk'], 'with': target,
                         'existing_propositions': cardfmt.propositions_of(old.body),
                         'place': {'domain': old.domain, 'pillar': old.fm.get('pillar'), 'topic': old.fm.get('topic')},
                         'carry': own,
                         'card': {k: v for k, v in card.items() if not k.startswith('_')}})
    kept = {s: c for o in checked for s, c in o['accepted'].items()}
    for o in checked:  # a card of this source that would make the card it extends too large (decision D-15)
        for slug, card in (o.get('split_merges') or {}).items():
            into = kept[card['_with']]
            shown = {'id': card['_with'], 'title': into['title'], 'essence': into.get('essence'),
                     'propositions': list(into.get('propositions') or []),
                     'flashcards': list(into.get('flashcards') or [])}
            plan.append({'slot': split.slot_name(len(plan) + 1), 'slug': slug, 'chunk': o['chunk'],
                         'with': card['_with'], 'of_source': True, 'existing_card': shown,
                         'existing_propositions': shown['propositions'],
                         'place': {'domain': into['domain'], 'pillar': into['pillar'], 'topic': into['topic']},
                         'carry': card['_carry'], 'card': {k: v for k, v in card.items() if not k.startswith('_')}})
    jobs._internal_update(root, job_id, split_plan=plan, split_covered=covered, split_qa_only=qa_only)
    return plan


def _merging(packet: dict) -> bool:
    """Whether this job adds what an ``extends`` card holds to its existing card (``[ingest] merge_into_existing``)."""
    return bool(_frozen(packet, 'merge_into_existing'))


def split_outcome(root: Path, state: dict, packet: dict, item: dict) -> dict:
    """``{"settled", "next", "card", "pending"}`` of one split slot, its rounds like a chunk's."""
    base = item['slot']
    slots = _slots(state, base)
    info0 = (state.get('chunks') or {}).get(base) or {}
    rules = jobs.policy_of(packet)
    used = len(slots) - 1
    if not slots or not slots[0][1].get('candidate'):
        if not _retries_left(info0, rules):
            return {'settled': True, 'next': None, 'card': None,
                    'pending': f'the split card failed validation {_refusals(info0, ANSWER_ROLES[0])} times: '
                               f'{str(info0.get("last_validation_error") or "")[:500]}'}
        return {'settled': False, 'next': ('produce', base), 'card': None, 'pending': None}
    last = None
    for slot, info in slots:
        if not info.get('candidate'):
            if _retries_left(info, rules):
                return {'settled': False, 'next': ('repair', slot), 'card': None, 'pending': None}
            break
        if not info.get('review'):
            if _retries_left(info, rules, answer_role(slot, 'reviewer')):
                return {'settled': False, 'next': ('review', slot), 'card': None, 'pending': None}
            return {'settled': True, 'next': None, 'card': None, 'pending': info.get('given_up')}
        review = jobs.require_independent_review(root, state, slot)
        last = (slot, review)
        if review['decision'] == 'accept':
            card = jobs.artifact(root, state, 'candidate', slot)['card']
            return {'settled': True, 'next': None, 'card': {**card, '_slot': slot}, 'pending': None}
    if last and _rounds_left(base, 'producer', rules, used) and len(slots) == sum(1 for _, i in slots
                                                                                 if i.get('review')):
        return {'settled': False, 'next': ('repair', _slot_name(base, len(slots))), 'card': None, 'pending': None}
    return {'settled': True, 'next': None, 'card': None,
            'pending': f'the split card was not accepted: {(last[1] if last else {}).get("reason", "")}'}


def _split_siblings(root: Path, state: dict, item: dict) -> list[tuple[str, list]]:
    """``[(slug, propositions)]`` of the latest card of every other split slot of the same existing card (D-22)."""
    out = []
    for other in state.get('split_plan') or []:
        if other['with'] != item['with'] or other['slot'] == item['slot']:
            continue
        written = [s for s, info in _slots(state, other['slot']) if info.get('candidate')]
        if written:
            card = jobs.artifact(root, state, 'candidate', written[-1]).get('card') or {}
            out.append((card.get('slug') or other['slug'], list(card.get('propositions') or [])))
    return out


def _own_carry(card: dict, patch: dict, merged: list[dict]) -> dict:
    """What of ``patch`` comes from ``card`` and the cards of the source merged into it (D-22): each split slot
    rewrites only its own card's additions."""
    props = set(card.get('propositions') or []) | {p for m in merged for p in m.get('propositions') or []}
    qa = {(f.get('q'), f.get('a')) for c in [card, *merged] for f in c.get('flashcards') or []}
    out = {'propositions': [p for p in patch.get('propositions') or [] if p in props],
           'flashcards': [f for f in patch.get('flashcards') or [] if (f.get('q'), f.get('a')) in qa],
           'replace': [r for r in patch.get('replace') or [] if r.get('by') in props],
           'already': [a for a in patch.get('already') or [] if a.get('text') in props]}
    return {k: v for k, v in out.items() if v}


def _split_turned(root: Path, job_id: str, kind: str = 'note') -> list[dict]:
    """The split cards the program sent back after their review accepted them, every round: for a blocking note
    (``kind`` note, D-18 3), or for a proposition the existing card says (``kind`` matches, D-22)."""
    state = jobs.load(root, job_id)
    out = []
    for slot, info in sorted((state.get('chunks') or {}).items()):
        if not split.is_slot(_base(slot)) or not info.get('review'):
            continue
        turned = jobs.artifact(root, state, 'review', slot).get('turned') or {}
        if not turned.get(kind):
            continue
        item = _split_item(state, slot)
        row = {'chunk': item['chunk'], 'slot': slot, 'round': _round(slot), 'slug': item['slug']}
        out.append({**row, 'category': turned['note']['category'], 'note': turned['note']['text']} if kind == 'note'
                   else {**row, 'with': item['with'], 'matches': [list(m) for m in turned['matches']]})
    return out


def _split_stage(root: Path, job_id: str, packet: dict, settled: list[dict], producer, reviewer,
                 only: str | None = None, role: str | None = None) -> list:
    """Run every split slot's step that can run now; returns host hand-offs."""
    from ..runner import AwaitingSubmission, call_executor
    handoffs, handed = [], set()
    while True:
        state = jobs.load(root, job_id)
        todo = [(i, split_outcome(root, state, packet, i)) for i in _split_plan(root, job_id, packet, settled)]
        todo = [(i, o) for i, o in todo if not o['settled'] and i['slot'] not in handed
                and (only is None or _base(only) == i['slot'])
                and (role is None or (o['next'][0] == 'review') == (role == 'reviewer'))]
        if not todo:
            return handoffs
        item, out = todo[0]
        kind, slot = out['next']
        snapshot = read_snapshot(root)
        # the card it extends: an existing card, or a card of this source (not written yet: its fields)
        existing = item['existing_card'] if item.get('of_source') else \
            cardfmt.view(snapshot.cards[item['with']], 'body' if kind != 'review' else 'full')
        shown = {'existing_card': existing,
                 'chunk': {'id': item['chunk'], 'text': _chunk_text(packet, item['chunk'])},
                 **_note_part(root, state, packet, item['chunk']),
                 **_relation_views(packet['catalog'], [item['place']['domain']])}
        try:
            if kind == 'review':
                from ..runner import withhold_reasons
                text = split_review_prompt(state['job_id'])
                feedback = ((state.get('chunks') or {}).get(slot) or {}).get('last_review_error')
                if feedback:
                    text += '\n## Your previous answer was rejected by the validator\n\n' + feedback + '\n'
                with jobs.reading('review_packet', (slot, 'producer')):
                    cand = jobs.artifact(root, state, 'candidate', slot)
                    body = {**shown, 'card': withhold_reasons(cand['card']), **_program_view(root, state, slot, cand),
                            'other_chunks_cards': _other_cards(root, state, packet, item['chunk'],
                                                               named_titles([cand['card']]))}
                try:
                    call_executor(root, job_id, reviewer, role='reviewer', step='review', prompt_text=text,
                                  packet=body, output_schema=split.REVIEW_SCHEMA, chunk=slot)
                except ExecutorFailure as failure:
                    _failed_answer(root, job_id, slot, 'reviewer', failure)
            else:
                body = {**shown, 'card': item['card'], 'carry': item['carry'],
                        'other_chunks_cards': _other_cards(root, state, packet, item['chunk'],
                                                           named_titles([item['card']]))}
                if kind == 'repair':
                    prev = _slot_name(item['slot'], _round(slot) - 1)
                    with jobs.reading('produce_packet', (prev, 'reviewer')):  # the last round's card and review
                        body.update(previous_candidate=jobs.as_given(jobs.artifact(root, state, 'candidate', prev)),
                                    review_findings=jobs.artifact(root, state, 'review', prev))
                text = _prompt('split_card.md', state)
                feedback = ((state.get('chunks') or {}).get(slot) or {}).get('last_validation_error')
                if feedback:
                    text += '\n## Your previous answer was rejected by the validator\n\n' + feedback + '\n'
                try:
                    call_executor(root, job_id, producer, role='producer', step=kind, prompt_text=text, packet=body,
                                  output_schema=split.CARD_SCHEMA, chunk=slot)
                except ExecutorFailure as failure:
                    _failed_answer(root, job_id, slot, 'producer', failure)
        except AwaitingSubmission as waiting:
            handoffs.append({**waiting.handoff, 'chunk': slot, 'step': 'split'})
            handed.add(item['slot'])


#: The view each route's candidates get in the merge check's packet (it only adds links).
MERGE_CHECK_LEVELS = {'words': 'brief'}


def _source_check_input(root: Path, packet: dict, settled: list[dict]) -> dict:
    """The merge check's frozen input: the decision set's cards, their relation types, the existing cards each may link."""
    snapshot = read_snapshot(root)
    domains = sorted({c['domain'] for o in settled for c in o['accepted'].values()})
    k = retrieval.k_for(_frozen(packet, 'candidates_k'), 'merge_check')
    found = {slug: retrieval.candidates(root, snapshot, retrieval.card_query(card), k=k)
             for o in settled for slug, card in o['accepted'].items()}
    # only to add links: the brief view says what each card is; one table within the packets' budget
    table, packing = retrieval.fit(snapshot, retrieval.merge(found.values()), _frozen(packet, 'candidates_chars'),
                                   MERGE_CHECK_LEVELS)
    # each card's candidates by id only, closest first; the table shows each once, with its domain (the
    # cross-domain relation rule needs it even for a card shown by title only)
    table = {cid: {**view, 'domain': snapshot.cards[cid].domain} for cid, view in table.items()}
    budget, chars = _frozen(packet, 'candidates_chars'), sum(cardfmt.view_chars(v) for v in table.values())
    for e in reversed(retrieval.merge(found.values())):  # the domains added to title-only views stay in the budget
        if chars <= budget:
            break
        if e['id'] in table and set(table[e['id']]) == {'id', 'title', 'domain'} and 'named' not in e['via']:
            chars -= cardfmt.view_chars(table.pop(e['id']))
            packing['left_out'] = list(packing['left_out']) + [e['id']]
    packing['chars'] = chars
    rows = {slug: [e['id'] for e in retrieval.keep(entries, table)] for slug, entries in found.items()}
    frozen = source_check.frozen_input(settled, packet["source"], candidates=rows, candidate_cards=table,
                                       relation_views=_relation_views(packet['catalog'], domains),
                                       existing=lambda cid: cardfmt.view(snapshot.cards[cid], 'full'))
    frozen['packing'] = {'view_chars': packing['chars'], 'budget': packing['budget'], 'lowered': packing['lowered'],
                         'left_out': len(packing['left_out']),
                         'row_chars': len(json.dumps([c['candidates'] for c in frozen['cards']]))}
    if _frozen(packet, 'context_note') != 'off' and len(packet['chunks']) > 1:  # one copy of the source's outline
        frozen['source']['outline'] = chunking.outline_text(packet['source']['text'], None,
                                                            _frozen(packet, 'context_note_chars'))
    return {**frozen, 'placement': packet['placement']} if packet.get('placement') else frozen


def skip_source_check(root: Path, job_id: str, *, reason: str) -> dict:
    with _frozen_run(root, jobs.read_input(root, job_id)):
        return _skip_source_check(root, job_id, reason=reason)


def _skip_source_check(root: Path, job_id: str, *, reason: str) -> dict:
    """Give up the source check of ``job_id`` after it kept failing; the cards are written as the chunk reviews left them.

    Answers that fail validation, and runs given up without an answer, are counted at the gate in both modes
    (:func:`secondbrain.jobs.receive`); once they are more than ``max_invalid_retries`` the check is skipped by
    itself, and this command finds it done. Before that it refuses: hand the check to a new sub-agent.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('skipping the source check needs a concrete reason')
    with job_lock(root, job_id):
        state = jobs.load(root, job_id)
        packet = jobs.read_input(root, job_id)
        if state['kind'] != KIND:
            raise IngestError(f'{job_id} is not an ingest job')
        settled = [chunk_outcome(root, state, packet, c['chunk_id']) for c in effective_chunks(packet, state)]
        info = (state.get('chunks') or {}).get(source_check.SLOT) or {}
        if not all(o['settled'] for o in settled) or not _merge_due(
                state, _for_merge(root, packet, _routed(state, settled))) or not info.get('candidate'):
            raise IngestError(f'{job_id} has no source check waiting; run `sb job advance`')
        if info.get('review') or info.get('skipped'):
            raise IngestError(f'The source check of {job_id} is already done')
        runs = [jobs.load_run(root, job_id, r) for r in state.get('runs') or []]
        runs = [r for r in runs if r.get('chunk') == source_check.SLOT]
        failed = _merge_check_failures(root, state, source_check.SLOT)
        needed = jobs.policy_of(packet).max_invalid_retries + 1
        if failed < needed:
            raise IngestError(f'The source check can be skipped after {needed} rejected answers or given-up runs; '
                              f'{job_id} has {failed}. Hand the check to a new sub-agent first')
        for run in runs:
            if run['status'] == 'started':
                jobs.abandon_run(root, job_id, run['run_id'], reason=f'source check skipped: {reason}')
        _mark_slot(root, job_id, source_check.SLOT, skipped=f'skipped after {failed} rejected answers or given-up '
                                                           f'runs: {reason.strip()}; the cards were written as the '
                                                           'chunk reviews left them')
        return jobs.load(root, job_id)


def _checked(root: Path, state: dict, packet: dict, settled: list[dict],
             entries: list | None = None) -> tuple[list[dict], dict | None]:
    """``(settled, report)``: the chunks routed by the reviewer's levels (:func:`_routed`), then the merge check's
    values applied; ``report`` is None when no check was due.

    ``entries`` is the decision record the merge records its program steps in
    (a copy of the job's when not given).
    """
    entries = list(state.get('decisions') or []) if entries is None else entries
    routed = _routed(state, settled)
    judged = _for_merge(root, packet, routed)
    if not _merge_due(state, judged):
        return routed, None
    slot = source_check.round_slot(max(0, _merge_round(state)))
    info = (state.get('chunks') or {}).get(slot) or {}
    if info.get('skipped'):
        return routed, {'status': 'skipped', 'reason': info['skipped']}
    if not info.get('review'):
        raise Conflict('The source check is not finished; run `sb job advance`')
    frozen = jobs.artifact(root, state, 'candidate', slot)
    try:
        out, report = source_check.apply(judged, frozen, entries)
    except ValueError as exc:
        raise Conflict(str(exc)) from exc
    return [{**o, 'repeats': r.get('repeats', {})} for o, r in zip(out, routed)], {**report, 'round': slot}


# --------------------------------------------------------------------------
# Diagram step (after review, on final card fields; see card_diagrams)
# --------------------------------------------------------------------------
def _diagram_cards(root: Path, job_id: str, packet: dict, settled: list[dict]) -> list[dict]:
    """The card list the diagram step works on, frozen in the job state on first use."""
    state = jobs.load(root, job_id)
    if state.get('diagram_cards') is not None:
        return state['diagram_cards']
    accepted = [(o['chunk'], s, c) for o in settled for s, c in o['accepted'].items()]
    snapshot = read_snapshot(root)
    titles = {slug: card['title'] for _, slug, card in accepted}

    def title_of(target):
        if target in titles:
            return titles[target]
        card = snapshot.cards.get(target)
        return card.title if card else None
    cards = card_diagrams.cards_for(accepted, title_of)
    jobs._internal_update(root, job_id, diagram_cards=cards)
    return cards


def _diagram_answer(root: Path, state: dict, slot: str) -> tuple[bool, object]:
    info = (state.get('chunks') or {}).get(slot) or {}
    if info.get('candidate'):
        return True, jobs.artifact(root, state, 'candidate', slot)
    if info.get('skipped') or info.get('invalid_output'):  # its budget used up (invalid_output: an older job)
        return True, None
    return False, None


def diagram_outcome(root: Path, job_id: str, packet: dict, settled: list[dict]) -> dict:
    if not packet['settings'].get('diagrams'):
        return {'pending': [], 'diagrams': {}, 'report': []}
    cards = _diagram_cards(root, job_id, packet, settled)
    state = jobs.load(root, job_id)
    return card_diagrams.evaluate(cards, lambda slot: _diagram_answer(root, state, slot))


def _diagram_stage(root: Path, job_id: str, packet: dict, settled: list[dict], executor,
                   only: str | None = None) -> tuple[dict, list]:
    """Run every diagram request that can run now; returns (outcome, host hand-offs)."""
    from ..runner import AwaitingSubmission, call_executor
    handoffs, handed = [], set()
    while True:
        outcome = diagram_outcome(root, job_id, packet, settled)
        todo = [p for p in outcome['pending'] if p[0] not in handed and (only is None or p[0] == only)]
        if not todo:
            return outcome, handoffs
        slot, step, prompt_text, request = todo[0]
        try:
            call_executor(root, job_id, executor, role='producer', step=step, prompt_text=prompt_text,
                          packet=request, output_schema=card_diagrams.answer_schema(), chunk=slot)
        except AwaitingSubmission as waiting:
            handoffs.append({**waiting.handoff, 'chunk': slot, 'step': 'diagram'})
            handed.add(slot)
        except ExecutorFailure as failure:
            _failed_answer(root, job_id, slot, 'producer', failure)


def _mark_slot(root: Path, job_id: str, slot: str, **fields) -> None:
    from ..store import vault_lock, write_json
    with vault_lock(root):
        state = jobs.load(root, job_id)
        info = state.setdefault('chunks', {}).setdefault(
            slot, {'candidate': None, 'review': None, 'status': 'prepared', 'round': 0})
        info.update(fields)
        state['updated_at'] = jobs.now()
        write_json(root, jobs.location(job_id), state)


def _summary(settled: list[dict]) -> dict:
    return {'accepted': [{'slug': s, 'title': c['title'], 'domain': c['domain'], 'chunk': o['chunk']}
                         for o in settled for s, c in o['accepted'].items()],
            'pending': [p for o in settled for p in o['pending']],
            'duplicates': [d for o in settled for d in o['duplicates']],
            'new_topics': [{'domain': k[0], 'pillar': k[1], 'name': k[2]} for o in settled for k in o['topics']],
            'needs_taxonomy': [x for o in settled for x in o['needs_taxonomy']]}


# --------------------------------------------------------------------------
# S8 write, S9 commit
# --------------------------------------------------------------------------
def _assign_ids(prefix: str, slugs: list[str], taken: set) -> dict:
    out = {}
    for slug in slugs:
        candidate, n = f'{prefix}{slug}', 2
        while candidate in taken:
            candidate, n = f'{prefix}{slug}-{n}', n + 1
        taken.add(candidate)
        out[slug] = candidate
    return out


def _unique_title(title: str, taken: set) -> str:
    """``title``, or with `` (2)``, `` (3)`` ... when a card of the vault says the same words (``taken``: their
    :func:`secondbrain.textnorm.plain` forms)."""
    candidate, n = title, 2
    while plain(candidate) in taken:
        candidate, n = f'{title} ({n})', n + 1
    taken.add(plain(candidate))
    return candidate


def content_digest(card) -> str:
    """Digest of a card's text apart from its related section (which back links change) and front matter."""
    related = {h['related'] for h in cardfmt.HEADINGS.values()}
    parts = {k: v for k, v in cardfmt.sections(card.body).items() if k not in related}
    return digest(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode('utf-8'))


def _conflict_key(row: dict) -> tuple:
    """What makes two conflict records the same disagreement: the source, the card's slug and the existing card.

    A parent job records a conflict of a card it could not write with no id; the retry that writes the card
    records it again with the id (drill 2 copy: 5 shown, 4 real). The later record wins.
    """
    if row.get('slug') and row.get('source_sha256'):
        return row['source_sha256'], row['slug'], row['with']
    return row.get('card') or row.get('slug'), row['with']


def open_conflicts(root: Path, snapshot) -> list[dict]:
    """Recorded conflicts whose existing card is unchanged since (``sb status``); the latest record per pair."""
    rows = {}
    for row in inbox._jsonl(root, layouts.of(root).conflicts):
        card = snapshot.cards.get(row.get('with'))
        if card is not None and row.get('with_digest') == content_digest(card):
            rows[_conflict_key(row)] = {
                k: row.get(k) for k in ('with', 'with_title', 'card', 'slug', 'title', 'detail', 'by', 'job_id', 'at',
                                        'source_sha256')}
    return list(rows.values())


def _merge_topics(tax: dict, additions: list[tuple[str, str]]) -> dict:
    merged = json.loads(json.dumps(tax))
    for pillar, name in additions:
        for p in merged.get('pillars', []):
            if p.get('name') == pillar and name not in p.get('topics', []):
                p.setdefault('topics', []).append(name)
    return merged


def _inverse_edge(inv: str, source_id: str, relation: dict) -> dict:
    """The back link of ``relation``; a weak (extension) link stays weak."""
    edge = {'type': inv, 'target': source_id}
    if relation.get('tier'):
        edge['tier'] = relation['tier']
    return edge


def _current_taxonomy(snapshot, domain: str) -> dict:
    moc = (snapshot.catalog.get('domains', {}).get(domain) or {}).get('moc')
    return snapshot.taxonomies.get(moc) or {}


def apply_job(root: Path, job_id: str, settled: list[dict] | None = None) -> dict:
    with _frozen_run(root, jobs.read_input(root, job_id)):
        return _apply_job(root, job_id, settled)


def _apply_job(root: Path, job_id: str, settled: list[dict] | None = None) -> dict:
    from ..moc import build_mocs
    from ..projections import current_day, entry_projection, planned_paths
    state = jobs.load(root, job_id)
    if state.get('commit_intent'):
        return jobs.resume_transaction(root, job_id) or jobs.load(root, job_id)
    packet = jobs.read_input(root, job_id)
    settled = settled or [chunk_outcome(root, state, packet, c['chunk_id'])
                          for c in effective_chunks(packet, state)]
    if not all(o['settled'] for o in settled):
        raise Conflict('Not every chunk is reviewed yet; run `sb job advance`')
    # the write reads final values only from the decision record; its own steps (topic refiles, relation
    # filters) are added to a copy of it, which the result keeps
    entries = list(state.get('decisions') or [])
    chunks_settled = settled
    settled, check = _checked(root, state, packet, settled, entries)
    outcomes = _outcomes_of(entries, chunks_settled, settled, check)
    lay = layouts.of(root)
    settings = packet['settings']
    language = settings['language']
    drawn = diagram_outcome(root, job_id, packet, settled)
    if drawn['pending']:
        raise Conflict('The diagram step is not finished; run `sb job advance`')
    snapshot = read_snapshot(root)
    writable_snapshot(snapshot)
    fates = _repeat_fates(root, state, packet, settled, check, snapshot)
    # every fate of a card of the patch set (or of a card of the source too large to merge) goes into the decision
    # record, with why; the outcomes, patch_set, merge_set and the pending list are read back from there, so they
    # never differ (a card written next to its existing card: write; one its card already holds: drop; one waiting
    # for the owner: pending)
    for s, f in sorted(fates.items()):
        decisions.record(entries, 'outcome.program', s, _fate_record(f, outcomes[s]), role='program', slot='write')
    ran = bool(check) and check.get('status') == 'checked'
    outcomes = {**outcomes, **{s: overlap.outcome(entries, s, checked=ran)['outcome'] for s in fates}}
    recorded_fates = {s: e['data'] for s, e in decisions.values(entries, 'outcome.program').items() if s in fates}
    accepted = [(o['chunk'], s, _final_card(entries, s, c)) for o in settled for s, c in o['accepted'].items()]
    accepted += [(o['chunk'], s, {**_final_card(entries, s, c), '_not_merged': fates[s]['reason']})
                 for o in settled for s, c in o.get('repeats', {}).items() if fates[s]['how'] == 'written']
    accepted += [(f['chunk'], f['card']['slug'], {**_final_card(entries, f['card']['slug'], f['card']),
                                                  '_split_of': f['target'], '_from': s})
                 for s, f in fates.items() if f['how'] == 'split']
    pending = [p for o in settled for p in o['pending']]
    held = {x: c for o in settled for x, c in [*o.get('repeats', {}).items(), *o.get('split_merges', {}).items()]}
    pending += [{'slug': s, 'title': held[s]['title'], 'chunk': r['chunk'], 'reason': r.get('reason')}
                for s, r in sorted(recorded_fates.items()) if r['how'] == 'pending']
    # a link to a card that went into its existing card goes there; to one rewritten as a split card, to that card
    into_existing = {s: f['target'] for s, f in fates.items() if f['how'] in ('patched', 'covered')}  # links to it go there
    alias = {s: f['card']['slug'] for s, f in fates.items() if f['how'] == 'split'}
    if not jobs._bindings(state):
        return jobs.update(root, job_id, status='failed', error={
            'code': 'NO_REVIEWED_CANDIDATE', 'pending': pending,
            'message': 'no chunk produced a candidate that passed validation'})
    # New topics: at most N per source, first come first served in chunk order;
    # a topic someone added meanwhile is simply used, a vanished pillar is not.
    topics, topic_overflow = [], []
    for o in settled:
        for key in o['topics']:
            domain, pillar, name = key
            if key in topics or (pillar, name) in category_pairs(snapshot, domain):
                continue
            (topics if len(topics) < settings['new_topics_per_source'] else topic_overflow).append(key)
    keep = []
    for chunk, slug, card in accepted:
        key = (card['domain'], card['pillar'], card['topic'])
        pillars = {p.get('name') for p in (_current_taxonomy(snapshot, card['domain']).get('pillars') or [])
                   if isinstance(p, dict)}
        if key in topic_overflow:
            reason = 'more new topics than new_topics_per_source allows'
            decisions.step(entries, 'classification', slug, 'overflow', role='program', slot='write',
                           data={'reason': reason})
        elif card['domain'] not in snapshot.catalog.get('domains', {}):
            reason = f'domain {card["domain"]!r} no longer exists'
        elif (card['pillar'], card['topic']) in category_pairs(snapshot, card['domain']) or \
                (key in topics and card['pillar'] in pillars):
            keep.append((chunk, slug, card))
            continue
        else:
            reason = (f'category {card["pillar"]} / {card["topic"]} is neither in the taxonomy '
                      'nor an accepted new topic')
        pending.append({'slug': slug, 'title': card['title'], 'chunk': chunk, 'reason': reason})
    accepted = keep
    topics = [t for t in topics if any((c['domain'], c['pillar'], c['topic']) == t for _, _, c in accepted)]
    ids = _assign_ids(settings['id_prefix'], [s for _, s, _ in accepted], set(snapshot.cards))
    titles_taken = {plain(c.title) for c in snapshot.cards.values()}
    titles = {s: _unique_title(c['title'], titles_taken) for _, s, c in accepted}
    source = packet['source']
    source_rel = lay.source_path(source['sha256'], source['filename'])
    source_raw = base64.b64decode(source['raw_b64'], validate=True)
    from ..reviews import now_local
    today = now_local().date()  # cards are dated by the calendar; HOME below uses the review day
    inverse = packet['catalog'].get('inverse_relations') or {}
    # relations: resolve slugs, drop links to cards that are not written
    rels: dict = {s: [] for _, s, _ in accepted}
    skipped_relations = []
    new_domains = {ids[s]: c['domain'] for _, s, c in accepted}
    # a target the reviewer judged the same as existing cards lives on in the first of them (drill 2, P6)
    duplicate_of = {d['slug']: d['with'][0] for o in settled for d in o['duplicates']
                    if d.get('by') == 'reviewer' and d.get('with') and d['with'][0] in snapshot.cards}

    def dropped(slug, r, why, op='filter', **extra):  # the program's step on a relation, kept in the record
        decisions.step(entries, 'relations', slug, op, role='program', slot='write',
                       data={'target': r['target'], 'type': r['type'], 'why': why, **extra})
    # a pending target may be written by a retry, which then adds the link; a dropped one will not be
    open_slugs = {p.get('slug') for p in pending if p.get('slug')}

    def unwritten(slug):
        return 'target card was not written' if slug in open_slugs else \
            'target card was dropped in review; add the link by hand if it still matters'
    for _, slug, card in accepted:
        linked = {alias.get(r.get('target'), r.get('target')) for r in card.get('relations') or []}
        for r in card.get('relations') or []:
            target = alias.get(r['target'], r['target'])
            if target in into_existing:
                if into_existing[target] in linked or into_existing[target] == card.get('_split_of'):
                    dropped(slug, r, 'its target went into an existing card this card links already')
                    continue
                dropped(slug, r, 'its target went into an existing card', op='redirect', to=into_existing[target])
                target = into_existing[target]
            if target in snapshot.cards:
                tid = target
            elif target in ids:
                tid = ids[target]
            elif target in _written_slugs(packet) and _written_slugs(packet)[target] in snapshot.cards:
                tid = _written_slugs(packet)[target]
            elif target in duplicate_of:
                if duplicate_of[target] in linked:  # the card links the existing card already
                    dropped(slug, r, 'its target repeats an existing card this card links already')
                    continue
                tid = duplicate_of[target]
                dropped(slug, r, 'its target repeats an existing card', op='redirect', to=tid)
            else:
                skipped_relations.append({'from': ids[slug], 'type': r['type'], 'target_slug': target,
                                          'reason': unwritten(target)})
                dropped(slug, r, unwritten(target))
                continue
            closed = _closed_relation(packet['catalog'], r['type'], card['domain'],
                                      snapshot.cards[tid].domain if tid in snapshot.cards else new_domains.get(tid))
            if closed:  # refused at submit too; this also covers answers checked before that rule
                skipped_relations.append({'from': ids[slug], 'type': r['type'], 'target_slug': target,
                                          'reason': closed})
                dropped(slug, r, closed)
                continue
            if any(x['type'] == r['type'] and x['target'] == tid for x in rels[slug]):
                continue
            rels[slug].append({k: v for k, v in (('type', r['type']), ('target', tid),
                                                 ('note', r.get('note')), ('tier', r.get('tier'))) if v})
    # links an earlier job of this source could not write because their target was pending. Each gets one
    # outcome: added now (from the new card's side, so the back link reaches the earlier card), already linked
    # by the new card, sent to the existing card the target turned out to repeat, still pending (carried to the
    # next retry), or dropped with its target (listed, for the owner to add by hand)
    restored_relations, extra_edges = [], []
    merged_into = (check or {}).get('merged_into') or {}
    for carried in (packet.get('retry') or {}).get('skipped_relations') or []:
        kind, source_id = carried['type'], carried['from']
        slug = merged_into.get(carried['target_slug'], carried['target_slug'])
        row = {'from': source_id, 'type': kind, 'target_slug': carried['target_slug']}
        source_card = snapshot.cards.get(source_id)
        inv = inverse.get(kind)
        if source_card is None:
            skipped_relations.append({**row, 'reason': 'the card the link starts from no longer exists'})
            continue
        if slug in ids:
            tid, target_domain = ids[slug], new_domains[ids[slug]]
            linked = next((x for x in rels[slug] if x['target'] == source_id), None)
            if linked:
                restored_relations.append({**row, 'target': tid,
                                           'linked_as': {'from': tid, 'type': linked['type'], 'target': source_id}})
                continue
        elif slug in duplicate_of:
            tid = duplicate_of[slug]
            target_domain = snapshot.cards[tid].domain
            linked = next(({'from': a, 'type': e.get('type'), 'target': b}
                           for a, b in ((source_id, tid), (tid, source_id))
                           for e in snapshot.cards[a].fm.get('relations') or []
                           if isinstance(e, dict) and e.get('target') == b), None)
            if tid == source_id or linked:  # the target repeats the earlier card itself, or they are linked
                restored_relations.append({**row, 'target': tid, 'redirected_from': slug,
                                           **({'linked_as': linked} if linked else {})})
                continue
        else:
            skipped_relations.append({**row, 'reason': unwritten(slug)})
            continue
        closed = _closed_relation(packet['catalog'], kind, source_card.domain, target_domain)
        if closed:
            skipped_relations.append({**row, 'reason': closed})
            continue
        if slug in ids and inv:
            rels[slug].append({'type': inv, 'target': source_id})
        else:  # both cards exist, or the type has no inverse: write the link on the earlier card itself
            extra_edges.append((source_id, {'type': kind, 'target': tid}))
            if inv and tid in snapshot.cards:
                extra_edges.append((tid, {'type': inv, 'target': source_id}))
        moved = slug not in ids or slug != carried['target_slug']  # a duplicate, or merged by the source check
        restored_relations.append({**row, 'target': tid, **({'redirected_from': carried['target_slug']} if moved else {})})
    by_id = {ids[s]: s for s in ids}
    for slug in list(rels):
        for r in list(rels[slug]):
            inv = inverse.get(r['type'])
            if inv and r['target'] in by_id:
                back = rels[by_id[r['target']]]
                if not any(x['type'] == inv and x['target'] == ids[slug] for x in back):
                    back.append(_inverse_edge(inv, ids[slug], r))
    title_of = {cid: c.title for cid, c in snapshot.cards.items()}
    title_of.update({ids[s]: titles[s] for s in ids})
    # [[links]] in the text written go to the final titles: a card of this source that was written under another
    # title, went into an existing card or was judged the same as one is named by that card's title; a link to a
    # card that is nowhere becomes plain text, and its card is written needs-review (dangling_link)
    relinks = _final_links(settled, fates, ids, titles, title_of, into_existing, duplicate_of, alias,
                           (check or {}).get('merged_into') or {})
    links_moved, links_unlinked = [], []
    for _, slug, card in accepted:
        moved, gone = _relink_card(card, relinks, set(title_of.values()), titles[slug])
        links_moved += [{'card': slug, 'from': a, 'to': b} for a, b in moved]
        links_unlinked += [{'card': slug, 'link': x} for x in gone]
        if gone:
            card['_unlinked'] = gone
    by_target: dict = {}
    for slug, fate in fates.items():
        if fate['how'] == 'patched':
            by_target.setdefault(fate['target'], []).append(slug)
    for target, slugs in sorted(by_target.items()):
        # the patch an existing card takes is relinked once, here, and what it adds is read from the relinked patch:
        # the write checks (``taken``) and writes (``add``, ``add_flashcards``, a part of ``taken``) the same words, and
        # an item that repeats the card or another item once relinked goes in once
        fate = fates[slugs[0]]
        fix = _relinker(relinks, set(title_of.values()), title_of.get(target))
        taken = _relinked_patch(fate.get('patch') or check['patches'][target], fix)
        problem, add, add_f = _patch_fits(snapshot.cards[target], taken)
        # relinking only makes items alike, so the patch still fits; 'too_large' is left to pass: an item the card held
        # with a link that is now plain text is one more, and the card takes it rather than the job stopping
        if problem not in (None, 'too_large'):
            raise WriteStopped(f'{target} could take its patch before its links were pointed and not after: {problem}. '
                               'Nothing was written; this is a fault of the program, not of the answer')
        for slug in slugs:
            fates[slug].update(taken=taken, add=add, add_flashcards=add_f)
        links_moved += [{'into': target, 'cards': slugs, 'from': a, 'to': b} for a, b in dict.fromkeys(fix.moved)]
        links_unlinked += [{'into': target, 'cards': slugs, 'link': x} for x in dict.fromkeys(fix.gone)]
    changes: list[Change] = []
    updated: dict = {}
    produced = []
    notes = []
    # existing cards that take the merge check's patch; the cards that extend them are not written, and their
    # relations move to the existing card (old review cards and old propositions keep their words and numbers)
    merged_into_existing, patch_edges = [], []
    repeat_cards = {s: _final_card(entries, s, c) for o in settled for s, c in o.get('repeats', {}).items()}
    for target in sorted(by_target):
        # the merge check's patch; or, beside split cards, the review cards of the cards it says all of (D-24)
        old, slugs = snapshot.cards[target], by_target[target]
        patch, add, add_f = fates[slugs[0]]['taken'], fates[slugs[0]]['add'], fates[slugs[0]]['add_flashcards']
        swaps = {int(r['proposition'][1:]): r['by'] for r in patch.get('replace') or []}
        old_props = cardfmt.propositions_of(old.body)
        _, swapped = source_check.swap_flashcards(cardfmt.flashcards_of(old.body), patch.get('flashcard_replace') or [])
        first = len(old_props) + 1
        numbers = [f'P{n}' for n in range(first, first + len(add))] + [f'P{n}' for n in sorted(swaps)]
        present = {e.get('target') for e in old.fm.get('relations') or [] if isinstance(e, dict)}
        moved = []
        for slug in slugs:
            for r in repeat_cards[slug]['relations']:
                t = into_existing.get(alias.get(r['target'], r['target']), alias.get(r['target'], r['target']))
                tid = t if t in snapshot.cards else ids.get(t) or duplicate_of.get(t) or (
                    _written_slugs(packet).get(t) if _written_slugs(packet).get(t) in snapshot.cards else None)
                if tid is None:
                    skipped_relations.append({'from': target, 'type': r['type'], 'target_slug': t, 'reason': unwritten(t)})
                    continue
                if tid == target or tid in present or any(m['target'] == tid for m in moved):
                    continue
                closed = _closed_relation(packet['catalog'], r['type'], old.domain,
                                          snapshot.cards[tid].domain if tid in snapshot.cards else new_domains.get(tid))
                if closed:
                    skipped_relations.append({'from': target, 'type': r['type'], 'target_slug': t, 'reason': closed})
                    continue
                moved.append({k: v for k, v in (('type', r['type']), ('target', tid), ('note', r.get('note')),
                                                 ('tier', r.get('tier'))) if v})
        located = repeat_cards[slugs[0]]['source_quotes'][0]
        source_item = {'path': source_rel, 'sha256': source['sha256'], 'locator': located,
                       **({'propositions': numbers} if numbers else {})}
        try:  # the write checks that every item of the patch is in the card (decision D-25)
            raw2 = cardfmt.patch(old.raw, taken=patch, add_props=add, replace_props=swaps, add_flashcards=add_f,
                                 replace_flashcards={int(w['flashcard'][1:]): w['after'] for w in swapped},
                                 add_source=source_item, add_relations=moved, title_of=title_of.get, language=language)
        except cardfmt.PatchLost as exc:  # before anything is written: no item may go missing on the way
            raise WriteStopped(f'{target} would not hold what the merge check\'s patch gives it: {exc}. '
                              'Nothing was written; this is a fault of the program, not of the answer') from exc
        except cardfmt.PatchRefused as exc:
            raise Conflict(f'{target} could not take its patch: {exc}; run `sb job advance` again') from exc
        fm2, body2 = parse_note(raw2)
        changes.append(Change(old.path, old.sha256, raw2))
        updated[target] = replace(old, raw=raw2, fm=fm2, body=body2, sha256=digest(raw2))
        for m in moved:  # back links: on a new card of this job before it is written, else on the existing card
            inv = inverse.get(m['type'])
            if inv and m['target'] in by_id:
                rels[by_id[m['target']]].append(_inverse_edge(inv, target, m))
            elif inv:
                patch_edges.append((m['target'], _inverse_edge(inv, target, m)))
        merged_into_existing.append({
            'id': target, 'title': old.title, 'from': slugs, 'chunks': sorted({fates[x]['chunk'] for x in slugs}),
            'propositions': numbers[:len(add)],
            'replaced': [f'P{n}' for n in sorted(swaps)] + [w['flashcard'] for w in swapped],
            # the words before and after each swap, for the owner to look back on (D-26)
            'replacements': [{'proposition': f'P{n}', 'before': old_props[n - 1], 'after': swaps[n]}
                             for n in sorted(swaps)] + swapped,
            'flashcards': len(add_f), 'relations': moved,
            # what the merge check said the card holds already (H7 judge: each one checked against its Pn)
            'already': [{'text': a.get('text'), 'as': a.get('as')} for a in patch.get('already') or []
                        if isinstance(a, dict)]})
    # a review-card swap goes in with its whole patch; a patch that did not go in whole (split into narrower cards,
    # held, disputed) makes none of its swaps, and they are listed for the owner (D-26)
    replacements_not_made = list((check or {}).get('replacements_not_made') or [])
    for target, patch in sorted(((check or {}).get('patches') or {}).items()):
        if not patch.get('flashcard_replace') or (target in by_target and not fates[by_target[target][0]].get('patch')):
            continue
        why = next((f['reason'] for f in fates.values() if f['target'] == target and f['how'] in ('written', 'pending')
                    and f.get('reason')),
                   f'its patch would take {target} past {source_check.MAX_PROPOSITIONS} propositions or '
                   f'{source_check.MAX_FLASHCARDS} review cards, so what extends it was written as narrower cards '
                   'and it took no patch')
        replacements_not_made += [{'into': target, **swap, 'reason': why} for swap in patch['flashcard_replace']]
    for chunk, slug, card in accepted:
        cid, title = ids[slug], titles[slug]
        relations = [cardfmt.Relation(r['type'], r['target'], title_of.get(r['target'], ''), r.get('note'), r.get('tier'))
                     for r in rels[slug]]
        meta = {'id': cid, 'title': title, 'kind': card['kind'], 'domain': card['domain'],
                'pillar': card['pillar'], 'topic': card['topic'],
                'tags': ['review', f'flashcards/{card["domain"]}'],
                'aliases': list(dict.fromkeys(a for a in card.get('aliases') or [] if a != title)) or None,
                # a card other cards were merged into names where their carried propositions stand in the source
                'sources': [{'path': source_rel, 'sha256': source['sha256'], 'locator': card['source_quotes'][0]}] + [
                    {'path': source_rel, 'sha256': source['sha256'], 'locator': c['locator'],
                     **({'propositions': [f'P{i}' for i in c['propositions']]} if c['propositions'] else {})}
                    for c in card.get('_carried') or []],
                'relations': rels[slug], 'created': today,
                'status': 'needs-review' if _review_reasons(card) else 'confirmed',
                'review_reason': _review_reasons(card) or None}
        meta = {k: v for k, v in meta.items() if v is not None}
        raw = cardfmt.render_card(meta, card, relations, language=language,
                                  diagram=drawn['diagrams'].get(slug))
        fm, body = parse_note(raw)
        rel = lay.card_path(card['domain'], f'{title}.md')
        if read_bytes(root, rel) is not None:
            raise Conflict(f'A file already exists at {rel}')
        changes.append(Change(rel, None, raw))
        updated[cid] = Card(id=cid, title=title, domain=card['domain'], path=rel, raw=raw, fm=fm,
                            body=body, sha256=digest(raw))
        produced.append({'id': cid, 'slug': slug, 'title': title, 'domain': card['domain'], 'path': rel,
                         'chunk': chunk})
        notes.append({'id': cid, 'path': rel, 'title': title, 'domain': card['domain'],
                      'pillar': card['pillar'], 'topic': card['topic'], 'source_hash': source['sha256']})
    # inverse edges into existing cards: frontmatter list and related section; other bytes stay
    to_add: dict = {}
    for target, edge in extra_edges + patch_edges:
        to_add.setdefault(target, []).append(edge)
    for slug in rels:
        for r in rels[slug]:
            inv = inverse.get(r['type'])
            if inv and r['target'] in snapshot.cards:
                to_add.setdefault(r['target'], []).append(_inverse_edge(inv, ids[slug], r))
    maintenance = []
    for target in sorted(to_add):
        existing = updated.get(target) or snapshot.cards[target]  # a patched card takes its back links on top
        cur = existing.fm.get('relations')
        if cur is not None and not (isinstance(cur, list) and all(isinstance(e, dict) for e in cur)):
            maintenance.append({'target': target, 'reason': 'relations is not a list of mappings; inverse edge not added'})
            continue
        new_list = [dict(e) for e in (cur or [])]
        present = {(e.get('type'), e.get('target')) for e in new_list}
        added = [e for e in to_add[target] if (e['type'], e['target']) not in present]
        if not added:
            continue
        raw2 = append_list_items(existing.raw, 'relations', added)
        fm2, body2 = parse_note(raw2)
        if body2 != existing.body:
            maintenance.append({'target': target, 'reason': 'patch would change the body; inverse edge not added'})
            continue
        shown = [cardfmt.Relation(e['type'], e['target'], title_of.get(e['target'], ''), None, e.get('tier'))
                 for e in added]
        whole = [cardfmt.Relation(e['type'], e['target'], title_of.get(e['target'], ''), e.get('note'), e.get('tier'))
                 for e in fm2.get('relations') or [] if isinstance(e, dict) and isinstance(e.get('target'), str)]
        body3 = cardfmt.add_related(body2, shown, language=language, all_relations=whole)
        raw2 = raw2[:len(raw2) - len(body2.encode('utf-8'))] + body3.encode('utf-8')
        fm2, body2 = parse_note(raw2)
        changes = [c for c in changes if c.relative_path != existing.path]
        changes.append(Change(existing.path, snapshot.cards[target].sha256, raw2))
        updated[target] = replace(existing, raw=raw2, fm=fm2, body=body2, sha256=digest(raw2))
    # accepted new topics
    new_taxonomies = {}
    by_moc: dict = {}
    for domain, pillar, name in topics:
        moc = packet['catalog']['domains'][domain]['moc']
        by_moc.setdefault(moc, []).append((pillar, name))
    for moc, additions in by_moc.items():
        merged = _merge_topics(snapshot.taxonomies.get(moc) or {}, additions)
        rel = lay.taxonomy_path(moc)
        before = read_bytes(root, rel)
        changes.append(Change(rel, None if before is None else digest(before),
                              yaml.safe_dump(merged, allow_unicode=True, sort_keys=False,
                                             default_flow_style=False).encode('utf-8')))
        new_taxonomies[moc] = merged
    # source copy, manifest, inbox registry, notes registry
    before = read_bytes(root, source_rel)
    if before is not None and before != source_raw:
        raise Conflict(f'{source_rel} exists with different bytes')
    inbox_now = read_bytes(root, source['inbox_path']) if source['inbox_path'] else None
    move = settings['after_ingest'] == 'move' and inbox_now == source_raw
    if before is None or move:
        # an archive move must show the identical copy inside the same transaction
        changes.append(Change(source_rel, None if before is None else digest(before), source_raw))
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    manifest_rows = inbox._jsonl(root, lay.manifest)
    if not any(r.get('vault_path') == source_rel and r.get('sha256') == source['sha256'] for r in manifest_rows):
        changes.append(inbox.appended(root, lay.manifest, (json.dumps(
            {'original': source['filename'], 'vault_path': source_rel, 'sha256': source['sha256'],
             'collected_at': now, 'job_id': job_id}, ensure_ascii=False) + '\n').encode('utf-8')))
    # conflicts with existing cards: recorded for the owner, never merged (a row stays open until that card's text
    # changes); the digest is taken from the card as this write leaves it, back links included
    conflicts = []
    by_slug = {s: c for _, s, c in accepted}
    for o in settled:
        for row in o['conflicts']:
            other = updated.get(row['with']) or snapshot.cards.get(row['with'])
            if other is None:
                continue
            conflicts.append({'card': ids.get(row['slug']) if row['slug'] in by_slug else None, 'slug': row['slug'],
                              'title': titles.get(row['slug'], row['title']), 'with': row['with'],
                              'with_title': other.title, 'detail': row['detail'], 'by': row['by'],
                              'chunk': row['chunk']})
    # a patch changes an existing card's text, which would close the conflicts open on it: they are recorded
    # again, bound to its new text, in the same write (a merge does not settle a disagreement)
    rebound = [{**row, 'rebound_by': job_id} for row in open_conflicts(root, snapshot) if row.get('with') in by_target]
    rows = [{**c, 'job_id': job_id, 'source_sha256': source['sha256'], 'at': now,
             'with_digest': content_digest(updated.get(c['with']) or snapshot.cards[c['with']])} for c in conflicts]
    rows += [{**row, 'at': now, 'with_digest': content_digest(updated[row['with']])} for row in rebound]
    if rows:
        changes.append(inbox.appended(root, lay.conflicts, b''.join(inbox.registry_line(r) for r in rows)))
    # an extends card whose existing card does not take it is written next to it, for the owner to merge
    needs_review = [{'id': ids[s], 'title': titles[s], 'with': ids.get(c['_with'], into_existing.get(c['_with'],
                                                                                                     c['_with'])),
                     'reason': c['_reason'],
                     'not_merged': c.get('_not_merged')} for _, s, c in accepted if c.get('_with')]
    needs_taxonomy = [x for o in settled for x in o['needs_taxonomy']]
    status = 'partial' if (pending or needs_taxonomy) else 'applied'
    progress = None
    if packet.get('retry'):
        # a retry that writes no card and leaves as many items open as it was given would only repeat itself
        given = sum(len(items) for items in packet['retry']['pending'].values())
        left = len(pending) + len(needs_taxonomy)
        progress = {'open_before': given, 'open_after': left, 'cards_written': len(produced),
                    'progress': bool(produced) or left < given}
    stalled = status == 'partial' and progress is not None and not progress['progress']
    # a retry child runs after the parent may have moved the file: name where the source is now
    row_path = source['inbox_path'] if inbox_now == source_raw else (
        source_rel if packet.get('retry') else source['inbox_path'] or source['filename'])
    changes.append(inbox.appended(root, lay.inbox_registry, inbox.registry_line(
        {'sha256': source['sha256'], 'path': row_path, 'status': status,
         'job_id': job_id, 'cards': [p['id'] for p in produced], 'pending': len(pending) + len(needs_taxonomy),
         'at': now, **({'stalled': True} if stalled else {})})))
    reg_raw = read_bytes(root, lay.registry)
    if reg_raw is None:
        raise IngestError('notes_registry.json is missing')
    registry = json.loads(reg_raw)
    if not isinstance(registry, dict) or not isinstance(registry.get('notes'), list):
        raise IngestError('notes_registry.json must hold {"notes": [...]}')
    registry['notes'] = registry['notes'] + notes
    changes.append(Change(lay.registry, digest(reg_raw),
                          (json.dumps(registry, ensure_ascii=False, indent=2) + '\n').encode('utf-8')))
    # projections: affected MOCs and the entry page
    projected = replace(snapshot, cards={**snapshot.cards, **updated},
                        taxonomies={**snapshot.taxonomies, **new_taxonomies})
    affected = {c.domain for c in updated.values()} | {d for d, _, _ in topics}
    projection_metrics = {}
    if affected:
        projection = build_mocs(projected, affected_domains=affected)
        changes.extend(projection_changes(root, projection))
        projection_metrics = projection.metrics
    if move:
        changes.append(Change(source['inbox_path'], digest(inbox_now), None))
    if affected:
        # last, so its folder table counts the vault as this transaction leaves it
        written, removed = planned_paths(changes)
        open_now = {_conflict_key(r) for r in open_conflicts(root, projected)}
        open_now |= {_conflict_key({**c, 'source_sha256': source['sha256']}) for c in conflicts}  # this write's
        open_now |= {_conflict_key(r) for r in rebound}
        changes.extend(projection_changes(root, entry_projection(projected, current_day(), final_paths=written,
                                                                 removed_paths=removed, conflicts=len(open_now))))
    result = {'status': status, 'new_card_count': len(produced), 'produced': produced,
              'pending': pending, 'needs_taxonomy': needs_taxonomy,
              'duplicates': [d for o in settled for d in o['duplicates']],
              'out_of_scope': [x for o in settled for x in o['skipped']],
              'new_topics': [{'domain': d, 'pillar': p, 'name': n} for d, p, n in topics],
              'skipped_relations': skipped_relations, 'restored_relations': restored_relations,
              'maintenance': maintenance,
              'needs_review': needs_review, 'conflicts': conflicts,
              **({'source_check': _check_report(check, ids)} if check else {}),
              'source_check_refusals': _check_refusals(root, job_id),
              'source_record': {'sha256': source['sha256'], 'path': source_rel},
              'inbox_moved': move, 'projection_metrics': projection_metrics,
              'diagrams': [row for row in drawn['report'] if row['ref'] in {p['slug'] for p in produced}],
              'same_family': bool(state.get('same_family')),
              'outcomes': outcomes, 'decision_record': entries,
              'amended': [r for o in settled for r in o.get('amended') or []],
              # what the program repaired in the answers, writing no knowledge text (fence, ids, quotes, links)
              'program_repairs': program_repairs(root, state),
              'review_notes': [n for o in settled for n in o.get('notes') or []],
              # cards accepted with a note of a blocking kind, sent back by the program: which note, card, round
              'notes_turned': [t for o in settled for t in o.get('turned') or []] + _split_turned(root, job_id),
              # the reviewer's answers about its minor notes that may name a blocking problem (notecheck)
              'notes_checked': [a for o in settled for a in o.get('notes_checked') or []],
              # split cards sent back because a proposition says what the existing card says (D-22)
              'split_restated': _split_turned(root, job_id, 'matches'),
              'refused_topics': [t for o in settled for t in o.get('refused_topics') or []],
              'merged_into_existing': merged_into_existing,
              'replacements_not_made': replacements_not_made,
              # [[links]] the program pointed at the title a card was written or merged under, and links to cards
              # that were not written, left as plain text (their cards are written needs-review: dangling_link)
              'links_moved': links_moved, 'links_unlinked': links_unlinked,
              'split': [{'slug': f['card']['slug'], 'id': ids.get(f['card']['slug']), 'from': s, 'of': ids.get(f['target'], f['target']),
                         'slot': f['slot']} for s, f in fates.items() if f['how'] == 'split'],
              'patch_set': {s: r['how'] for s, r in recorded_fates.items() if not r.get('of_source')},
              'merge_set': {s: r['how'] for s, r in recorded_fates.items() if r.get('of_source')},
              # D-20: the merge check's last answer, kept patch by patch: the cards that took it, the ones held
              'patches_held': [{'target': t, 'cards': h['cards'], 'problems': h['problems']}
                               for t, h in sorted(((check or {}).get('held') or {}).items())]
              + [{'target': ids.get(h['into'], into_existing.get(h['into'], h['into'])), 'cards': [s],
                  'problems': h['problems'], 'carry': True}
                 for s, h in sorted(((check or {}).get('held_carries') or {}).items())]
              + [{'target': t, 'cards': row['cards'], 'problems': [fates[row['cards'][0]]['reason']],
                  'review_cards_only': True}
                 for t, row in sorted((state.get('split_qa_only') or {}).items())
                 if fates.get(row['cards'][0], {}).get('held')]}
    if progress is not None:
        result['retry_progress'] = progress
    if stalled:
        result['message'] = (f'This retry wrote no card and left {progress["open_after"]} of the '
                             f'{progress["open_before"]} items it was given open, so another retry would likely '
                             'repeat it. ' + _STALLED_ADVICE.format(job=job_id))
    elif status == 'partial':
        result['hint'] = f'sb ingest --retry {job_id}'
    try:
        done = jobs.finish_transaction(root, job_id, result, changes, checks=snapshot_checks(snapshot, changes))
    except Conflict as exc:
        if jobs.load(root, job_id).get('commit_intent'):
            jobs.release_intent(root, job_id, reason=f'{exc}; run `sb job advance` to rebuild the write plan')
        raise
    return _maybe_commit(root, job_id, done)


def _repeat_fates(root: Path, state: dict, packet: dict, settled: list[dict], check: dict | None,
                  snapshot) -> dict:
    """What becomes of each card of the patch set (``repeats``): ``{slug: {"how", "target", ...}}``.

    ``patched``: its existing card takes the merge check's patch (the card is
    not written). ``split``: the patch would make that card too long; the
    split slot's accepted card is written instead (``card``). ``written``: the
    existing card cannot take a patch (not in sb format, gone, or the job does
    not merge: ``merge_into_existing`` off), so the card is written next to it,
    needs-review. ``pending``: disputed by the merge check's last round, or its
    split card was not accepted.
    """
    patches = (check or {}).get('patches') or {}
    disputes = (check or {}).get('disputes') or {}
    held = (check or {}).get('held') or {}
    plan = {i['slug']: i for i in state.get('split_plan') or []}
    covered = set(state.get('split_covered') or [])
    qa_only = {s: (t, row) for t, row in (state.get('split_qa_only') or {}).items() for s in row['cards']}
    out = {}
    for o in settled:
        for slug, card in (o.get('repeats') or {}).items():
            target = card['_with']
            fate = {'target': target, 'chunk': o['chunk']}
            old = snapshot.cards.get(target)
            if card.get('_given_up'):  # its judgement again, after a dispute, kept failing the program's checks
                fate.update(how='pending', reason=card['_given_up'])
            elif slug in disputes:
                fate.update(how='pending', reason=f'the merge check could not add it to {target}: {disputes[slug]}')
            elif target in held:  # D-20: written next to its card, needs-review, with what the program found
                fate.update(how='written', held=True,
                            reason=f'the merge check\'s patch for {target} did not pass the program checks in its '
                                   f'last answer: {"; ".join(held[target]["problems"])}')
            elif not _merging(packet) or target not in patches or old is None:
                skipped = (check or {}).get('status') == 'skipped'
                why = ('its existing card no longer exists' if old is None else
                       'this job does not add to existing cards (merge_into_existing is off)' if not _merging(packet)
                       else f'the merge check was given up, so nothing went into {target}: {check.get("reason")}'
                       if skipped and cardfmt.patchable(old.body) is None
                       else f'its existing card cannot take a patch: {cardfmt.patchable(old.body) or "no patch"}')
                fate.update(how='written', reason=why)
            elif slug in covered:  # D-22: its existing card says all it would add (already); nothing to write
                fate.update(how='covered', reason=f'{target} already says each of its propositions')
            elif slug in qa_only:  # D-24: only review cards left: they go in as a patch of their own, or are held
                partial = {'flashcards': qa_only[slug][1]['flashcards'], 'already': qa_only[slug][1]['already']}
                problem, add, add_f = _patch_fits(old, partial)
                fate.update(how='patched', add=add, add_flashcards=add_f, patch=partial) if problem is None else \
                    fate.update(how='written', held=True,
                                reason=f'{target} says each of its propositions, but its review cards would take '
                                       f'{target} past {source_check.MAX_FLASHCARDS} review cards'
                                       if problem == 'too_large' else f'its existing card cannot take a patch: {problem}')
            elif slug in plan:
                done = split_outcome(root, state, packet, plan[slug])
                fate.update(how='split', card=done['card'], slot=plan[slug]['slot']) if done['card'] else \
                    fate.update(how='pending', reason=done['pending'] or 'its split card is not finished')
            else:
                problem, add, add_f = _patch_fits(old, patches[target])
                fate.update(how='patched', add=add, add_flashcards=add_f) if problem is None else \
                    fate.update(how='written', reason=f'its existing card cannot take more: {problem}')
            out[slug] = fate
        for slug, card in (o.get('split_merges') or {}).items():  # a card of the source too large to merge into
            fate = {'target': card['_with'], 'chunk': o['chunk'], 'of_source': True}
            done = split_outcome(root, state, packet, plan[slug]) if slug in plan else \
                {'card': None, 'pending': 'its split slot was never planned'}
            fate.update(how='split', card=done['card'], slot=plan[slug]['slot']) if done['card'] else \
                fate.update(how='pending', reason=done['pending'] or 'its split card is not finished')
            out[slug] = fate
    return out


#: The outcome a fate of :func:`_repeat_fates` records: written next to its existing card, held by it already, or
#: waiting for the owner; a card patched or split keeps the outcome its levels give.
FATE_OUTCOMES = {'written': 'write', 'covered': 'drop', 'pending': 'pending'}


def _fate_record(fate: dict, levels_say: str) -> dict:
    """The ``outcome.program`` value of one fate: the outcome (``levels_say`` unless :data:`FATE_OUTCOMES` names
    another), what became of the card (``how``), its existing card or card of the source, its chunk, and why."""
    return {'outcome': FATE_OUTCOMES.get(fate['how'], levels_say), 'how': fate['how'], 'target': fate['target'],
            'chunk': fate['chunk'], **({'of_source': True} if fate.get('of_source') else {}),
            **({'reason': fate['reason']} if fate.get('reason') else {})}


def _final_card(entries: list, slug: str, card: dict) -> dict:
    """``card`` with the place and relations the decision record gives it (``decisions.fold``), not the producer's."""
    place = decisions.fold(entries, 'classification', slug)
    if not all(place.get(k) for k in ('domain', 'pillar', 'topic')):
        raise Conflict(f'{slug}: the decision record holds no place in the taxonomy for it')
    return {**card, 'domain': place['domain'], 'pillar': place['pillar'], 'topic': place['topic'],
            'relations': decisions.fold(entries, 'relations', slug)}


def _review_reasons(card: dict) -> list[str]:
    """Why a card is written ``needs-review``: the source is unclear (the producer's flag, accepted by the reviewer),
    a possible repeat of an existing card that could not take it (the outcome table)."""
    return [reason for reason, why in (('source_unclear', card.get('status') == 'needs-review'),
                                       ('possible_repeat', bool(card.get('_with'))),
                                       ('dangling_link', bool(card.get('_unlinked')))) if why]


def _final_links(settled: list[dict], fates: dict, ids: dict, titles: dict, title_of: dict, into_existing: dict,
                 duplicate_of: dict, alias: dict, merged: dict) -> dict:
    """``{title a card of this source was proposed under: the title it is in the vault under now, or None}``: written
    (perhaps under a title made unique), gone into an existing card, judged the same as one, rewritten as a split
    card, merged into another card of the source; None when it was not written at all."""
    proposed: dict = {}
    for o in settled:
        for s, c in [*o['accepted'].items(), *(o.get('repeats') or {}).items(), *(o.get('split_merges') or {}).items()]:
            proposed.setdefault(s, c.get('title'))
        for d in o.get('duplicates') or []:
            proposed.setdefault(d.get('slug'), d.get('title'))
        for p in o.get('pending') or []:
            proposed.setdefault(p.get('slug'), p.get('title'))

    def final(slug, depth=0):
        if slug in ids:
            return titles[slug]
        if slug in alias and alias[slug] in ids:
            return titles[alias[slug]]
        if slug in into_existing:
            return title_of.get(into_existing[slug])
        if slug in duplicate_of:
            return title_of.get(duplicate_of[slug])
        if slug in merged and depth < 5:
            return final(merged[slug], depth + 1)
        return None
    return {t: final(s) for s, t in proposed.items() if isinstance(t, str) and t}


def _relinker(relinks: dict, known: set, own: str | None):
    """A function that points a text's ``[[links]]`` at final titles (:func:`_final_links`); it keeps the links it
    moved (``moved``) and those it left as plain text (``gone``)."""
    from .source_check import _WIKILINK

    def sub(m):
        target = m.group(1).strip()
        if target in known and target != own:
            return m.group(0)
        to = relinks.get(target) if target not in known else target
        if to is None:
            fix.gone.append(target)
        elif to != own:
            fix.moved.append((target, to))
            return f'[[{to}{m.group(2)}{m.group(3)}]]'
        elif target != own:  # it names, under another title, the card it stands in: plain text, moved to that card
            fix.moved.append((target, to))
        return m.group(3)[1:] if m.group(3) else target  # plain text: the alias, or the title

    def fix(text):
        return _WIKILINK.sub(sub, text) if isinstance(text, str) else text
    fix.moved, fix.gone = [], []
    return fix


def _relinked_qa(card, fix):
    """A review card (``{q, a}``, or a swap ``{flashcard, q, a}``) with its links pointed by ``fix``."""
    return {**card, 'q': fix(card.get('q')), 'a': fix(card.get('a'))} if isinstance(card, dict) else card


def _relinked_patch(patch: dict, fix) -> dict:
    """``patch`` (the merge check's patch for an existing card) with the links of every item it writes pointed by
    ``fix`` (:func:`_relinker`): its propositions, review cards, the new words of a replaced proposition and the
    new question and answer of a swapped review card."""
    out = dict(patch)
    if isinstance(patch.get('propositions'), list):
        out['propositions'] = [fix(x) for x in patch['propositions']]
    for key in ('flashcards', 'flashcard_replace'):
        if isinstance(patch.get(key), list):
            out[key] = [_relinked_qa(f, fix) for f in patch[key]]
    if isinstance(patch.get('replace'), list):
        out['replace'] = [{**r, 'by': fix(r.get('by'))} if isinstance(r, dict) else r for r in patch['replace']]
    return out


def _relink_card(card: dict, relinks: dict, known: set, own: str) -> tuple[list, list]:
    """Point the links in ``card``'s text at final titles, in place; returns (moved, gone)."""
    fix = _relinker(relinks, known, own)
    for key in ('essence', 'intuition'):
        if key in card:
            card[key] = fix(card[key])
    for key in ('propositions', 'confusions'):
        if isinstance(card.get(key), list):
            card[key] = [fix(x) for x in card[key]]
    if isinstance(card.get('flashcards'), list):
        card['flashcards'] = [_relinked_qa(f, fix) for f in card['flashcards']]
    return fix.moved, list(dict.fromkeys(fix.gone))


def _outcomes_of(entries: list, chunks: list[dict], routed: list[dict], check: dict | None) -> dict:
    """``{slug: outcome}`` of every accepted card from the decision record (``overlap.outcome``), checked against
    where the cards went: one outcome per card, the same one the write follows."""
    ran = bool(check) and check.get('status') == 'checked'
    merged = (check or {}).get('merged_into') or {}
    written = {s for o in routed for s in o['accepted']}
    patched = {s for o in routed for s in o.get('repeats', {})}
    dropped = {d['slug'] for o in routed for d in o['duplicates'] if d.get('by') == 'reviewer'}
    out = {}
    for o in chunks:
        for slug in o['accepted']:
            try:
                found = overlap.outcome(entries, slug, checked=ran)
            except overlap.OutcomeError as exc:
                raise Conflict(str(exc)) from exc
            went = 'write' if slug in written else 'patch' if slug in patched else 'drop' if slug in dropped else \
                ('merge_carry', 'merge') if slug in merged else None
            if found['outcome'] != went and not (isinstance(went, tuple) and found['outcome'] in went):
                raise Conflict(f'{slug}: the decision record says {found["outcome"]}, the write would {went}')
            out[slug] = found['outcome']
    return out


def _check_refusals(root: Path, job_id: str) -> list[dict]:
    """Every merge-check answer the program sent back, with its reasons (API runs that failed validation, and
    host submissions refused), in order: ``[{slot, run_id, message}]``."""
    out = []
    for run in jobs.list_runs(root, job_id):
        if run.get('role') != 'reviewer' or not source_check.is_slot(run.get('chunk')):
            continue
        messages = list(run.get('rejected') or [])
        error = run.get('error') or {}
        if run.get('status') == 'failed' and error.get('code') == INVALID_OUTPUT and \
                str(error.get('message') or '')[:4000] not in messages:  # not JSON, or refused at submit (once)
            messages.append(str(error.get('message') or ''))
        out += [{'slot': run['chunk'], 'run_id': run['run_id'], 'message': m} for m in messages]
    return out


def _check_report(check: dict, ids: dict) -> dict:
    """The source check's report for the job result, with written cards named by id."""
    if check.get('status') != 'checked':
        return check
    name = lambda slug: ids.get(slug, slug)  # noqa: E731
    return {'status': 'checked', 'cards': check['cards'],
            'merged': [{**m, 'into': name(m['into'])} for m in check['merged']],
            'rewritten_links': check.get('rewritten_links', 0),
            'added_links': [{**r, 'from': name(r['from']), 'target': name(r['target'])} for r in check['added_links']],
            'alone': [{**a, 'id': name(a['slug'])} for a in check['alone']],
            'refiled_topics': check['refiled_topics'],
            'moved_relations': [{**r, 'from': name(r['from']), 'target': name(r['target'])}
                                for r in check['moved_relations']]}


def _maybe_commit(root: Path, job_id: str, state: dict) -> dict:
    # a git commit runs after the write, outside its transaction: read live (config.runtime), not frozen
    if not config.runtime().git_commit or state.get('status') not in {'applied', 'partial'} or state.get('commit'):
        return state
    from ..gitcommit import commit_paths
    try:
        commit = commit_paths(root, state.get('applied_paths', []),
                              f'secondbrain: ingest {job_id} ({state.get("new_card_count", 0)} cards)')
        return jobs.update(root, job_id, commit=commit)
    except (OSError, ValueError) as exc:
        return jobs.update(root, job_id, commit={'status': 'commit_failed', 'error': str(exc)})


# --------------------------------------------------------------------------
# Host mode: explicit packet for one role/chunk
# --------------------------------------------------------------------------
def packet_for(root: Path, job_id: str, *, role: str, chunk: str | None, executor) -> dict:
    """Create the next run for ``role`` (optionally a specific chunk) via ``executor``. It reads the answers the job
    took as ``advance`` does, behind the same guard (:func:`secondbrain.jobs.guarded`)."""
    def once():
        with _frozen_run(root, jobs.read_input(root, job_id)):
            return _packet_for(root, job_id, role=role, chunk=chunk, executor=executor)
    return jobs.guarded(root, job_id, once)


def _packet_for(root: Path, job_id: str, *, role: str, chunk: str | None, executor) -> dict:
    from ..runner import AwaitingSubmission
    with job_lock(root, job_id):
        state = jobs.load(root, job_id)
        packet = jobs.read_input(root, job_id)
        for c in effective_chunks(packet, state):
            with jobs.reading('outcome', _newest(c['chunk_id'])):
                out = chunk_outcome(root, state, packet, c['chunk_id'])
            if out['settled'] or out['next'] is None:
                continue
            if chunk and _base(chunk) != c['chunk_id'] and out['next'][1] != chunk:
                continue
            wants = 'reviewer' if out['next'][0] in ('review', 'notecheck') else 'producer'
            if wants != role:
                continue
            try:
                _step(root, job_id, out['next'], executor, executor)
            except AwaitingSubmission as waiting:
                return {**waiting.handoff, 'chunk': out['next'][1], 'step': out['next'][0]}
            if ((jobs.load(root, job_id).get('chunks') or {}).get(c['chunk_id']) or {}).get('split'):
                raise ValueError(f'{c["chunk_id"]} was split in halves (its named cards are too many for one '
                                 'reviewer packet); ask for the producer packets of its halves')
            raise ValueError('The executor finished the step directly; use `sb job advance` instead')
        settled = []
        for c in effective_chunks(packet, state):
            with jobs.reading('outcome', _newest(c['chunk_id'])):
                settled.append(chunk_outcome(root, state, packet, c['chunk_id']))
        if role == 'reviewer' and all(o['settled'] for o in settled) and (
                not chunk or source_check.is_slot(chunk) or _is_rejudge(chunk) or split.is_slot(chunk)):
            with jobs.reading('merge_check'):
                handoffs = _source_check_stage(root, job_id, packet, settled, executor)
            if not handoffs:
                with jobs.reading('split'):
                    handoffs = _split_stage(root, job_id, packet, settled, executor, executor, role='reviewer')
            if handoffs:
                return handoffs[0]
        if role == 'producer' and all(o['settled'] for o in settled) and (not chunk or split.is_slot(chunk)):
            with jobs.reading('split'):
                handoffs = _split_stage(root, job_id, packet, settled, executor, executor, role='producer',
                                        only=chunk)
            if handoffs:
                return handoffs[0]
        if role == 'producer' and all(o['settled'] for o in settled) and (not chunk or card_diagrams.is_slot(chunk)):
            with jobs.reading('checked'):
                checked, _ = _checked(root, jobs.load(root, job_id), packet, settled)
            with jobs.reading('diagrams'):
                _, handoffs = _diagram_stage(root, job_id, packet, checked, executor, only=chunk)
            if handoffs:
                return handoffs[0]
        raise ValueError(f'No {role} step is due' + (f' for {chunk}' if chunk else ''))

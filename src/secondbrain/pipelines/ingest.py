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
import json
import re
import unicodedata
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .. import cards as cardfmt
from .. import config, inbox, jobs, schemas
from .. import layout as layouts
from ..catalog import category_pairs, relation_meanings, taxonomy_packet, validate_catalog
from ..classification import projection_changes, snapshot_checks, taxonomy_fingerprint, writable_snapshot
from ..evidence import check_verbatim
from ..executors.base import CHUNK_TOO_LARGE, INVALID_OUTPUT, ExecutorFailure
from ..locks import job_lock
from ..store import Change, Conflict, digest, read_bytes
from ..vault import Card, append_list_items, parse_note, read_snapshot
from . import card_diagrams
from .chunking import chunk_text
from .routing import essence, route

KIND = 'ingest'
METHOD = 'ingest/3'
TEXT_SUFFIXES = {'.md', '.markdown', '.txt', '.text', '.rst', '.org', ''}
_SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,59}$')
DISPOSITIONS = {'card', 'duplicate', 'out_of_scope', 'needs_taxonomy'}


class IngestError(ValueError):
    """Input or validation problem in the ingest chain."""


# --------------------------------------------------------------------------
# S1 read source, S2 chunk, prepare (freeze input)
# --------------------------------------------------------------------------
def _safe_filename(name: str) -> str:
    name = (name or '').strip()
    if not name or name in {'.', '..'} or '/' in name or '\\' in name or name.startswith('.') \
            or '\x00' in name:
        return 'source.txt'
    return name


def read_source(path: Path, settings: config.Settings) -> tuple[bytes, str]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise IngestError(f'Source must be a regular file (no symlink): {path}')
    size = path.stat().st_size
    if size > settings.ingest.max_source_bytes:
        raise IngestError(f'Source is larger than {settings.ingest.max_source_bytes} bytes; '
                          'split it instead of truncating it')
    raw = path.read_bytes()
    try:
        return raw, raw.decode('utf-8')
    except UnicodeDecodeError:
        pass
    if settings.ingest.converter == 'markitdown':
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


def prepare(root: Path, source: Path, *, settings: config.Settings | None = None) -> dict:
    """Freeze one source into its ingest job (or report it as already done)."""
    settings = settings or config.current()
    if settings.taxonomy_extra_reviewers:
        raise IngestError('[taxonomy] extra_reviewers > 0 is not supported yet; set it to 0')
    root = Path(root)
    lay = layouts.of(root)
    raw, text = read_source(source, settings)
    sha = digest(raw)
    job_id = inbox.job_id_for(sha)
    done = inbox.done_index(root).get(sha)
    if done and done['status'] in {'applied', 'done', 'partial'}:
        out = {'status': 'unchanged', 'result': done['status'], 'job_id': done.get('job_id') or job_id,
               'source_sha256': sha, 'via': done['via']}
        if done['status'] == 'partial':
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
        return {**state, 'resumed': True}
    source_path = Path(source).absolute()
    in_inbox = source_path.parent.resolve() == (root / lay.inbox).resolve()
    inbox_path = f'{lay.inbox}/{source_path.name}' if in_inbox else None
    snapshot = read_snapshot(root)
    writable_snapshot(snapshot)
    if not snapshot.catalog:
        raise IngestError('The vault has no catalog; run `sb init` first')
    validate_catalog(snapshot.catalog)
    _require_some_taxonomy(snapshot, lay)
    chunks = []
    for chunk in chunk_text(text, settings.ingest.chunk_chars):
        routed = route(root, snapshot, text[chunk['start']:chunk['end']], domains=settings.ingest.candidate_domains)
        chunks.append({**chunk, 'candidate_domains': routed['domains'], 'neighbors': routed['neighbors'],
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
                     'id_prefix': settings.ingest.id_prefix, 'language': settings.card_language,
                     'diagrams': settings.card_diagrams,
                     'after_ingest': settings.ingest.after_ingest},
        'snapshot_fingerprint': snapshot.fingerprint,
    }
    state = jobs.create(root, job_id, KIND, packet)
    return {**state, 'source_sha256': sha, 'chunks': [c['chunk_id'] for c in chunks],
            'inbox_path': inbox_path}


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
    now; its packets carry the open items with the reviewer's reasons and the
    cards of each chunk that are already written. It has its own transaction,
    so it can be rolled back on its own. Calling it again on the same parent
    returns the existing child. A child that was rolled back, reset with
    `sb job reset`, or made for an earlier run of the parent (the parent was
    rolled back and the source ingested again) does not block a new child:
    it is set aside like `sb job reset` does, and the new child takes its id.
    """
    settings = settings or config.current()
    root = Path(root)
    parent = jobs.load(root, job_id)
    if parent['kind'] != KIND or parent['status'] != 'partial':
        raise IngestError(f'Only a partial ingest job can be retried; {job_id} is {parent["status"]}')
    if parent.get('retried_by'):
        live = _live_child(root, job_id, parent, parent['retried_by'])
        if live is not None:
            return _resumed_child(live)
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
    snapshot = read_snapshot(root)
    writable_snapshot(snapshot)
    validate_catalog(snapshot.catalog)
    chunks = []
    for cid in [c['chunk_id'] for c in effective_chunks(old, parent) if c['chunk_id'] in open_items]:
        chunk = {k: v for k, v in _chunk(old, cid).items() if k not in {'candidate_domains', 'neighbors', 'routing'}}
        routed = route(root, snapshot, old['source']['text'][chunk['start']:chunk['end']],
                       domains=settings.ingest.candidate_domains)
        # the domains the parent's reviewers pointed to, and where the open cards last were
        named = [d for d in _reviewed_domains(root, parent, old, cid, open_items[cid])
                 if d in snapshot.catalog['domains'] and d not in routed['domains']]
        chunks.append({**chunk, 'candidate_domains': routed['domains'] + named, 'neighbors': routed['neighbors'],
                       'routing': {'votes': routed['votes'], 'filled': routed['filled'],
                                   'passages': routed['passages'], 'named_by_review': named}})
    for card_list in written.values():
        for card in card_list:
            if card['id'] in snapshot.cards:
                card['essence'] = essence(snapshot.cards[card['id']])
    domains = sorted(snapshot.catalog['domains'])
    packet = {**old, 'chunks': chunks, 'catalog': snapshot.catalog,
              'taxonomy': taxonomy_packet(snapshot, domains), 'taxonomy_sha256': taxonomy_fingerprint(snapshot),
              'topic_counts': _topic_counts(snapshot), 'snapshot_fingerprint': snapshot.fingerprint,
              'retry': {'of': job_id, 'root': first, 'round': number,
                        'pending': {cid: open_items[cid] for cid in open_items},
                        'written': written}}
    child = f'{first}-retry{number}'
    live = _live_child(root, job_id, parent, child)
    if live is not None:  # made before an interruption, before the parent recorded it
        jobs._internal_update(root, job_id, retried_by=child)
        return _resumed_child(live)
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
            named += _domains_named(packet, _findings(jobs.artifact(root, state, 'review', slot)))
        if info.get('candidate'):
            for card in jobs.artifact(root, state, 'candidate', slot).get('cards', []):
                if isinstance(card, dict) and card.get('slug') in open_slugs:
                    last[card['slug']] = card.get('domain')
    return [d for d in dict.fromkeys(named + list(last.values())) if isinstance(d, str)]


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


def _resumed_child(state: dict) -> dict:
    out = {**state, 'resumed': True}
    if state['status'] == 'partial':
        out['hint'] = f'the retry child is partial too: sb ingest --retry {state["job_id"]}'
    return out


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


def _legal_listing(packet: dict, domain: str) -> str:
    """``Pillar: topic, topic; Pillar: topic`` for error messages a repair round can act on."""
    tax = _taxonomy_of(packet, domain)
    rows = [f'{p["name"]}: {", ".join(t for t in p.get("topics", []) if isinstance(t, str))}'
            for p in tax.get('pillars', []) if isinstance(p, dict)]
    return '; '.join(rows) or '(no curated topics yet; propose one in new_topics)'


def _relation_types(catalog: dict) -> list:
    from ..catalog import relation_types
    return relation_types(catalog)


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


def _other_cards(root: Path, state: dict, packet: dict, base: str) -> list[dict]:
    """Cards proposed for the other chunks of this source (latest per slug)."""
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
                                          'chunk': chunk['chunk_id']}
    return list(out.values())


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
           'relation_types': relation_meanings(packet['catalog']),
           'neighbors': chunk['neighbors'],
           'other_chunks_cards': _other_cards(root, state, packet, base),
           'rules': {'new_topics_per_source': packet['settings']['new_topics_per_source'],
                     'flashcards_per_card': [2, 4], 'id_prefix': packet['settings']['id_prefix']}}
    if packet.get('retry'):
        out['retry'] = {'findings': packet['retry']['pending'].get(base, []),
                        'written_cards': packet['retry']['written'].get(base, [])}
    if rnd:
        prev_slot = _slot_name(base, rnd - 1)
        previous = jobs.artifact(root, state, 'candidate', prev_slot)
        findings = _findings(jobs.artifact(root, state, 'review', prev_slot))
        out['previous_candidate'] = previous
        out['review_findings'] = findings
        out['kept_slugs'] = sorted(_accepted_so_far(root, state, base, rnd))
        wanted = list(chunk['candidate_domains'])
        for domain in _domains_named(packet, findings) + [c.get('domain') for c in previous.get('cards', [])]:
            if domain in packet['catalog']['domains'] and domain not in wanted:
                wanted.append(domain)
        out['candidate_taxonomies'] = _candidate_taxonomies(packet, wanted)
    return out


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


def _neighbors_for(root: Path, snapshot, card: dict) -> list[dict]:
    from ..index import search
    query = ' '.join([str(card.get('title') or ''), str(card.get('essence') or '')]
                     + [str(p) for p in (card.get('propositions') or [])[:3]])
    hits = search(root, snapshot, query, 3) if query.strip() else []
    return [{'id': h['id'], 'title': h['title'], 'domain': h['domain'],
             'body': snapshot.cards[h['id']].body} for h in hits]


def review_packet(root: Path, state: dict, packet: dict, slot: str) -> dict:
    from ..runner import withhold_reasons
    base, rnd = _base(slot), _round(slot)
    candidate = jobs.artifact(root, state, 'candidate', slot)
    shown = withhold_reasons(candidate) if config.review_policy().withhold_producer_reasons else candidate
    snapshot = read_snapshot(root)
    domains = sorted({c['domain'] for c in candidate.get('cards', []) if c.get('domain') in packet['catalog']['domains']}
                     | {t['domain'] for t in candidate.get('new_topics', []) if t.get('domain') in packet['catalog']['domains']})
    out = {'chunk': {'id': base, 'text': _chunk_text(packet, base)},
           'candidate': shown,
           'near_duplicates': {c['slug']: _neighbors_for(root, snapshot, c)
                               for c in candidate.get('cards', []) if isinstance(c, dict) and c.get('slug')},
           'taxonomies': _candidate_taxonomies(packet, domains),
           'domains': [{'slug': d, 'question': m.get('question')}
                       for d, m in sorted(packet['catalog']['domains'].items())],
           'relation_types': relation_meanings(packet['catalog']),
           'other_chunks_cards': _other_cards(root, state, packet, base)}
    if rnd:
        kept = _accepted_so_far(root, state, base, rnd)
        out['already_accepted_cards'] = [withhold_reasons(c) for c in kept.values()]
    if packet.get('retry'):
        out['already_written_cards'] = packet['retry']['written'].get(base, [])
    return out


# --------------------------------------------------------------------------
# S4 validation (pure on the frozen packet + current vault titles/ids)
# --------------------------------------------------------------------------
def _norm(name: str) -> str:
    text = unicodedata.normalize('NFKC', name).casefold()
    return ''.join(ch for ch in text if ch.isalnum())


def _text_fields(card: dict) -> list[str]:
    out = [card.get('title'), card.get('essence'), card.get('intuition')]
    out += list(card.get('propositions') or []) + list(card.get('confusions') or [])
    for f in card.get('flashcards') or []:
        if isinstance(f, dict):
            out += [f.get('q'), f.get('a')]
    out += [r.get('note') for r in card.get('relations') or [] if isinstance(r, dict)]
    return [x for x in out if isinstance(x, str)]


def _vault_ids_titles(root) -> tuple[set, set]:
    snap = read_snapshot(root)
    return set(snap.cards), {c.title.casefold() for c in snap.cards.values() if c.title}


def validate_candidate(packet: dict, payload: dict, *, slot: str, other_cards: list[dict],
                       kept_slugs: set, existing_ids: set, existing_titles: set,
                       known_topics: set = frozenset()) -> dict:
    """Raise :class:`IngestError` listing every problem; return the payload."""
    errors: list[str] = []
    base = _base(slot)
    text = _chunk_text(packet, base)
    catalog = packet['catalog']
    prefix = packet['settings']['id_prefix']
    bare_id = re.compile(re.escape(prefix) + r'[A-Za-z0-9]') if prefix else None
    relation_types = set(_relation_types(catalog))
    if not isinstance(payload, dict):
        raise IngestError('candidate must be a JSON object')
    cards = payload.get('cards')
    topics = payload.get('new_topics', [])
    coverage = payload.get('coverage')
    if not isinstance(cards, list) or not isinstance(topics, list):
        raise IngestError('cards and new_topics must be lists')
    if not isinstance(coverage, list) or not coverage:
        raise IngestError('coverage must list how every part of the chunk is handled')
    other_slugs = {c['slug'] for c in other_cards}
    other_titles = {str(c.get('title') or '').casefold() for c in other_cards}
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
        n = _norm(name)
        clash = [x for x in existing if _norm(x) == n or (len(n) >= 2 and len(_norm(x)) >= 2 and
                                                          (n in _norm(x) or _norm(x) in n))]
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
        elif slug in slugs or slug in other_slugs or slug in kept_slugs:
            errors.append(f'{where}.slug {slug!r} is already used in this source')
        else:
            slugs.add(slug)
        where = f'cards[{slug or i}]'
        problem = cardfmt.title_problem(c.get('title'))
        if problem:
            errors.append(f'{where}: {problem}')
        else:
            folded = c['title'].casefold()
            if folded in titles or folded in other_titles:
                errors.append(f'{where}: title repeats another card of this source')
            elif folded in existing_titles:
                errors.append(f'{where}: a card titled {c["title"]!r} already exists; '
                              'if it is the same knowledge, mark the coverage row as duplicate')
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
        for r in rels:
            if not isinstance(r, dict) or r.get('type') not in relation_types:
                errors.append(f'{where}.relations: type must be one of {sorted(relation_types)}')
                continue
            target = r.get('target')
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
        if disp == 'duplicate':
            dup = row.get('duplicate_of') or []
            if not dup or any(x not in existing_ids for x in dup):
                errors.append(f'{where}: duplicate rows must name existing card ids in duplicate_of')
        if disp == 'needs_taxonomy' and (not isinstance(row.get('suggested_pillar'), str) or not row['suggested_pillar'].strip()):
            errors.append(f'{where}: needs_taxonomy rows must give suggested_pillar')
        if not isinstance(row.get('reason'), str) or not row['reason'].strip():
            errors.append(f'{where}.reason must be a non-empty string')
    missing = slugs - covered
    if missing:
        errors.append(f'every card needs a coverage row; missing {sorted(missing)}')
    if errors:
        raise IngestError('Candidate rejected by validation:\n- ' + '\n- '.join(errors))
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


def validate_review(candidate: dict, review: dict, existing_ids: set, domains=()) -> dict:
    errors = []
    if not isinstance(review, dict):
        raise IngestError('review must be a JSON object')
    slugs = [c['slug'] for c in candidate.get('cards', [])]
    rows = review.get('cards')
    if not isinstance(rows, list):
        raise IngestError('review.cards must be a list')
    seen = []
    for row in rows:
        if not isinstance(row, dict) or row.get('slug') not in slugs:
            errors.append(f'review row {row!r} does not name a candidate slug')
            continue
        seen.append(row['slug'])
        if row.get('decision') not in {'accept', 'revise', 'duplicate'}:
            errors.append(f'{row["slug"]}: decision must be accept, revise or duplicate')
        if row.get('decision') == 'duplicate' and row.get('duplicate_of') not in existing_ids:
            errors.append(f'{row["slug"]}: duplicate_of must be an existing card id')
        if row.get('suggested_domain') is not None and row['suggested_domain'] not in domains:
            errors.append(f'{row["slug"]}: suggested_domain must be one of {", ".join(sorted(domains))}')
        if not isinstance(row.get('reason'), str) or not row['reason'].strip():
            errors.append(f'{row["slug"]}: every decision needs a concrete reason')
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
        raise IngestError('Review rejected by validation:\n- ' + '\n- '.join(errors))
    return review


def validate_submission(packet: dict, payload: dict, *, role: str, candidate=None,
                        root=None, chunk=None, state=None) -> dict:
    """jobs.submit hook: S4 for producers, shape checks for reviewers."""
    if chunk is None:
        raise IngestError('ingest runs are always bound to a chunk slot')
    if card_diagrams.is_slot(chunk):
        return card_diagrams.validate_answer(payload)
    existing_ids, existing_titles = _vault_ids_titles(root)
    if role == 'reviewer':
        return validate_review(candidate, payload, existing_ids, packet['catalog']['domains'])
    base, rnd = _base(chunk), _round(chunk)
    return validate_candidate(packet, payload, slot=chunk,
                              other_cards=_other_cards(root, state, packet, base),
                              kept_slugs=set(_accepted_so_far(root, state, base, rnd)) if rnd else set(),
                              existing_ids=existing_ids, existing_titles=existing_titles,
                              known_topics=_proposed_topics(root, state, exclude=chunk))


def _proposed_topics(root, state, *, exclude: str | None = None) -> set:
    """New topics proposed by the candidates of every other slot of this source."""
    out = set()
    for slot, info in (state.get('chunks') or {}).items():
        if slot != exclude and info.get('candidate'):
            for t in jobs.artifact(root, state, 'candidate', slot).get('new_topics', []):
                out.add((t['domain'], t['pillar'], t['name']))
    return out


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
        review = jobs.artifact(root, state, 'review', slot)
        decisions = {r['slug']: r for r in review.get('cards', [])}
        for c in cand.get('cards', []):
            if decisions.get(c['slug'], {}).get('decision') == 'accept':
                accepted[c['slug']] = c
            else:
                accepted.pop(c['slug'], None)
    return accepted


def chunk_outcome(root: Path, state: dict, packet: dict, base: str) -> dict:
    """Merge rounds of one chunk; ``next`` names the step still needed, if any."""
    slots = _slots(state, base)
    info0 = (state.get('chunks') or {}).get(base) or {}
    max_rounds = config.review_policy().max_repair_rounds
    used = int(info0.get('invalid_outputs', 0)) + sum(1 for _, i in slots[1:] if i.get('candidate'))
    out = {'chunk': base, 'settled': False, 'next': None, 'accepted': {}, 'pending': [],
           'duplicates': [], 'topics': {}, 'needs_taxonomy': [], 'skipped': []}
    if not slots or not slots[0][1].get('candidate'):
        if used > max_rounds:
            out.update(settled=True, pending=[{'scope': 'chunk', 'chunk': base, 'reason':
                       'the producer output failed validation in every allowed round: '
                       + str(info0.get('last_validation_error') or '')[:2000]}])
            return out
        out['next'] = ('produce', base)
        return out
    accepted: dict = {}
    topic_state: dict = {}
    open_cards: dict = {}
    open_coverage = None
    dups, needs_tax, skipped = [], [], []
    merged_rounds = 0
    for slot, info in slots:
        if not info.get('candidate'):
            # a repair round whose output failed validation: retry while budget lasts
            if used < max_rounds:
                out['next'] = ('repair', slot)
                return out
            break
        if not info.get('review'):
            out['next'] = ('review', slot)
            return out
        merged_rounds += 1
        review = jobs.require_independent_review(root, state, slot)
        cand = jobs.artifact(root, state, 'candidate', slot)
        decisions = {r['slug']: r for r in review.get('cards', [])}
        for c in cand.get('cards', []):
            d = decisions[c['slug']]
            open_cards.pop(c['slug'], None)
            accepted.pop(c['slug'], None)
            if d['decision'] == 'accept':
                accepted[c['slug']] = {**c, '_slot': slot}
            elif d['decision'] == 'duplicate':
                dups.append({'slug': c['slug'], 'title': c.get('title'), 'duplicate_of': d.get('duplicate_of'),
                             'reason': d['reason'], 'chunk': base})
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
            if row.get('disposition') == 'needs_taxonomy':
                needs_tax.append({'chunk': base, 'source_quote': row.get('source_quote'),
                                  'suggested_pillar': row.get('suggested_pillar'), 'reason': row.get('reason')})
            elif row.get('disposition') == 'out_of_scope':
                skipped.append({'chunk': base, 'source_quote': row.get('source_quote'), 'reason': row.get('reason')})
        cov = review.get('coverage') or {}
        open_coverage = None if cov.get('decision') == 'accept' else {'scope': 'coverage', 'chunk': base,
                                                                       'reason': cov.get('reason')}
    findings = bool(open_cards) or open_coverage is not None or \
        any(t['decision'] != 'accept' for t in topic_state.values())
    if findings and used < max_rounds and merged_rounds == len(slots):
        out['next'] = ('repair', _slot_name(base, len(slots)))
        return out
    pending = list(open_cards.values())
    if open_coverage:
        pending.append(open_coverage)
    for slug, card in list(accepted.items()):
        key = (card['domain'], card['pillar'], card['topic'])
        if key in topic_state and topic_state[key]['decision'] != 'accept':
            accepted.pop(slug)
            pending.append({'slug': slug, 'title': card['title'], 'chunk': base,
                            'reason': f'its new topic {key[2]!r} was not accepted: '
                                      f'{topic_state[key]["review_reason"]}'})
    out.update(settled=True, accepted=accepted, pending=pending, duplicates=dups,
               topics={k: v for k, v in topic_state.items() if v['decision'] == 'accept'},
               needs_taxonomy=needs_tax, skipped=skipped)
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
    if kind == 'review':
        call_executor(root, job_id, reviewer, role='reviewer', step='review',
                      prompt_text=_prompt('review_cards.md', state),
                      packet=review_packet(root, state, packet, slot),
                      output_schema=schemas.REVIEW_CARDS, chunk=slot)
        return
    text = _prompt('produce_cards.md', state)
    if packet.get('retry'):
        text += '\n' + _prompt_only('retry_cards.md')
    if kind == 'repair':
        text += '\n' + _prompt_only('repair_cards.md')
    base = _base(slot)
    info = (state.get('chunks') or {}).get(slot) or {}
    feedback = info.get('last_validation_error') if not info.get('candidate') else None
    if feedback:
        text += '\n## Your previous answer was rejected by the validator\n\n' + feedback + '\n'
    try:
        call_executor(root, job_id, producer, role='producer', step='repair' if kind == 'repair' else 'produce',
                      prompt_text=text, packet=producer_packet(root, state, packet, slot),
                      output_schema=schemas.CARDS, chunk=slot)
    except ExecutorFailure as failure:
        if failure.code == CHUNK_TOO_LARGE and slot == base and not info.get('candidate') \
                and len(_chunk_text(packet, base)) >= 2 * MIN_SPLIT_CHARS:
            _record_split(root, job_id, base, failure.error.get('message', ''))
            return
        if failure.code != INVALID_OUTPUT:
            raise
        _record_invalid(root, job_id, base, slot, failure.error.get('message', ''))


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


def _record_invalid(root: Path, job_id: str, base: str, slot: str, message: str) -> None:
    from ..store import vault_lock, write_json
    with vault_lock(root):
        state = jobs.load(root, job_id)
        chunks = state.setdefault('chunks', {})
        info = chunks.setdefault(base, {'candidate': None, 'review': None, 'status': 'prepared', 'round': 0})
        info['invalid_outputs'] = int(info.get('invalid_outputs', 0)) + 1
        info['last_validation_error'] = message[:8000]
        chunks.setdefault(slot, {'candidate': None, 'review': None, 'status': 'prepared', 'round': 0})
        chunks[slot]['last_validation_error'] = message[:8000]
        state['updated_at'] = jobs.now()
        write_json(root, jobs.location(job_id), state)


def outcomes(root: Path, job_id: str) -> list[dict]:
    state = jobs.load(root, job_id)
    packet = jobs.read_input(root, job_id)
    return [chunk_outcome(root, state, packet, c['chunk_id']) for c in effective_chunks(packet, state)]


def advance(root: Path, job_id: str, *, producer, reviewer, apply: bool = True) -> dict:
    """Run every step that can run now; returns the job state plus hand-offs."""
    from ..runner import AwaitingSubmission
    with job_lock(root, job_id):
        state = jobs.load(root, job_id)
        if state.get('commit_intent'):
            if not apply:
                return state
            return jobs.resume_transaction(root, job_id) or jobs.load(root, job_id)
        if state['status'] in {'applied', 'partial', 'published', 'rolled_back', 'failed'}:
            return state
        packet = jobs.read_input(root, job_id)
        handoffs, visited = [], set()
        while True:
            state = jobs.load(root, job_id)
            todo = [c['chunk_id'] for c in effective_chunks(packet, state) if c['chunk_id'] not in visited]
            if not todo:
                break
            base = todo[0]
            out = chunk_outcome(root, state, packet, base)
            if out['settled']:
                visited.add(base)
                continue
            try:
                _step(root, job_id, out['next'], producer, reviewer)
            except AwaitingSubmission as waiting:
                handoffs.append({**waiting.handoff, 'chunk': out['next'][1], 'step': out['next'][0]})
                visited.add(base)
        state = jobs.load(root, job_id)
        settled = [chunk_outcome(root, state, packet, c['chunk_id']) for c in effective_chunks(packet, state)]
        if handoffs or not all(o['settled'] for o in settled):
            state = jobs.update(root, job_id, status='awaiting_submission' if handoffs else 'producing')
            return {**state, 'handoffs': handoffs}
        if not apply:
            state = jobs.update(root, job_id, status='reviewed', proposal=_summary(settled))
            return state
        _, handoffs = _diagram_stage(root, job_id, packet, settled, producer)
        if handoffs:
            state = jobs.update(root, job_id, status='awaiting_submission')
            return {**state, 'handoffs': handoffs}
        return apply_job(root, job_id, settled)


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
    if info.get('invalid_output'):
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
            if failure.code != INVALID_OUTPUT:
                raise
            _mark_slot(root, job_id, slot, invalid_output=failure.error.get('message', '')[:2000])


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
    candidate, n = title, 2
    while candidate.casefold() in taken:
        candidate, n = f'{title} ({n})', n + 1
    taken.add(candidate.casefold())
    return candidate


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
    lay = layouts.of(root)
    settings = packet['settings']
    language = settings['language']
    drawn = diagram_outcome(root, job_id, packet, settled)
    if drawn['pending']:
        raise Conflict('The diagram step is not finished; run `sb job advance`')
    snapshot = read_snapshot(root)
    writable_snapshot(snapshot)
    accepted = [(o['chunk'], s, c) for o in settled for s, c in o['accepted'].items()]
    pending = [p for o in settled for p in o['pending']]
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
    titles_taken = {c.title.casefold() for c in snapshot.cards.values()}
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
    for _, slug, card in accepted:
        for r in card.get('relations') or []:
            target = r['target']
            if target in snapshot.cards:
                tid = target
            elif target in ids:
                tid = ids[target]
            elif target in _written_slugs(packet) and _written_slugs(packet)[target] in snapshot.cards:
                tid = _written_slugs(packet)[target]
            else:
                skipped_relations.append({'from': ids[slug], 'type': r['type'], 'target_slug': target,
                                          'reason': 'target card was not written'})
                continue
            if any(x['type'] == r['type'] and x['target'] == tid for x in rels[slug]):
                continue
            rels[slug].append({k: v for k, v in (('type', r['type']), ('target', tid),
                                                 ('note', r.get('note')), ('tier', r.get('tier'))) if v})
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
    changes: list[Change] = []
    updated: dict = {}
    produced = []
    notes = []
    for chunk, slug, card in accepted:
        cid, title = ids[slug], titles[slug]
        relations = [cardfmt.Relation(r['type'], r['target'], title_of.get(r['target'], ''), r.get('note'), r.get('tier'))
                     for r in rels[slug]]
        meta = {'id': cid, 'title': title, 'kind': card['kind'], 'domain': card['domain'],
                'pillar': card['pillar'], 'topic': card['topic'],
                'tags': ['review', f'flashcards/{card["domain"]}'],
                'aliases': list(dict.fromkeys(a for a in card.get('aliases') or [] if a != title)) or None,
                'sources': [{'path': source_rel, 'sha256': source['sha256'], 'locator': card['source_quotes'][0]}],
                'relations': rels[slug], 'status': card.get('status', 'confirmed'), 'created': today}
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
    for slug in rels:
        for r in rels[slug]:
            inv = inverse.get(r['type'])
            if inv and r['target'] in snapshot.cards:
                to_add.setdefault(r['target'], []).append(_inverse_edge(inv, ids[slug], r))
    maintenance = []
    for target in sorted(to_add):
        existing = snapshot.cards[target]
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
        changes.append(Change(existing.path, existing.sha256, raw2))
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
    needs_taxonomy = [x for o in settled for x in o['needs_taxonomy']]
    status = 'partial' if (pending or needs_taxonomy) else 'applied'
    # a retry child runs after the parent may have moved the file: name where the source is now
    row_path = source['inbox_path'] if inbox_now == source_raw else (
        source_rel if packet.get('retry') else source['inbox_path'] or source['filename'])
    changes.append(inbox.appended(root, lay.inbox_registry, inbox.registry_line(
        {'sha256': source['sha256'], 'path': row_path, 'status': status,
         'job_id': job_id, 'cards': [p['id'] for p in produced], 'pending': len(pending) + len(needs_taxonomy),
         'at': now})))
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
        changes.extend(projection_changes(root, entry_projection(projected, current_day(), final_paths=written,
                                                                 removed_paths=removed)))
    result = {'status': status, 'new_card_count': len(produced), 'produced': produced,
              'pending': pending, 'needs_taxonomy': needs_taxonomy,
              'duplicates': [d for o in settled for d in o['duplicates']],
              'out_of_scope': [x for o in settled for x in o['skipped']],
              'new_topics': [{'domain': d, 'pillar': p, 'name': n} for d, p, n in topics],
              'skipped_relations': skipped_relations, 'maintenance': maintenance,
              'source_record': {'sha256': source['sha256'], 'path': source_rel},
              'inbox_moved': move, 'projection_metrics': projection_metrics,
              'diagrams': [row for row in drawn['report'] if row['ref'] in {p['slug'] for p in produced}],
              'same_family': bool(state.get('same_family'))}
    try:
        done = jobs.finish_transaction(root, job_id, result, changes, checks=snapshot_checks(snapshot, changes))
    except Conflict as exc:
        if jobs.load(root, job_id).get('commit_intent'):
            jobs.release_intent(root, job_id, reason=f'{exc}; run `sb job advance` to rebuild the write plan')
        raise
    return _maybe_commit(root, job_id, done)


def _maybe_commit(root: Path, job_id: str, state: dict) -> dict:
    settings = config.current()
    if not settings.git_commit or state.get('status') not in {'applied', 'partial'} or state.get('commit'):
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
    """Create the next run for ``role`` (optionally a specific chunk) via ``executor``."""
    from ..runner import AwaitingSubmission
    with job_lock(root, job_id):
        state = jobs.load(root, job_id)
        packet = jobs.read_input(root, job_id)
        for c in effective_chunks(packet, state):
            if chunk and _base(chunk) != c['chunk_id']:
                continue
            out = chunk_outcome(root, state, packet, c['chunk_id'])
            if out['settled'] or out['next'] is None:
                continue
            wants = 'reviewer' if out['next'][0] == 'review' else 'producer'
            if wants != role:
                continue
            try:
                _step(root, job_id, out['next'], executor, executor)
            except AwaitingSubmission as waiting:
                return {**waiting.handoff, 'chunk': out['next'][1], 'step': out['next'][0]}
            raise ValueError('The executor finished the step directly; use `sb job advance` instead')
        settled = [chunk_outcome(root, state, packet, c['chunk_id']) for c in effective_chunks(packet, state)]
        if role == 'producer' and all(o['settled'] for o in settled) and (not chunk or card_diagrams.is_slot(chunk)):
            _, handoffs = _diagram_stage(root, job_id, packet, settled, executor, only=chunk)
            if handoffs:
                return handoffs[0]
        raise ValueError(f'No {role} step is due' + (f' for {chunk}' if chunk else ''))

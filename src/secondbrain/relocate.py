"""Body-preserving card relocation between existing catalog domains.

Selection uses prior classification jobs: rows whose independent review
accepted a ``needs_domain_review`` disposition, with a catalog-listed
``suggested_domain`` different from the current one, are frozen into a
``relocation`` packet. Applying performs the paired old-path removal + new-path
creation move trusted by ``store._validate_moves`` plus same-id registry, MOC
and entry/review projection changes in one recoverable transaction; card
bodies/SR/unknown frontmatter stay untouched. A card stays pending, with
concrete referencing files, whenever a still-standing markdown link would break
(explicit old-path links outside this transaction's regenerated projections, or
a directory-relative link inside the moving card). Reviewers never see the
earlier author's self-judgement.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote

from . import classification as cls, jobs
from . import layout as layouts
from .catalog import category_pairs, taxonomy_packet
from .evidence import check_verbatim
from .classification import projection_changes, taxonomy_fingerprint, writable_snapshot
from .store import Change, Conflict, digest, read_bytes
from .vault import (Card, card_packet, parse_note,
                    patch_frontmatter, read_snapshot)

METHOD_VERSION = 'relocation/1'
_MDPATH_RE = re.compile(r'\[[^\[\]]*\]\(([^)]*)\)')
_WIKI_RE = re.compile(r'\[\[([^\]|#^]+)')
_SCHEME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*:')


def prepare(root_, *, source_jobs, job_id, ids=None):
    """Freeze a relocation batch from independently reviewed domain-review rows."""
    snapshot = read_snapshot(root_)
    if snapshot.root.resolve() != root_.resolve():
        raise ValueError('The batch snapshot belongs to another vault')
    writable_snapshot(snapshot)
    if (not isinstance(source_jobs, list) or not source_jobs
            or len(source_jobs) != len(set(source_jobs))):
        raise ValueError('source_jobs must be a non-empty list of distinct classification job ids')
    catalog_domains = set((snapshot.catalog.get('domains') or {})
                          if isinstance(snapshot.catalog, dict) else {})
    selected, evidence = {}, {}
    for jid in source_jobs:
        state = jobs.load(root_, jid)
        if state['kind'] != 'classification':
            raise ValueError(f'Source job is not a classification job: {jid}')
        for value in cls.reviewed_rows_with_repairs(root_, jid):
            row, finding = value['row'], value['finding']
            if row.get('disposition') != 'needs_domain_review' or finding['decision'] != 'accept':
                continue
            if ids is not None and row['id'] not in ids:
                continue
            reviewed_state = jobs.load(root_, value['job_id'])
            cid = row['id']
            if cid not in snapshot.cards:
                raise ValueError(f'A reviewed card no longer exists: {cid}')
            if snapshot.cards[cid].sha256 != row['input_sha256']:
                raise Conflict(f'The card behind a reviewed move suggestion has changed: {cid}')
            suggested = row.get('suggested_domain')
            if not isinstance(suggested, str) or not suggested:
                raise ValueError(f'No suggested_domain: {cid}')
            if suggested not in catalog_domains:
                raise ValueError(f'Target domain is not in the catalog (new domains are a separate decision): {suggested}')
            if suggested == snapshot.cards[cid].domain:
                raise ValueError(f'Target domain must differ from the current one: {cid}')
            if cid in selected and selected[cid] != suggested:
                raise Conflict(f'The same card is suggested for different target domains: {cid}')
            selected[cid] = suggested
            evidence[value['job_id']] = {'candidate_sha256': reviewed_state['candidate']['sha256'],
                                         'review_sha256': reviewed_state['review']['sha256']}
    if ids is not None:
        if len(ids) != len(set(ids)) or any(i not in selected for i in ids):
            raise ValueError('Card ids repeat or are not among the reviewed needs_domain_review items')
        selected = {i: selected[i] for i in ids}
    cards = [snapshot.cards[cid] for cid in selected]
    cards.sort(key=lambda c: (c.domain, c.id))
    if not cards:
        return {'status': 'unchanged', 'job_id': job_id, 'input_count': 0}
    scope = sorted({c.domain for c in cards} | set(selected.values()))
    packet = {'kind': 'relocation', 'method_version': METHOD_VERSION,
              'cards': [card_packet(c) for c in cards],
              'destinations': {c.id: selected[c.id] for c in cards},
              'catalog': snapshot.catalog,
              'taxonomy': taxonomy_packet(snapshot, scope),
              'taxonomy_sha256': taxonomy_fingerprint(snapshot, scope),
              'taxonomy_scope': scope,
              'snapshot_fingerprint': snapshot.fingerprint,
              'source_evidence': evidence}
    state = jobs.create(root_, job_id, 'relocation', packet)
    return {**state, 'input_count': len(cards), 'destinations': packet['destinations']}


def validate(packet, proposal):
    """Coverage, version and quote rules as in classification; an ``assigned`` row must name the bound destination domain and one of its pillar/topic pairs."""
    rows = proposal.get('items')
    if not isinstance(rows, list):
        raise ValueError('A move proposal must be {items:[...]}')
    expected = {c['id']: c for c in packet['cards']}
    ids = [r.get('id') for r in rows if isinstance(r, dict)]
    if len(ids) != len(rows) or len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError('A move proposal must cover every id of the batch exactly once')
    for row in rows:
        card = expected[row['id']]
        if row.get('input_sha256') != card['sha256']:
            raise Conflict(f'Wrong input_sha256 in the proposal: {row["id"]}')
        if row.get('disposition') not in {'assigned', 'taxonomy_gap'}:
            raise ValueError(f'No valid disposition: {row["id"]}')
        problem = check_verbatim(card['body'], row.get('evidence_quote'))
        if problem:
            raise ValueError(f'Evidence is not a verbatim quote of the card body: {row["id"]} ({problem})')
        if not isinstance(row.get('reason'), str) or not row['reason'].strip():
            raise ValueError(f'Move reason missing: {row["id"]}')
        if row['disposition'] != 'assigned':
            continue
        if row.get('domain') != packet['destinations'].get(row['id']):
            raise ValueError(f'An assigned item must use the target domain bound at prepare: {row["id"]}')
        if not row.get('pillar') or not row.get('topic'):
            raise ValueError(f'Fill both pillar and topic, not only one: {row["id"]}')
        if (row['pillar'], row['topic']) not in _packet_pairs(packet, row['domain']):
            raise ValueError(f"Classification is not in this domain's taxonomy: {row['id']}")
    return rows


def _packet_pairs(packet, domain):
    tax = packet.get('taxonomy') or {}
    moc = (tax.get('domain_mocs') or {}).get(domain)
    body = (tax.get('taxonomies') or {}).get(moc) or {}
    return {(p['name'], t) for p in body.get('pillars', []) for t in p.get('topics', [])}


def apply(root_, job_id):
    """Move only accepted, ``assigned`` cards whose move breaks no link; the rest stay pending with the files that refer to them."""
    from .moc import build_mocs
    from .projections import current_day, entry_projection, planned_paths, review_projection
    from .reviews import available as reviews_available
    state = jobs.load(root_, job_id)
    if state['kind'] != 'relocation':
        raise ValueError('This job is not a move job')
    if state['status'] in {'applied', 'published', 'partial'}:
        return state
    resumed = jobs.resume_transaction(root_, job_id)
    if resumed is not None:
        return resumed
    packet = jobs.read_input(root_, job_id)
    rows = validate(packet, jobs.artifact(root_, state, 'candidate'))
    review = jobs.require_independent_review(root_, state)
    findings = cls.review_rows(rows, review)
    snapshot = read_snapshot(root_)
    writable_snapshot(snapshot)
    if taxonomy_fingerprint(snapshot, packet['taxonomy_scope']) != packet['taxonomy_sha256']:
        raise Conflict('The taxonomy changed after preparation; prepare the affected cards again')
    inputs = {c['id']: c for c in packet['cards']}
    pending, moves = [], []
    for row in rows:
        card = snapshot.cards.get(row['id'])
        if card is None or card.sha256 != inputs[row['id']]['sha256']:
            raise Conflict(f'Card changed after preparation: {row["id"]}')
        decision = findings[row['id']]
        if decision['decision'] != 'accept' or row['disposition'] != 'assigned':
            pending.append({'id': row['id'], 'disposition': row['disposition'],
                            'reason': decision['reason'] if decision['decision'] != 'accept' else row['reason'],
                            'proposal': row})
            continue
        moves.append(row)
    documents = dict(_iter_md(root_))
    active = list(moves)
    while active:
        changes, projected, moved, affected = _stage_moves(root_, snapshot, packet, active)
        projection = build_mocs(projected, affected_domains=affected)
        written, removed = planned_paths(changes)
        day = current_day()
        entries = entry_projection(projected, day, final_paths=written | set(projection.files),
                                   removed_paths=removed)
        # the daily review page reads the review registry, which needs the review extra
        reviews = (review_projection(projected, day, extra_targets=entries.files)
                   if reviews_available() else None)
        replacements = {**projection.files, **entries.files, **(reviews.files if reviews else {})}
        obstacles = link_obstacles(root_, {c.id: c for c, _ in moved},
                                   documents=documents, replacements=replacements)
        if not obstacles:
            break
        for row in active:
            if row['id'] in obstacles:
                pending.append({'id': row['id'], 'disposition': 'assigned',
                                'reason': 'Not moved: links to its old path or folder-relative links would break',
                                'proposal': row, 'referencing_files': obstacles[row['id']]})
        active = [row for row in active if row['id'] not in obstacles]
    if not active:
        return jobs.update(root_, job_id, status='needs_revision' if pending else 'applied',
                           pending=pending, applied_paths=[], moved_count=0)
    changes.append(_registry_change(root_, moved))
    changes.extend(projection_changes(root_, projection))
    changes.extend(projection_changes(root_, entries))
    if reviews is not None:
        changes.extend(projection_changes(root_, reviews))
    observed = dict(snapshot.read_hashes)
    for rel, raw in documents.items():
        sha = digest(raw)
        if rel in observed and observed[rel] != sha:
            raise Conflict(f'A snapshot input changed while links were scanned: {rel}')
        observed[rel] = sha
    checks = cls.snapshot_checks(replace(snapshot, read_hashes=observed), changes)
    return jobs.finish_transaction(root_, job_id,
        {'status': 'partial' if pending else 'applied', 'pending': pending,
         'moved_count': len(moved), 'projection_metrics': projection.metrics}, changes, checks=checks)


def _stage_moves(root_, snapshot, packet, moves):
    changes, updated = [], dict(snapshot.cards)
    moved, affected = [], set()
    for row in moves:
        card = snapshot.cards[row['id']]
        dest = packet['destinations'][row['id']]
        if (row['pillar'], row['topic']) not in category_pairs(snapshot, dest):
            raise ValueError(f"Classification is not in this domain's taxonomy: {card.id}")
        new_path = layouts.of(root_).card_path(dest, card.path.rsplit("/", 1)[1])
        if read_bytes(root_, new_path) is not None:
            raise Conflict(f'Move target exists already: {new_path}')
        new_raw, new_fm, new_body = _relocated_bytes(card, dest, row)
        changes.append(Change(new_path, None, new_raw))
        changes.append(Change(card.path, card.sha256, None))
        updated[card.id] = replace(card, path=new_path, domain=dest, raw=new_raw,
                                   fm=new_fm, body=new_body, sha256=digest(new_raw))
        moved.append((card, dest))
        affected.update({card.domain, dest})
    return changes, replace(snapshot, cards=updated), moved, affected


def _relocated_bytes(card, dest, row):
    """Change only domain, pillar, topic and the domain part of the flashcards tag; body and review data stay."""
    patches = {'domain': dest, 'pillar': row['pillar'], 'topic': row['topic']}
    old_tag = f'flashcards/{card.domain}'
    tags = card.fm.get('tags')
    if isinstance(tags, list):
        swapped = [f'flashcards/{dest}' if t == old_tag else t for t in tags]
        if swapped != tags:
            patches['tags'] = swapped
    elif tags == old_tag:
        patches['tags'] = f'flashcards/{dest}'
    raw = patch_frontmatter(card.raw, patches)
    fm, body = parse_note(raw)
    if body != card.body:
        raise ValueError(f'The move patch would change the card body: {card.id}')
    return raw, fm, body


def _registry_change(root_, moved):
    """Change only path and domain of the registry entries with these ids, keep unknown fields; compare-and-swap on the bytes read at apply time."""
    registry_rel = layouts.of(root_).registry
    raw = read_bytes(root_, registry_rel)
    if raw is None:
        raise ValueError('notes_registry is missing; cannot move cards')
    data = json.loads(raw)
    notes = data.get('notes')
    if not isinstance(notes, list):
        raise ValueError('registry notes is not a list')
    index = {n['id']: i for i, n in enumerate(notes) if isinstance(n, dict)}
    for card, dest in moved:
        i = index.get(card.id)
        if i is None:
            raise ValueError(f'Card missing from the registry: {card.id}')
        note = dict(notes[i])
        note['path'] = layouts.of(root_).card_path(dest, card.path.rsplit("/", 1)[1])
        note['domain'] = dest
        notes[i] = note
    after = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    return Change(registry_rel, digest(raw), after)


def link_obstacles(root_, moving, *, documents=None, replacements=None):
    """``{id: [vault-relative files that refer to it]}`` for cards whose move would break a link.

    Links are checked in the bytes as they will be written; text kept in a
    custom map page or a guide is not exempt because of its folder name.
    """
    if not moving:
        return {}
    stem_ids = {card.path[:-3]: cid for cid, card in moving.items()}
    path_ids = {card.path: cid for cid, card in moving.items()}
    obstacles = {cid: [] for cid in moving}
    documents = dict(_iter_md(root_)) if documents is None else documents
    # Generated MOC pages (one root page per catalog MOC plus its page folder)
    # are projections rebuilt from the moved snapshot, so their old links are
    # not real obstacles. Hand-written pages in the MOC folder are still checked.
    documents = {rel: raw for rel, raw in documents.items() if not _generated_moc_page(root_, rel)}
    for rel, raw_bytes in {**documents, **(replacements or {})}.items():
        text = raw_bytes.decode('utf-8')
        for raw in _WIKI_RE.findall(text):
            norm = _normalize(unquote(raw))
            if '/' not in norm:
                continue
            candidates = {norm, _normalize(posixpath.normpath(posixpath.join(posixpath.dirname(rel), norm)))}
            for stem, cid in stem_ids.items():
                if stem in candidates or any(stem.endswith('/' + value) for value in candidates):
                    obstacles[cid].append(rel)
        md_targets = _MDPATH_RE.findall(text)
        md_targets += re.findall(r'^\s*\[[^]\n]+\]:\s*(<[^>]+>|\S+)', text, flags=re.M)
        for raw in md_targets:
            inner = raw.strip()
            if inner.startswith('<') and '>' in inner:
                inner = inner[1:inner.index('>')]
            else:
                inner = re.split(r'\s+["\']', inner, maxsplit=1)[0]
            if not inner or inner.startswith('#') or inner.startswith('//') or _SCHEME_RE.match(inner):
                continue
            target = unquote(inner.split('#')[0].split('?')[0].strip())
            if not target:
                continue
            if rel in path_ids and not target.startswith('/'):
                obstacles[path_ids[rel]].append(rel)      # a folder-relative link depends on the card's domain folder
            if target.startswith('/'):
                norm = _normalize(target.lstrip('/'))
            else:
                norm = _normalize(posixpath.normpath(
                    posixpath.join(posixpath.dirname(rel), target)))
            if norm in stem_ids:
                obstacles[stem_ids[norm]].append(rel)
    return {cid: sorted(set(files)) for cid, files in obstacles.items() if files}


def _generated_moc_page(root_, rel):
    lay = layouts.of(root_)
    if not lay.is_moc_page(rel):
        return False
    rest = rel[len(lay.mocs) + 1:].split('/')
    raw = read_bytes(root_, lay.catalog)
    mocs = {m.get('moc') for m in (json.loads(raw).get('domains') or {}).values()
            if isinstance(m, dict)} if raw else set()
    head = rest[0][:-3] if len(rest) == 1 else rest[0]
    return head in mocs


def _normalize(value):
    return (value.strip().strip('"').removesuffix('.md')
            .removeprefix('/').removeprefix('./').rstrip('/'))


def _iter_md(root_):
    """Every .md file of the vault, read through no-follow handles; only git internals are skipped."""
    root_ = Path(root_)
    for base, dirs, files in os.walk(root_):
        base_path = Path(base)
        dirs[:] = sorted(d for d in dirs if d != '.git' and not (base_path / d).is_symlink())
        for name in sorted(files):
            if not name.endswith('.md'):
                continue
            full = base_path / name
            if full.is_symlink() or not full.is_file():
                continue
            rel = str(full.relative_to(root_)).replace(os.sep, '/')
            raw = read_bytes(root_, rel)
            if raw is None:
                raise Conflict(f'A file disappeared while links were scanned: {rel}')
            raw.decode('utf-8')
            yield rel, raw

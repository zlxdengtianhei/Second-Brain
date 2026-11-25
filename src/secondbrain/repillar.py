"""`sb repillar`: file cards under another pillar of their own domain, as one recoverable transaction.

This is how a large pillar is split into pillars of its domain (`sb advise` decided ``split_pillars``), and how a few
cards are moved by hand between pillars of one map. `sb relocate` moves cards between domains, `sb classify` only
fills a missing or illegal classification, and `sb curate` adds pillars but never moves a card; this command closes
that gap. It judges no meaning and asks no model: which cards go where comes from a reviewed advice job (its groups
and pillars) or from the owner (``--ids … --to``, or a plan file).

One transaction (`sb recover <job> --rollback` puts every byte back) changes only:

* the ``pillar`` line of each moved card (and its ``topic`` line, when it had one: a new or target pillar files its
  cards directly, :mod:`secondbrain.levels`); every other byte of the card stays, and the plan checks it line by line;
* ``pillar`` (and ``topic``) of the moved cards' registry notes;
* the map's taxonomy file: new pillars stand where the pillar they came from stood (pillars after them move down one
  number each), a target's ``why`` is the one given, and a pillar this move leaves without cards is removed;
* the generated map pages of every map (a pillar page another map links to may change its name); a generated page
  the rebuilt maps no longer have is removed, and the plan stops when a page the owner wrote still names it;
* ``HOME.md``, and today's review page when there is one, as `sb relocate` regenerates them (they count map pages
  and name pillars).

Without ``apply`` it only plans and writes nothing.
"""
from __future__ import annotations

import copy
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

from . import jobs
from . import layout as layouts
from . import levels
from .classification import projection_changes, snapshot_checks, writable_snapshot
from .curation import _serialise_taxonomy
from .flatten import referrers, stale_pages
from .moc import build_mocs
from .store import Change, Conflict, apply_changes, digest, read_bytes
from .vault import parse_note, patch_frontmatter, placement_updates, read_snapshot, with_topic

#: Characters of a pillar name (the longest name `sb advise` allows, in either card language).
NAME_MAX = 48
#: The frontmatter keys a move may change, so the only lines of a card that may differ.
PLACEMENT = ('pillar', 'topic')


# ---------------------------------------------------------------------------
# What moves where: one spec, from an advice job, from ids, or from a plan file
# ---------------------------------------------------------------------------
def spec_from_advice(root: Path, snapshot, job_id: str) -> dict:
    """The moves of a reviewed ``split_pillars`` advice, while the pillar's evidence is the one it judged."""
    from .advise import job_id_of, packet_of
    state = jobs.load(root, job_id)
    if state.get('kind') != 'advice':
        raise ValueError(f'{job_id} is not an advice job (sb advise)')
    advice = state.get('advice') if state.get('status') == 'applied' else None
    if not advice:
        raise ValueError(f'the advice job {job_id} has no advice yet; finish it with sb advise --job {job_id}')
    packet = jobs.read_input(root, job_id)
    domain, pillar = packet['domain']['id'], packet['pillar']['name']
    if advice.get('decision') != 'split_pillars':
        other = {'new_domain': 'a new domain is made with sb domcreate',
                 'keep': 'the pillar stays whole'}.get(advice.get('decision'), 'nothing to move')
        raise ValueError(f'the advice {job_id} is {advice.get("decision")!r}, not split_pillars: {other}')
    review = advice.get('review') or {}
    if review.get('decision') != 'accept':
        raise ValueError(f'the review of advice {job_id} did not accept it ({review.get("decision")!r}: '
                         f'{review.get("reason")}); ask again with sb advise --domain {domain} --pillar {pillar}')
    try:
        now = job_id_of(packet_of(snapshot, domain, pillar))
    except ValueError:
        now = None
    if now != job_id:
        raise Conflict(f'the cards or links of pillar {pillar!r} changed after the advice {job_id}; ask again with '
                       f'sb advise --domain {domain} --pillar {pillar}')
    candidate = jobs.artifact(root, state, 'candidate')
    groups = {g['id']: [c['id'] for c in g['cards']] for g in packet['groups']}
    pillars = [{'name': p['name'].strip(), 'why': p['why'].strip(),
                'ids': [cid for gid in p['groups'] for cid in groups[gid]]} for p in candidate['pillars']]
    return {'domain': domain, 'from': f'advice {job_id}', 'pillars': pillars,
            'kept': [c['id'] for c in packet['scattered']], 'source_pillar': pillar}


def spec_from_ids(ids: list[str], to: str, why: str | None = None, domain: str | None = None) -> dict:
    if not ids or not to:
        raise ValueError('give the cards (--ids) and the pillar they go to (--to)')
    return {'domain': domain, 'from': 'ids', 'pillars': [{'name': to, 'why': why, 'ids': list(ids)}]}


def spec_from_file(path: Path, domain: str | None = None) -> dict:
    """A plan file: ``{"domain"?: slug, "pillars": [{"name", "why"?, "ids": [...]}]}``."""
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('pillars'), list):
        raise ValueError('a plan file is {"pillars": [{"name": ..., "why": ..., "ids": [...]}]}')
    if domain and data.get('domain') and data['domain'] != domain:
        raise ValueError(f'the plan file is for domain {data["domain"]!r}, not {domain!r}')
    return {'domain': domain or data.get('domain'), 'from': f'plan {Path(path).name}', 'pillars': data['pillars']}


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------
def _check_spec(snapshot, spec) -> tuple[str, list[dict]]:
    """The domain and the cleaned target list; raises ``ValueError`` naming every problem."""
    problems, targets, seen = [], [], set()
    rows = spec.get('pillars')
    if not isinstance(rows, list) or not rows:
        raise ValueError('name at least one pillar the cards go to')
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f'pillars[{i}] is not an object')
            continue
        name = row.get('name').strip() if isinstance(row.get('name'), str) else ''
        why = row.get('why').strip() if isinstance(row.get('why'), str) else None
        ids = row.get('ids')
        if not 1 <= len(name) <= NAME_MAX or '\n' in name:
            problems.append(f'pillars[{i}]: a pillar name of 1-{NAME_MAX} characters on one line')
        if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids):
            problems.append(f'pillars[{i}] ({name}): ids must be a non-empty list of card ids')
            continue
        for cid in ids:
            if cid in seen:
                problems.append(f'card {cid} is named twice')
            seen.add(cid)
            if cid not in snapshot.cards:
                problems.append(f'no card {cid}')
        targets.append({'name': name, 'why': why or None, 'ids': list(ids)})
    names = [t['name'] for t in targets]
    if len(set(names)) != len(names):
        problems.append('each target pillar is named once')
    domains = {snapshot.cards[cid].domain for t in targets for cid in t['ids'] if cid in snapshot.cards}
    domain = spec.get('domain')
    if domain is None and len(domains) == 1:
        domain = next(iter(domains))
    if domain is None:
        problems.append(f'the cards are in {len(domains)} domains; give --domain' if domains else 'no domain')
    elif domains - {domain}:
        problems.append(f'cards outside domain {domain!r} ({sorted(domains - {domain})}): '
                        'sb relocate moves cards between domains')
    if problems:
        raise ValueError('sb repillar refused the move:\n- ' + '\n- '.join(problems))
    return domain, targets


def _only_placement_lines(before: bytes, after: bytes) -> bool:
    """True when ``after`` differs from ``before`` only in top-level ``pillar:`` / ``topic:`` lines."""
    import difflib
    a, b = before.decode('utf-8').splitlines(), after.decode('utf-8').splitlines()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        if not all(line.split(':', 1)[0] in PLACEMENT for line in a[i1:i2] + b[j1:j2]):
            return False
    return True


def _ordered(taxonomy: dict, targets: list[dict], anchor: str, emptied: set) -> tuple[list[dict], dict]:
    """The pillars after the move: new ones where ``anchor`` stood (after it, or in its place when it is emptied),
    emptied ones gone; numbers kept where they can be, else the next free one. Returns ``(pillars, renumbered)``."""
    old = sorted((p for p in taxonomy['pillars'] if isinstance(p, dict)), key=lambda p: p.get('order', 0))
    existing = {p['name'] for p in old}
    new = [{'name': t['name'], 'order': 0, 'why': t['why']} for t in targets if t['name'] not in existing]
    out = []
    for p in old:
        if p['name'] == anchor:
            if p['name'] not in emptied:
                out.append(p)
            out.extend(new)
            new = []
        elif p['name'] not in emptied:
            out.append(p)
    out.extend(new)  # no anchor (cards from several pillars): at the end
    renumbered, last = {}, 0
    for p in out:
        before = p.get('order', 0)
        p['order'] = before if before > last else last + 1
        if before and before != p['order']:
            renumbered[p['name']] = [before, p['order']]
        last = p['order']
    return out, renumbered


def plan(root: Path, spec: dict) -> dict:
    """The changes that file the cards of ``spec`` under their target pillars, and what they do."""
    root = Path(root)
    lay = layouts.of(root)
    snapshot = read_snapshot(root)
    writable_snapshot(snapshot)
    domain, targets = _check_spec(snapshot, spec)
    domains = snapshot.catalog.get('domains') or {}
    if domain not in domains:
        raise ValueError(f'unknown domain: {domain}')
    moc = domains[domain]['moc']
    sharing = sorted(d for d, m in domains.items() if m.get('moc') == moc)
    taxonomy = snapshot.taxonomies.get(moc)
    if not isinstance(taxonomy, dict) or not isinstance(taxonomy.get('pillars'), list):
        raise ValueError(f'map {moc!r} has no taxonomy file')
    pillars = {p['name']: p for p in taxonomy['pillars'] if isinstance(p, dict) and isinstance(p.get('name'), str)}

    problems, moves, already = [], {}, []
    for t in targets:
        current = pillars.get(t['name'])
        if current is not None and not levels.is_flat(current):
            problems.append(f'pillar {t["name"]!r} has topics of its own; sb flatten --domain {domain} first')
        if current is None and not t['why']:
            problems.append(f'{t["name"]!r} is a new pillar: say what it answers (why)')
        for cid in t['ids']:
            card = snapshot.cards[cid]
            stored, _ = parse_note(card.raw)
            if stored.get('pillar') not in pillars:
                problems.append(f'card {cid} has no pillar of this map yet; sb classify --domain {domain} files it')
            elif stored.get('pillar') == t['name'] and 'topic' not in stored:
                already.append(cid)
            else:
                moves[cid] = (stored.get('pillar'), t['name'])
    if problems:
        raise ValueError('sb repillar refused the move:\n- ' + '\n- '.join(problems))

    before_count = Counter(c.fm.get('pillar') for c in snapshot.cards.values() if c.domain in sharing)
    after_count = Counter(before_count)
    for cid, (old, new) in moves.items():
        after_count[old] -= 1
        after_count[new] += 1
    emptied = {name for name in pillars if before_count[name] > 0 and after_count[name] <= 0
               and name not in {t['name'] for t in targets}}
    sources = Counter(old for old, _ in moves.values())
    anchor = spec.get('source_pillar') or (sources.most_common(1)[0][0] if len(sources) == 1 else None)

    new_tax = copy.deepcopy(taxonomy)
    ordered, renumbered = _ordered(new_tax, targets, anchor, emptied)
    whys = {t['name']: t['why'] for t in targets if t['why']}
    for p in ordered:
        if p['name'] in whys:
            p['why'] = whys[p['name']]
        p['topics'] = levels.pillar_topics(p)
    new_tax['pillars'] = ordered
    new_pillars = {p['name']: p for p in ordered}

    changes = []
    tax_rel = lay.taxonomy_path(moc)
    tax_before = read_bytes(root, tax_rel)
    tax_after = _serialise_taxonomy(new_tax)
    if tax_before != tax_after:
        changes.append(Change(tax_rel, None if tax_before is None else digest(tax_before), tax_after))
    updated = dict(snapshot.cards)
    for cid in sorted(moves):
        card = snapshot.cards[cid]
        target = moves[cid][1]
        raw = patch_frontmatter(card.raw, placement_updates(target, target))
        fm, body = parse_note(raw)
        stored, _ = parse_note(card.raw)
        if (body != card.body or {k: v for k, v in stored.items() if k not in PLACEMENT}
                != {k: v for k, v in fm.items() if k not in PLACEMENT} or not _only_placement_lines(card.raw, raw)):
            raise ValueError(f'the move would change more of the card than its pillar line: {cid}')
        changes.append(Change(card.path, card.sha256, raw))
        updated[cid] = replace(card, raw=raw, fm=with_topic(fm, new_pillars), sha256=digest(raw))
    registry_raw = read_bytes(root, lay.registry)
    if registry_raw is not None and moves:
        data = json.loads(registry_raw)
        notes = data.get('notes') if isinstance(data, dict) else None
        if isinstance(notes, list):
            for i, note in enumerate(notes):
                if isinstance(note, dict) and note.get('id') in moves:
                    note = {k: v for k, v in note.items() if k != 'topic'}
                    note['pillar'] = moves[note['id']][1]
                    notes[i] = note
            after = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
            if after != registry_raw:
                changes.append(Change(lay.registry, digest(registry_raw), after))
    projected = replace(snapshot, cards=updated, taxonomies={**snapshot.taxonomies, moc: new_tax})
    # every map: another map's page names this map's pillar pages, whose numbers may change
    projection = build_mocs(projected)
    pages = projection_changes(root, projection)
    changes.extend(pages)
    stale = stale_pages(root, moc, projection)
    changes.extend(stale)
    # HOME counts the map pages, and the daily review page names pillars: both as the move leaves the vault
    from .projections import current_day, entry_projection, planned_paths, review_projection
    from .reviews import available as reviews_available
    written, removed = planned_paths(changes)
    day = current_day()
    entries = entry_projection(projected, day, final_paths=written | set(projection.files), removed_paths=removed)
    reviews = review_projection(projected, day, extra_targets=entries.files) if reviews_available() else None
    if reviews is not None and not any(rel.endswith('.md') and read_bytes(root, rel) is not None
                                       for rel in reviews.files):
        reviews = None  # no review page of today yet: this move does not start one
    entry_pages = projection_changes(root, entries) + (projection_changes(root, reviews) if reviews else [])
    changes.extend(entry_pages)
    linked = referrers(root, [c.relative_path for c in stale],
                       {**projection.files, **entries.files, **(reviews.files if reviews else {})})
    if linked:
        shown = '; '.join(f'{page} ← {", ".join(files)}' for page, files in linked.items())
        raise Conflict(f'pages this move removes or renames are still named by files the program does not write: '
                       f'{shown}. Change those links first.')
    report = {
        'domain': domain, 'moc': moc, 'from': spec.get('from'),
        'moved': {cid: {'from': old, 'to': new} for cid, (old, new) in sorted(moves.items())},
        'cards_changed': len(moves), 'already_there': sorted(already),
        'kept_in_place': sorted(spec.get('kept') or []),
        'pillars_added': [t['name'] for t in targets if t['name'] not in pillars],
        'pillars_removed': sorted(emptied), 'pillars_renumbered': renumbered,
        'pillars_after': [{'name': p['name'], 'order': p['order'], 'cards': after_count[p['name']]} for p in ordered],
        'pages_written': len(pages), 'entry_pages_written': [c.relative_path for c in entry_pages],
        'pages_removed': [c.relative_path for c in stale],
        'projection_issues': projection.issues,
    }
    key = json.dumps([report['moved'], sorted(whys.items()), snapshot.fingerprint], ensure_ascii=False)
    return {'snapshot': snapshot, 'changes': changes, 'report': report,
            'job_id': 'repillar-' + digest(key.encode('utf-8'))[:16]}


def _unused(root: Path, job_id: str) -> str:
    """``job_id``, or after a rollback of that same plan the next ``job_id-N``: the same move can be written again."""
    from .store import _load_journal
    name, n = job_id, 1
    while (journal := _load_journal(root, name)) is not None and journal.get('status') == 'rolled_back':
        n += 1
        name = f'{job_id}-{n}'
    return name


def run(root: Path, spec: dict, *, apply: bool = False, job_id: str | None = None) -> dict:
    """Plan, or with ``apply`` write, the move of ``spec``."""
    p = plan(root, spec)
    report = p['report']
    job = job_id or _unused(root, p['job_id'])
    if not apply:
        return {'status': 'planned', 'job_id': job, 'changed_files': len(p['changes']),
                'paths': [c.relative_path for c in p['changes']], **report}
    if not p['changes']:
        return {'status': 'unchanged', 'job_id': job, **report}
    checks = snapshot_checks(p['snapshot'], p['changes'])
    tx = apply_changes(root, job, p['changes'], checks=checks)
    return {'status': 'applied', 'job_id': job, 'changed_files': len(tx.get('paths') or []),
            'rollback': f'sb recover {job} --rollback', **report}

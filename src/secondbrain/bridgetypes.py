"""``sb bridges``: which bridge strengths a vault's catalog declares, and the step that adds or takes back the strong one.

A bridge joins two cards of two domains, and its relation type carries its strength (:mod:`secondbrain.bridges`):
``same_concept_as`` (strong) for one concept that each domain describes with its own emphasis, ``similar_to``
(weak) for two cards that are alike but answer different questions. A catalog written before the strong type
existed declares only the weak one, so every bridge it gets is weak and ``sb status`` cannot tell the two apart.

- ``--upgrade`` writes the catalog that declares the strong type (open to every domain, its own inverse, glossed
  in the vault's language). No card changes: the bridges written before stay weak, because the level the judge
  gave each pair is kept in the job, not in the vault.
- ``--undo`` takes it back: every relation of the strong type on a card is written as the weak type (on both ends
  of a bridge, in the front matter and in the related section), the bridge rows follow, and the catalog loses the
  type. So the vault reads as it did before the upgrade, with the bridges written since kept, weak.

Both are one transaction each (``sb recover`` resumes or rolls one back); without ``--apply`` they only say what
would change. Map pages name the relation types, so ``sb refresh --apply`` follows either step.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from . import bridges, jobs
from . import layout as layouts
from . import pointers as pointer_store
from .catalog import validate_catalog
from .store import Change, apply_changes, digest, read_bytes
from .vault import patch_frontmatter, read_snapshot


def _catalog(root: Path) -> tuple[bytes, dict]:
    rel = layouts.of(root).catalog
    raw = read_bytes(root, rel)
    if raw is None:
        raise ValueError('No catalog yet; `sb init` writes one')
    return raw, json.loads(raw)


def describe(root: Path) -> dict:
    """What the catalog declares and how many relations of each strength the cards hold."""
    _, catalog = _catalog(root)
    snapshot = read_snapshot(root)
    held = {'strong': 0, 'weak': 0}
    for card in snapshot.cards.values():
        for rel in card.fm.get('relations') or []:
            strength = bridges.strength(rel.get('type')) if isinstance(rel, dict) else None
            if strength:
                held['strong' if strength == 'same' else 'weak'] += 1
    kinds = bridges.declared(catalog)
    return {'declared': kinds, 'relations_held': held,
            'next': ('sb bridges --undo takes the strong type back' if kinds['strong'] else
                     'sb bridges --upgrade adds the strong type (same_concept_as); sb bridges --undo takes it back')}


_LINE = re.compile(r'^(\s*-\s*)(' + '|'.join(map(re.escape, bridges.STRONG)) + r')(::)', re.M)


def _weakened_body(body: str, dropped_titles) -> str:
    """The related lines of a card body with the strong type written as the weak one (a line for a target the card
    names by the weak type already is left out)."""
    out = []
    for line in body.split('\n'):
        if _LINE.match(line):
            if any(f'[[{t}]]' in line or f'[[{t}|' in line for t in dropped_titles):
                continue
            line = _LINE.sub(lambda m: m.group(1) + bridges.WEAK[0] + m.group(3), line)
        out.append(line)
    return '\n'.join(out)


def _undo_changes(root: Path, snapshot) -> tuple[list[Change], list[dict]]:
    """The card and bridge-row writes that turn every strong relation into the weak one, and what they change."""
    changes, retyped, new_sha = [], [], {}
    for cid, card in sorted(snapshot.cards.items()):
        rels = card.fm.get('relations') or []
        if not any(isinstance(r, dict) and r.get('type') in bridges.STRONG for r in rels):
            continue
        after, n = bridges.retyped_relations(rels)
        weak_before = {r.get('target') for r in rels if isinstance(r, dict) and r.get('type') in bridges.WEAK}
        kept_targets = weak_before | {r.get('target') for r in after if isinstance(r, dict)
                                      and r.get('type') in bridges.WEAK}
        # a strong relation to a target the card names by the weak type already is left out, with its line
        dropped = [r.get('target') for r in rels if isinstance(r, dict) and r.get('type') in bridges.STRONG
                   and r.get('target') in weak_before]
        dropped_titles = [snapshot.cards[t].title for t in dropped if t in snapshot.cards]
        raw = card.raw if isinstance(card.raw, bytes) else card.raw.encode('utf-8')
        patched = patch_frontmatter(raw, {'relations': after})
        head, sep, body = patched.decode('utf-8').partition('\n---\n')
        text = head + sep + _weakened_body(body, dropped_titles) if sep else patched.decode('utf-8')
        data = text.encode('utf-8')
        if data != raw:
            changes.append(Change(card.path, card.sha256, data))
            new_sha[cid] = (card.sha256, digest(data))
            retyped.append({'card': cid, 'relations': n, 'weak_targets': sorted(t for t in kept_targets if t)})
    rows = pointer_store.load(root)
    if rows and (new_sha or any(r.get('type') in bridges.STRONG for r in rows)):
        moved = []
        for row in rows:
            row = dict(row)
            if row.get('type') in bridges.STRONG:
                row['type'] = bridges.WEAK[0]
            for end, sha in (('into', 'into_sha'), ('narrow', 'narrow_sha')):
                old, new = new_sha.get(row.get(end), (None, None))
                if old is not None and row.get(sha) == old:
                    row[sha] = new  # the write that retypes a card is not a change of what it says
            moved.append(row)
        change = pointer_store.change(root, moved)
        if change is not None:
            changes.append(change)
    return changes, retyped


def run(root: Path, *, upgrade: bool = False, undo: bool = False, apply: bool = False,
        job_id: str | None = None) -> dict:
    """``sb bridges``: describe, or plan and (``apply``) write the upgrade or its undo."""
    if upgrade and undo:
        raise ValueError('--upgrade and --undo are two steps; give one')
    if not (upgrade or undo):
        return describe(root)
    from . import labels
    raw, catalog = _catalog(root)
    rel = layouts.of(root).catalog
    if upgrade:
        after = bridges.upgraded(catalog, labels.recorded_language(catalog) or labels.detected_language(root))
        if after is None:
            return {'status': 'unchanged', 'declared': bridges.declared(catalog)}
        validate_catalog(after)
        changes, retyped = [Change(rel, digest(raw), jobs.encoded(after))], []
    else:
        after = bridges.downgraded(catalog)
        if after is None:
            return {'status': 'unchanged', 'declared': bridges.declared(catalog)}
        validate_catalog(after)
        snapshot = read_snapshot(root)
        card_changes, retyped = _undo_changes(root, snapshot)
        changes = [*card_changes, Change(rel, digest(raw), jobs.encoded(after))]
    out = {'step': 'upgrade' if upgrade else 'undo', 'declared_after': bridges.declared(after),
           'catalog_added' if upgrade else 'catalog_removed': list(bridges.STRONG),
           **({'cards_retyped': retyped} if undo else {}),
           'paths': [c.relative_path for c in changes],
           'next': ['sb refresh --apply'] + (['sb bridges --undo takes it back'] if upgrade else [])}
    if not apply:
        return {'status': 'planned', **out}
    job_id = job_id or f'bridges-{"upgrade" if upgrade else "undo"}-{uuid.uuid4().hex[:12]}'
    result = apply_changes(root, job_id, changes)
    return {**result, **out}

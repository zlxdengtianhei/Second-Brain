"""The article skeleton, made by the program from the taxonomy (no model).

* The map's narrative feeds the introduction.
* Pillars become sections in ``order``; a pillar's ``why`` is the point of the
  section's first paragraph.
* Topics become sub-sections in taxonomy order. Topics with fewer than
  :data:`SMALL_TOPIC` cards get no heading of their own: their cards open the
  pillar's section, before its first sub-section.
* Inside a topic, cards follow the prerequisite order of the map pages
  (:func:`secondbrain.moc.learning_order`).
* Each pillar is cut into writing units of :data:`MIN_UNIT`-:data:`MAX_UNIT`
  cards, as even as possible, cutting at a topic boundary when one is within
  :data:`SNAP` cards of the even cut. Every unit carries a target length.
* Cards of the map that have no valid (pillar, topic) form a last section, so
  no card of the scope is lost.

The result is plain JSON: it is the frozen input of the export job.
"""
from __future__ import annotations

import math
import re
from pathlib import PurePosixPath

from .. import cards as card_text
from .. import labels
from .. import moc as moc_module

SCHEMA = 'secondbrain-export/1'
SMALL_TOPIC = 3
MIN_UNIT = 8
MAX_UNIT = 15
SNAP = 3
#: Target length per card: characters for Chinese, words for English.
PER_CARD = {'zh': 250, 'en': 120}
#: Card sections the writer does not need: links (given as structured
#: relations), the flashcards and the generated diagram.
DROPPED_SECTIONS = frozenset(card_text.HEADINGS[lang][key] for lang in card_text.HEADINGS
                             for key in ('related', 'flashcards', 'diagram'))


class SkeletonError(ValueError):
    """The scope cannot be turned into an article (no map, no cards ...)."""


def body_view(body: str) -> str:
    """The card body without its links, flashcards and diagram sections."""
    out, keep = [], True
    for line in body.splitlines():
        heading = re.match(r'^##\s+(.+?)\s*$', line)
        if heading:
            keep = heading.group(1) not in DROPPED_SECTIONS
        if keep:
            out.append(line)
    return '\n'.join(out).strip() + '\n'


def _card_entry(card) -> dict:
    """What the writer gets of a card: its whole-body view (:func:`secondbrain.cards.view`, the one way packets
    read cards) without links, review cards and diagram, and how to cite it."""
    shown = card_text.view(card, 'body')
    return {'id': shown['id'], 'title': shown['title'], 'cite': link_name(card), 'kind': card.fm.get('kind'),
            'sha256': card.sha256, 'body': body_view(shown['body'])}


def link_name(card) -> str:
    """What goes inside ``[[...]]`` for this card (the file name, as Obsidian resolves it)."""
    return PurePosixPath(card.path).stem


def _moc_of(snapshot, domain: str) -> str:
    entry = (snapshot.catalog.get('domains') or {}).get(domain) if isinstance(snapshot.catalog, dict) else None
    if not isinstance(entry, dict) or not entry.get('moc'):
        raise SkeletonError(f'Domain {domain!r} has no map (MOC) in the catalog')
    return entry['moc']


def resolve_scope(snapshot, *, domain: str | None = None, moc: str | None = None,
                  ids: list[str] | None = None) -> tuple[str, list[str], list]:
    """``(map title, domains of that map, cards in scope)``."""
    if bool(domain) == bool(moc):
        raise SkeletonError('Give exactly one of --domain or --moc')
    title = moc or _moc_of(snapshot, domain)
    domains = sorted(d for d, e in (snapshot.catalog.get('domains') or {}).items()
                     if isinstance(e, dict) and e.get('moc') == title)
    if not domains:
        raise SkeletonError(f'No domain uses the map {title!r}')
    chosen = [c for c in snapshot.cards.values() if c.domain in domains]
    if ids:
        missing = sorted(set(ids) - {c.id for c in chosen})
        if missing:
            raise SkeletonError(f'These ids are not cards of the map {title!r}: {missing}')
        chosen = [c for c in chosen if c.id in set(ids)]
    if not chosen:
        raise SkeletonError(f'The map {title!r} has no cards in scope')
    return title, domains, sorted(chosen, key=lambda c: c.id)


def _cuts(sizes: list[int]) -> list[int]:
    """Card positions where a pillar of topics with ``sizes`` is cut into units."""
    total = sum(sizes)
    count = max(1, math.ceil(total / MAX_UNIT))
    if count == 1:
        return []
    bounds, pos = [], 0
    for size in sizes[:-1]:
        pos += size
        bounds.append(pos)
    cuts, last = [], 0
    for i in range(1, count):
        ideal = round(i * total / count)
        near = [b for b in bounds if abs(b - ideal) <= SNAP and MIN_UNIT <= b - last <= MAX_UNIT]
        cut = min(near, key=lambda b: (abs(b - ideal), b)) if near else ideal
        if cut <= last:
            cut = ideal if ideal > last else last + 1
        cuts.append(cut)
        last = cut
    if total - last > MAX_UNIT:  # snapping left the tail too long: fall back to even cuts
        return [round(i * total / count) for i in range(1, count)]
    return cuts


def _relations(unit_cards: list, cards_by_id: dict) -> list[dict]:
    inside = {c.id for c in unit_cards}
    seen, out = set(), []
    for card in unit_cards:
        for rel in moc_module._relations(card):
            target = rel.get('target')
            kind = rel.get('type')
            if target not in inside or target == card.id or not isinstance(kind, str):
                continue
            key = frozenset((card.id, target))
            if key in seen:
                continue
            seen.add(key)
            row = {'from': link_name(card), 'type': kind, 'to': link_name(cards_by_id[target])}
            if rel.get('note'):
                row['note'] = str(rel['note'])
            out.append(row)
    return out


def build(snapshot, *, domain: str | None = None, moc: str | None = None, ids: list[str] | None = None,
          mode: str = 'sequential', language: str = 'zh', tag: str = '') -> dict:
    """The frozen job input for one export (see the module docstring)."""
    if mode not in {'sequential', 'parallel'}:
        raise SkeletonError('mode must be sequential or parallel')
    if language not in PER_CARD:
        raise SkeletonError(f'language must be one of {sorted(PER_CARD)}')
    title, domains, chosen = resolve_scope(snapshot, domain=domain, moc=moc, ids=ids)
    taxonomy = snapshot.taxonomies.get(title)
    issues: list = []
    pillars, pillar_topics, narrative, _ = moc_module._taxonomy_structure(taxonomy, title, issues)
    by_id = {c.id: c for c in chosen}
    placed: set = set()
    sections = []
    for name, order, why, topics in pillars:
        parts = []
        small = []
        for topic in topics:
            group = [c for c in chosen if c.fm.get('pillar') == name and c.fm.get('topic') == topic]
            if not group:
                continue
            ordered, _ = moc_module.learning_order(group)
            if len(group) < SMALL_TOPIC:
                small.extend(ordered)
            else:
                parts.append((topic, ordered))
        if small:
            ordered, _ = moc_module.learning_order(small)
            parts.insert(0, (None, ordered))
        if parts:
            sections.append({'pillar': name, 'order': order, 'why': why, 'parts': parts})
            placed.update(c.id for _, group in parts for c in group)
    rest = [c for c in chosen if c.id not in placed]
    if rest:
        ordered, _ = moc_module.learning_order(rest)
        sections.append({'pillar': labels.export(language)['residual'], 'order': len(sections) + 1, 'why': '',
                         'parts': [(None, ordered)], 'residual': True})

    units, out_sections = [], []
    for section in sections:
        flat = [(topic, card) for topic, group in section['parts'] for card in group]
        cuts = [0, *_cuts([len(g) for _, g in section['parts']]), len(flat)]
        section_units = []
        for start, end in zip(cuts, cuts[1:]):
            slice_ = flat[start:end]
            parts, seen_topics = [], set()
            for topic, card in slice_:
                if parts and parts[-1]['topic'] == topic:
                    parts[-1]['cards'].append(card.id)
                    continue
                first_time = topic not in seen_topics and not any(
                    t == topic for t, _ in flat[:start])
                seen_topics.add(topic)
                parts.append({'part': f'p{len(parts) + 1}', 'topic': topic,
                              'heading': topic if (topic and first_time) else None,
                              'cards': [card.id]})
            for part in parts:
                part['target_length'] = PER_CARD[language] * len(part['cards'])
            unit_cards = [card for _, card in slice_]
            unit = {'id': f'u{len(units) + 1:02d}', 'pillar': section['pillar'], 'why': section['why'],
                    'first_in_section': not section_units, 'parts': parts,
                    'target_length': sum(p['target_length'] for p in parts),
                    'relations': _relations(unit_cards, by_id)}
            units.append(unit)
            section_units.append(unit['id'])
        out_sections.append({'pillar': section['pillar'], 'order': section['order'], 'why': section['why'],
                             'units': section_units, 'residual': bool(section.get('residual'))})

    cards = {c.id: _card_entry(c) for c in chosen}
    links = sorted({link_name(c) for c in snapshot.cards.values()} | {c.title for c in snapshot.cards.values()})
    return {'schema': SCHEMA, 'moc': title, 'domains': domains, 'narrative': narrative,
            'language': language, 'mode': mode, 'tag': tag, 'length_unit': 'chars' if language == 'zh' else 'words',
            'sections': out_sections, 'units': units, 'cards': cards, 'links': links,
            'issues': issues}

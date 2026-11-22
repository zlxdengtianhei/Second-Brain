"""Three levels of storage: domain, pillar, card.

A pillar written without ``topics`` in its taxonomy file (or whose only topic is its own name) files its cards
directly: it has one implicit topic, named like the pillar. Everything that reads the taxonomy sees that topic
(``topics: [<pillar>]``), so placement, shelves, review order and the checks of ingest work as before; nothing
that is stored names it: a card of such a pillar has no ``topic`` in its frontmatter, the registry note has none,
and the taxonomy file has no ``topics`` list. A pillar with topics of its own keeps the four levels it had.

This module holds that one rule; the readers (:func:`normalise_taxonomy`, :func:`card_topic`) and the writers
(:func:`stored_taxonomy`, :func:`stored_topic`) call it.
"""
from __future__ import annotations

import copy


def pillar_topics(pillar: dict) -> list:
    """The topics of ``pillar`` as the program reads them: its own list, else the implicit one."""
    topics = pillar.get('topics')
    if 'topics' not in pillar or topics is None:
        return [pillar.get('name')]
    return topics


def is_flat(pillar: dict) -> bool:
    """True when ``pillar`` files its cards directly (no topics of its own)."""
    name = pillar.get('name')
    return isinstance(name, str) and pillar_topics(pillar) == [name]


def normalise_taxonomy(parsed):
    """A copy of a parsed taxonomy file in which every pillar without ``topics`` has the implicit one."""
    if not isinstance(parsed, dict) or not isinstance(parsed.get('pillars'), list):
        return parsed
    out = dict(parsed)
    pillars = []
    for p in parsed['pillars']:
        if isinstance(p, dict) and isinstance(p.get('name'), str) and p.get('topics') is None:
            p = {**p, 'topics': [p['name']]}
        pillars.append(p)
    out['pillars'] = pillars
    return out


def stored_taxonomy(taxonomy):
    """A copy of ``taxonomy`` as it is written: a flat pillar loses its ``topics`` list."""
    if not isinstance(taxonomy, dict) or not isinstance(taxonomy.get('pillars'), list):
        return taxonomy
    out = copy.deepcopy(taxonomy)
    for p in out['pillars']:
        if isinstance(p, dict) and is_flat(p):
            p.pop('topics', None)
    return out


def stored_topic(pillar, topic):
    """The ``topic`` a card or a registry note stores: none when it is its pillar's implicit topic."""
    return None if topic is not None and topic == pillar else topic


def card_topic(fm: dict, pillars_by_name: dict):
    """The topic of a card's frontmatter as the program reads it: the stored one, else the implicit topic of its
    pillar when that pillar has it (``pillars_by_name``: the normalised pillars of the card's map)."""
    topic = fm.get('topic')
    if topic:
        return topic
    pillar = fm.get('pillar')
    p = pillars_by_name.get(pillar) if isinstance(pillar, str) else None
    if isinstance(p, dict) and pillar in (p.get('topics') or []):
        return pillar
    return topic

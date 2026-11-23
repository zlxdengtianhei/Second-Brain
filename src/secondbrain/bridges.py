"""Bridges between domains: one concept in two domains stays two narrow cards, joined by one relation.

A vault keeps three levels (domain, pillar, card), and a card is filed under one domain. When the same concept
turns up in a second domain it must not be folded into the card of the first: that card stays where it was
written, and the second domain would have no card of its own for its reader. So both cards are kept narrow, and
the pair is joined by a *bridge* -- a relation between two cards of two domains.

The strength of a bridge follows SKOS (W3C, section 10, the mapping properties between concept schemes):

- ``STRONG`` says the same concept (SKOS ``exactMatch``): it is transitive, so three cards joined by strong
  bridges are one concept.
- ``WEAK`` says a close one (SKOS ``closeMatch``): it is never transitive, because chaining close matches is how
  two different ideas get fused. SKOS gives this warning for exactly that reason.

The relation type carries the strength, so nothing else has to record it. A vault declares the types it lets a
card hold (``relation_types`` in the catalog, or ``domain_relations`` for one domain); a vault without a strong
type gets weak bridges, and the program says so rather than inventing a type the vault does not know.

Nothing here calls a model and nothing here decides whether two cards are one concept: the merge check judges
that, and the program only refuses to fold cards of two domains and joins them instead (:func:`keep_apart`).
"""
from __future__ import annotations

import re

#: Relation types that say "the same concept as" (SKOS exactMatch); transitive.
STRONG = ('same_concept_as',)
#: Relation types that say "close to" (SKOS closeMatch); never transitive.
WEAK = ('similar_to',)
#: Every type that joins two cards of two domains.
TYPES = STRONG + WEAK

#: Words a title shares with every other title: they say nothing about which concept a card is about.
_STOP = frozenset("""a an the this that these those its it is are was were be been and or of to in on at for by with
from into than then them they their there here what which who how why when where all any can could may might must
not but also more most less least one two three use used uses using same other into about over under between
when while where""".split())

_WORD = re.compile(r'[a-z0-9]+')


def strength(kind) -> str | None:
    """``'same'`` for a strong bridge, ``'close'`` for a weak one, None for any other relation type."""
    if kind in STRONG:
        return 'same'
    if kind in WEAK:
        return 'close'
    return None


def is_bridge(kind) -> bool:
    """True for a relation type that may join two cards of two domains."""
    return strength(kind) is not None


def choose(open_from, open_into) -> str | None:
    """The bridge type to write between a card of one domain and a card of another, or None.

    ``open_from`` and ``open_into`` are the relation types each domain lets its cards hold. A bridge is
    symmetric (both cards name the other), so the type must be open to both. A strong bridge is preferred; a
    vault that declares no strong type gets a weak one, which claims less.
    """
    both = set(open_from) & set(open_into)
    for kind in TYPES:
        if kind in both:
            return kind
    return None


def crossing(relations, domain_of, own_domain) -> list[dict]:
    """The bridges among ``relations``: ``[{'type', 'target', 'domain', 'strength'}]`` for every target that stands
    in another domain than ``own_domain``.

    ``domain_of`` maps a card id to its domain (None for a card it does not know, which is not counted).
    """
    out = []
    for rel in relations or []:
        if not isinstance(rel, dict):
            continue
        kind, target = rel.get('type'), rel.get('target')
        if not is_bridge(kind) or not isinstance(target, str):
            continue
        other = domain_of(target)
        if other is None or other == own_domain:
            continue
        out.append({'type': kind, 'target': target, 'domain': other, 'strength': strength(kind),
                    **({'note': rel['note']} if rel.get('note') else {})})
    return out


def same_concept(edges, a: str, b: str) -> bool:
    """Whether ``a`` and ``b`` are one concept by the strong bridges among ``edges`` (``[(from, type, to)]``).

    Only strong bridges carry: a chain of weak (close) bridges never counts, however long it is.
    """
    if a == b:
        return True
    step: dict = {}
    for source, kind, target in edges or []:
        if strength(kind) == 'same':
            step.setdefault(source, set()).add(target)
            step.setdefault(target, set()).add(source)
    seen, queue = {a}, [a]
    while queue:
        for nxt in step.get(queue.pop(), ()):  # noqa: B007 - the popped card
            if nxt == b:
                return True
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return False


def _words(text) -> set:
    return {w for w in _WORD.findall((text or '').lower()) if len(w) >= 3 and w not in _STOP}


def _keys(card) -> set:
    """The cheap keys a card is looked up by: its title words and its aliases, normalized (no model, no index)."""
    keys = {f'w:{w}' for w in _words(card.title)}
    for alias in (card.fm.get('aliases') or []):
        text = ' '.join(_WORD.findall(str(alias).lower()))
        if text:
            keys.add(f'a:{text}')
    return keys


def _bridged(cards) -> set:
    """Pairs already joined by a bridge, in either direction (``frozenset`` of two card ids)."""
    out = set()
    for card in cards.values():
        for rel in card.fm.get('relations') or []:
            if isinstance(rel, dict) and is_bridge(rel.get('type')) and isinstance(rel.get('target'), str):
                out.add(frozenset((card.id, rel['target'])))
    return out


def candidate_pairs(cards, *, limit: int = 200) -> list[dict]:
    """Cross-domain pairs that share a cheap key and have no bridge yet: the pairs a merge check should judge.

    The keys are what costs nothing to read: the words of the title (without words every title shares) and the
    aliases. Two cards of two domains are a candidate when they share an alias, or when they share at least two
    title words and half of the words of the shorter title. Cards whose domains are the same are never candidates,
    and neither is a pair a bridge already joins.
    """
    known = {cid: _keys(card) for cid, card in cards.items()}
    bridged = _bridged(cards)
    out = []
    ids = sorted(cards)
    for i, a in enumerate(ids):
        card_a = cards[a]
        for b in ids[i + 1:]:
            card_b = cards[b]
            if card_a.domain == card_b.domain or frozenset((a, b)) in bridged:
                continue
            shared = known[a] & known[b]
            aliases = {k for k in shared if k.startswith('a:')}
            words = {k for k in shared if k.startswith('w:')}
            smaller = min(len({k for k in known[a] if k.startswith('w:')}),
                          len({k for k in known[b] if k.startswith('w:')})) or 0
            if not aliases and not (len(words) >= 2 and smaller and len(words) * 2 >= smaller):
                continue
            out.append({'a': a, 'b': b, 'domains': [card_a.domain, card_b.domain],
                        'shared': sorted(k.split(':', 1)[1] for k in shared)})
    out.sort(key=lambda p: (-len(p['shared']), p['a'], p['b']))
    return out[:limit]


def report(cards) -> dict:
    """The cross-domain reading of a vault: ``bridges`` (strong, weak, by domain pair) and ``candidates``.

    ``bridges`` counts *pairs*: a bridge written on both cards (each end names the other, as an inverse relation
    does) is one bridge, and so is one written on a single card. ``candidates`` counts the cross-domain pairs that
    share a cheap key and no bridge yet (a merge check would judge each of them).
    """
    domain_of = {cid: card.domain for cid, card in cards.items()}
    pairs: dict = {}
    for card in cards.values():
        for bridge in crossing(card.fm.get('relations'), lambda i: domain_of.get(i), card.domain):
            pairs.setdefault(frozenset((card.id, bridge['target'])), {**bridge, 'own': card.domain})
    strong = sum(1 for b in pairs.values() if b['strength'] == 'same')
    weak = len(pairs) - strong
    by_pair: dict = {}
    for bridge in pairs.values():
        domains = sorted((bridge['own'], bridge['domain']))
        key = '|'.join(domains)
        by_pair[key] = by_pair.get(key, 0) + 1
    return {'bridges': len(pairs), 'strong': strong, 'weak': weak,
            'by_domain_pair': dict(sorted(by_pair.items())),
            'candidates': len(_all_candidates(cards))}


def _all_candidates(cards) -> list[dict]:
    """Every cross-domain candidate pair, uncapped (``candidate_pairs`` caps its list for reading)."""
    return candidate_pairs(cards, limit=10 ** 6)


def keep_apart(rows: dict, domain_of, open_types) -> tuple[dict, list[dict]]:
    """Levels of the merge check with every merge across two domains turned into a keep, and the bridges to write.

    ``rows`` maps a card's slug to its level against the other cards of the source (``overlap.source``: ``overlap``
    and ``with``). ``domain_of`` maps a slug to the domain its card stands in, ``open_types`` a domain to the
    relation types it lets its cards hold. A card judged ``extends`` or ``same`` as a card of *another* domain is
    not merged: both stay narrow cards, and the pair gets a bridge of the strongest type both domains accept.

    Returns ``(rows, bridges)``: the rows with those levels rewritten to ``distinct`` (a ``bridges`` list names the
    card it keeps beside, and ``reason`` says why), and ``[{'slug', 'into', 'domains', 'type', 'reason'}]``. A
    pair with no bridge type open to both domains is still kept apart; its ``type`` is None, and the reason says
    what the catalog is missing.
    """
    out, found = dict(rows), []
    for slug, row in rows.items():
        level = row.get('overlap')
        into = (row.get('with') or [None])[0]
        if level == 'distinct' or not into:
            continue
        here, there = domain_of(slug), domain_of(into)
        if here is None or there is None or here == there:
            continue
        kind = choose(open_types(here) or (), open_types(there) or ())
        reason = (f'{into} stands in domain {there!r} and this card in {here!r}: one concept in two domains keeps '
                  f'a narrow card in each, joined by a bridge' + (f' ({kind})' if kind else
                                                                  ' -- no bridge type is open to both domains'))
        out[slug] = {'overlap': 'distinct', 'with': [], 'bridges': [into],
                     'kept_from': level, 'reason': reason}
        found.append({'slug': slug, 'into': into, 'domains': [here, there], 'type': kind, 'reason': reason})
    return out, found

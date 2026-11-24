"""Bridges and pointers between domains: one concept has one description, kept in the domain that had it first.

A vault keeps three levels (domain, pillar, card), and a card is filed under one domain. When the same concept
turns up in a second domain, one rule decides what becomes of it (:func:`settle`), by the level a judge gave and
the two domains:

- ``point`` -- the level is ``same`` (SKOS ``exactMatch``): it is one concept. No second card is written. The
  second domain's pillar page carries a *pointer* to the canonical card (the one of the domain that had the
  concept first), and the description stays in that card: one description cannot drift from itself. When the
  canonical card changes, the pillar page that points at it is built again (:mod:`secondbrain.pointers`,
  :func:`secondbrain.moc.build_mocs`).
- ``apart`` -- the level is ``extends`` (SKOS ``closeMatch``): one concept, and the second domain says something
  about it that the first card does not hold (a new mechanism, condition or conclusion). Each keeps a narrow card of
  its own (the second one written next to the first, with the emphasis of its own domain), and a *strong* bridge
  (``same_concept_as``) joins them when the catalog declares it.

A third strength is not this rule's: two cards of two domains that are alike but answer different questions are
joined by a *weak* bridge (``similar_to``, SKOS ``relatedMatch``), which the producer writes like any relation. A
weak bridge is never chained: a chain of "alike" is how two different ideas get fused. A strong bridge is the
program's own step on a pair the judge found one concept (``extends``); a strong type a card names otherwise is
written as the weak one (:func:`retype_unjudged`).
- ``drop`` -- ``same`` inside one domain: the card is not written.

A relation type carries the strength of a bridge, so nothing else has to record it. A vault declares the types it
lets a card hold (``relation_types`` in the catalog, or ``domain_relations`` for one domain); a vault without a
strong type gets weak bridges, and the program says so rather than inventing a type the vault does not know.
:func:`upgraded` gives the catalog that declares it and :func:`downgraded` the one that does not
(``sb bridges --upgrade`` and ``--undo``, :mod:`secondbrain.bridgetypes`).

Nothing here calls a model and nothing here decides whether two cards are one concept: the merge check judges
that, and the program only refuses to fold cards of two domains. That one rule lives here (:func:`settle`): every
step that weighs a judged level against two domains asks it, so the levels of the chunk reviewer and of the merge
check cannot disagree about what a repeat across two domains becomes.
"""
from __future__ import annotations

import json
import re

#: Relation types that say "the same concept as, with the emphasis of another domain" (a judged ``extends``
#: across two domains); transitive: cards joined by them answer one question.
STRONG = ('same_concept_as',)
#: Relation types that say "alike, but another question" (SKOS relatedMatch); never transitive.
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


def choose(open_from, open_into, inverse=None) -> str | None:
    """The bridge type to write between a card of one domain and a card of another, or None.

    ``open_from`` and ``open_into`` are the relation types each domain lets its cards hold. A bridge is
    symmetric (both cards name the other), so the type must be open to both, and the back link the target gets
    (``inverse``: type -> its inverse type) must be open to the other domain too. A strong bridge is preferred; a
    vault that declares no strong type gets a weak one, which claims less.
    """
    both = set(open_from) & set(open_into)
    back_of = inverse or {}
    for kind in TYPES:
        if kind in both and back_of.get(kind, kind) in both:
            return kind
    return None


def settle(side: str, level, into, here, there, open_types=None, inverse=None) -> dict:
    """What becomes of a card judged ``level`` against ``into``: the one rule for a repeat, with its bridge.

    ``side`` is where the level was judged: ``'existing'`` (the chunk reviewer, against the vault's cards) or
    ``'source'`` (the merge check, against the other cards of the source). ``level`` is one of
    :data:`secondbrain.overlap.LEVELS`, or None for a card of the patch set whose level the caller has already
    settled (it is a repeat, whatever the level is called). ``here`` and ``there`` are the domains of the card and
    of ``into``; ``open_types(domain)`` gives the relation types a domain lets its cards hold, and ``inverse`` the
    catalog's pairs of inverse types. Both are asked only when a bridge is to be written.

    Returns ``{'how', 'type', 'reason'}``. ``how`` is

    - ``'distinct'``: the level's own outcome, the card stands on its own;
    - ``'merge'``: it goes into ``into`` (a patch, a carry, or nothing to carry), as the level says;
    - ``'drop'``: it is not written at all (``same`` against the vault, in the same domain);
    - ``'point'``: one concept in two domains -- no second card is written; the second domain's pillar page gets a
      pointer to ``into``, the one card that holds the description;
    - ``'apart'``: two domains say different things about it -- both keep a narrow card of their own and a bridge
      of ``type`` joins them (None when the catalog opens no type to both), ``reason`` says why.

    Two cards of two domains are never merged, whatever the level says; two cards of one domain are never kept
    apart, and one of them is never pointed at.
    """
    if level == 'distinct':
        return {'how': 'distinct', 'type': None, 'reason': None}
    if into and here is not None and there is not None and here != there:
        if level == 'same':
            return {'how': 'point', 'type': None,
                    'reason': (f'{into} stands in domain {there!r} and this card in {here!r}: it is the same concept, '
                               'so this domain gets a pointer to that card and no second description')}
        kind = choose(open_types(here) or (), open_types(there) or (), inverse) if open_types else None
        return {'how': 'apart', 'type': kind,
                'reason': (f'{into} stands in domain {there!r} and this card in {here!r}: one concept in two domains '
                           'keeps a narrow card in each, joined by a bridge' +
                           (f' ({kind})' if kind else
                            ' -- the catalog opens no bridge type to both domains, so no bridge was written'))}
    if side == 'existing' and level == 'same':
        return {'how': 'drop', 'type': None, 'reason': None}
    return {'how': 'merge', 'type': None, 'reason': None}


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


def report(cards, rows=None) -> dict:
    """The cross-domain reading of a vault: ``bridges``, ``pointers``, ``candidates`` and what waits to be looked at.

    ``bridges`` counts *pairs*: a bridge written on both cards (each end names the other, as an inverse relation
    does) is one bridge, and so is one written on a single card. ``pointers`` counts the rows of
    :mod:`secondbrain.pointers` that put a concept of another domain on a pillar page instead of writing a second
    card. ``candidates`` counts the cross-domain pairs that share a cheap key and no bridge yet (a merge check
    would judge each of them). ``waiting`` counts the bridges whose canonical card changed after the bridge was
    written: those narrow cards are to be looked at again.
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
    from . import pointers as pointer_store
    rows = list(rows or ())
    return {'bridges': len(pairs), 'strong': strong, 'weak': weak,
            'by_domain_pair': dict(sorted(by_pair.items())),
            'pointers': len([r for r in rows if r.get('kind') == 'pointer']),
            'waiting': len(pointer_store.waiting(rows, cards)),
            'candidates': len(_all_candidates(cards))}


def _all_candidates(cards) -> list[dict]:
    """Every cross-domain candidate pair, uncapped (``candidate_pairs`` caps its list for reading)."""
    return candidate_pairs(cards, limit=10 ** 6)


def keep_apart(rows: dict, domain_of, open_types, inverse=None) -> tuple[dict, list[dict]]:
    """Levels of the merge check with every merge across two domains turned into its rule's result, and the bridges.

    ``rows`` maps a card's slug to its level against the other cards of the source (``overlap.source``: ``overlap``
    and ``with``). ``domain_of`` maps a slug to the domain its card stands in, ``open_types`` a domain to the
    relation types it lets its cards hold, ``inverse`` the catalog's inverse types. A card judged ``same`` or
    ``extends`` as a card of *another* domain is not merged: :func:`settle` decides whether it becomes a pointer
    (``same``: no second card, the pillar page of its domain points at the card in ``with``) or a narrow card of
    its own joined by a bridge of the strongest type both domains accept (``extends``).

    Returns ``(rows, found)``: the rows with those levels rewritten to ``distinct`` (a ``pointed`` or ``bridges``
    list names the card it keeps beside, and ``reason`` says why), and ``[{'slug', 'into', 'how', 'domains',
    'type', 'reason'}]``. A pair with no bridge type open to both domains is still kept apart; its ``type`` is
    None, and the reason says what the catalog is missing.
    """
    out, found = dict(rows), []
    for slug, row in rows.items():
        level = row.get('overlap')
        into = (row.get('with') or [None])[0]
        if level == 'distinct' or not into:
            continue
        here, there = domain_of(slug), domain_of(into)
        settled = settle('source', level, into, here, there, open_types, inverse)
        if settled['how'] == 'point':
            out[slug] = {'overlap': 'distinct', 'with': [], 'pointed': into, 'kept_from': level,
                         'reason': settled['reason']}
            found.append({'slug': slug, 'into': into, 'how': 'point', 'domains': [here, there], 'type': None,
                          'reason': settled['reason']})
        elif settled['how'] == 'apart':
            out[slug] = {'overlap': 'distinct', 'with': [], 'bridges': [into], 'kept_from': level,
                         'reason': settled['reason']}
            found.append({'slug': slug, 'into': into, 'how': 'apart', 'domains': [here, there],
                          'type': settled['type'], 'reason': settled['reason']})
    return out, found


def strong_gloss(language: str | None = None) -> str:
    """What ``sb bridges --upgrade`` writes into a catalog as the strong type's gloss, in ``language`` (the type is
    its own inverse, so one gloss reads both ways)."""
    from . import labels
    return labels.pages(language if language in ('en', 'zh') else 'en')['strong_bridge_gloss']


def declared(catalog) -> dict:
    """The bridge types a catalog declares (open to every domain or to some): ``{'strong': [...], 'weak': [...]}``."""
    from .catalog import relation_types
    known = set(relation_types(catalog or {}))
    return {'strong': [k for k in STRONG if k in known], 'weak': [k for k in WEAK if k in known]}


def upgraded(catalog: dict, language: str | None = None) -> dict | None:
    """``catalog`` with the strong bridge type open to every domain, or None when it declares one already.

    The type joins ``relation_types`` (open to every domain: a bridge needs it on both sides), is its own inverse
    (a bridge is symmetric) and gets the gloss of ``language`` unless the catalog glosses it already. Nothing else
    changes, so :func:`downgraded` gives the catalog back.
    """
    if declared(catalog)['strong']:
        return None
    kind = STRONG[0]
    out = json.loads(json.dumps(catalog))
    out.setdefault('relation_types', []).append(kind)
    out.setdefault('inverse_relations', {})[kind] = kind
    glosses = out.setdefault('relation_gloss', {})
    if not (isinstance(glosses.get(kind), str) and glosses[kind].strip()):
        glosses[kind] = strong_gloss(language)
    return out


def downgraded(catalog: dict) -> dict | None:
    """``catalog`` with every strong bridge type taken out, or None when it declares none.

    Out of ``relation_types``, every ``domain_relations`` list, ``inverse_relations`` (as a key and as a value) and
    ``relation_gloss``. Cards that still hold the type are the caller's to retype first (:func:`retyped_relations`).
    """
    if not declared(catalog)['strong']:
        return None
    out = json.loads(json.dumps(catalog))
    strong = set(STRONG)
    out['relation_types'] = [t for t in out.get('relation_types') or [] if t not in strong]
    for domain, words in list((out.get('domain_relations') or {}).items()):
        out['domain_relations'][domain] = [w for w in words if w not in strong]
    out['inverse_relations'] = {k: v for k, v in (out.get('inverse_relations') or {}).items()
                                if k not in strong and v not in strong}
    if isinstance(out.get('relation_gloss'), dict):
        out['relation_gloss'] = {k: v for k, v in out['relation_gloss'].items() if k not in strong}
        if not out['relation_gloss']:  # the upgrade wrote the only gloss: the catalog had none
            del out['relation_gloss']
    return out


def retyped_relations(relations) -> tuple[list, int]:
    """``relations`` with each strong bridge written as the weak type: ``(relations, how many changed)``.

    A strong relation to a target the card names already by the weak type is left out (one relation per pair).
    """
    out, changed = [], 0
    weak_targets = {r.get('target') for r in relations or [] if isinstance(r, dict) and r.get('type') in WEAK}
    for rel in relations or []:
        if isinstance(rel, dict) and rel.get('type') in STRONG:
            changed += 1
            if rel.get('target') in weak_targets:
                continue
            weak_targets.add(rel.get('target'))
            rel = {**rel, 'type': WEAK[0]}
        out.append(rel)
    return out, changed


def retype_unjudged(relations, domain: str, domain_of, judged=()) -> tuple[list, list]:
    """Strong bridges no judge gave, written as the weak type: ``(relations, [{'target', 'was', 'now'}])``.

    ``relations`` are a new card's, ``domain`` its domain, ``domain_of`` maps a card id or slug to its domain, and
    ``judged`` names the targets the rule (:func:`settle`, ``apart``) joined to this card. A strong type toward any
    other card of another domain says "one concept" without a judge having said so: it is written as
    :data:`WEAK`. A strong type inside one domain is left as it is (it is not a bridge).
    """
    out, changed = [], []
    judged = set(judged or ())
    for rel in relations or []:
        if isinstance(rel, dict) and rel.get('type') in STRONG and rel.get('target') not in judged:
            other = domain_of(rel.get('target')) if isinstance(rel.get('target'), str) else None
            if other is not None and other != domain:
                changed.append({'target': rel['target'], 'was': rel['type'], 'now': WEAK[0]})
                rel = {**rel, 'type': WEAK[0]}
        out.append(rel)
    return out, changed

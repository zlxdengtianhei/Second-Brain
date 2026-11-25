"""What a card says about the other domains that use its concept: the one reading of bridges and pointers.

A concept that two domains both meet is kept once (:mod:`secondbrain.bridges`): the second domain either points at
the canonical card (``same``; :mod:`secondbrain.pointers`) or keeps a narrow card of its own joined to the first
by a bridge (``extends``). A reader of one domain should still learn that the other one uses the concept, and what
it says there. The explanation pages (:mod:`secondbrain.explain`) and the daily review page
(:mod:`secondbrain.projections`) both ask this module, so the two say the same thing about the same card.

Nothing here writes and nothing here asks a model: the answers are read from the cards' relations and the pointer
rows, and the sentence a reader sees is put together by the program from them (a model could leave it out or
change it; the program cannot).
"""
from __future__ import annotations

from . import bridges
from . import pointers as pointer_store
from .cards import essence_of


def _clip(text: str, n: int) -> str:
    text = ' '.join((text or '').split())
    return text if len(text) <= n else text[:n - 1] + '…'


def domain_title(snapshot, domain: str) -> str:
    """What a reader calls a domain: the title of its map."""
    return ((snapshot.catalog.get('domains') or {}).get(domain) or {}).get('moc') or domain


def bridged(snapshot, card) -> list[dict]:
    """The bridges this card is one end of: the same concept standing in another domain.

    Read from both directions, because a bridge is written on the card the merge check kept apart, and the card
    it was kept apart from -- written earlier, or by another source -- names nothing. Each row says the other
    card (``target``, ``title``), its domain (``domain``, ``domain_title``), the relation (``type``) and its
    ``strength`` (``same`` or ``close``), what that side says (``essence``) and, when the bridge has one, where the
    two differ (``note``: the judge's reason, written on the relation).
    """
    domain_of = {cid: c.domain for cid, c in snapshot.cards.items()}

    def show(other_id, other_domain, bridge):
        other = snapshot.cards.get(other_id)
        return {'target': other_id, 'title': (other.title if other is not None else other_id),
                'domain': other_domain, 'domain_title': domain_title(snapshot, other_domain),
                'strength': bridge['strength'], 'type': bridge['type'],
                # what that side says: the sentence a reader gets is "this concept is also used there, and there
                # it says ..." (the canonical card names the narrow card, the narrow card the canonical one)
                **({'essence': _clip(essence_of(other.body), 160)} if other is not None else {}),
                **({'note': _clip(bridge['note'], 240)} if bridge.get('note') else {})}

    def noted(other, bridge):
        """The bridge as ``bridge`` of this card's side, with the note the other end holds when this side has none
        (the judge's reason is written on the narrow card, the one that was kept apart)."""
        if bridge.get('note') or other is None:
            return bridge
        for b in bridges.crossing(other.fm.get('relations'), domain_of.get, other.domain):
            if b['target'] == card.id and b.get('note'):
                return {**bridge, 'note': b['note']}
        return bridge

    out = [show(b['target'], b['domain'], noted(snapshot.cards.get(b['target']), b))
           for b in bridges.crossing(card.fm.get('relations'), domain_of.get, card.domain)
           if isinstance(b.get('target'), str)]
    seen = {b['target'] for b in out}
    for other in snapshot.cards.values():  # the other end: it names this card, in its own domain
        if other.id == card.id or other.domain == card.domain or other.id in seen:
            continue
        for b in bridges.crossing(other.fm.get('relations'), domain_of.get, other.domain):
            if b['target'] == card.id:
                out.append(show(other.id, other.domain, b))
                seen.add(other.id)
                break
    return out


def pointed(snapshot, card) -> list[str]:
    """The domains whose pillar page points at ``card``: the same concept, with no description of its own there."""
    rows = pointer_store.pointers(pointer_store.load(snapshot.root))
    return list(dict.fromkeys(domain_title(snapshot, row['domain']) for row in rows if row.get('into') == card.id))


def pointed_from(snapshot, domain: str, pillar: str) -> list[dict]:
    """The concepts a pillar page holds only as a pointer: the canonical card of each, which stands in another domain.

    Each row says that card (``target``, ``title``, ``essence``) and the domain it stands in (``domain``,
    ``domain_title``). A pointer whose canonical card is gone is left out (the pillar page says so itself).
    """
    out = []
    for row in pointer_store.pointers(pointer_store.load(snapshot.root), domain=domain, pillar=pillar):
        other = snapshot.cards.get(row.get('into'))
        if other is None or other.domain == domain:
            continue
        out.append({'target': other.id, 'title': other.title, 'domain': other.domain,
                    'domain_title': domain_title(snapshot, other.domain), 'essence': _clip(essence_of(other.body), 160)})
    return out

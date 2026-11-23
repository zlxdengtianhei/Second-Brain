"""Pointers and bridges between domains: one description, kept in the domain that had the concept first.

A vault files a card under one domain. When a second domain meets the same concept, two things can become of it,
and :func:`secondbrain.bridges.settle` decides which (one rule, one place):

- ``point`` (SKOS ``exactMatch``): it is the very same concept. No second card is written. The second domain's
  pillar page carries a *pointer* -- a link to the canonical card (the card of the domain that had the concept
  first) -- and the description stays in that one card. Two descriptions would have to be kept in step; one does
  not.
- ``apart`` (SKOS ``closeMatch``): the two domains say different things about it. Each keeps a narrow card of its
  own, and a bridge relation joins the pair.

This module keeps the two records that the cards themselves cannot hold: a pointer has no card to hang a relation
on, and a bridge has to remember what the canonical card said when it was written, so that a later change can be
noticed. The file is append-only JSON lines; the last row per key wins (the key of a pointer is the domain and
pillar it points from, of a bridge the narrow card).

Nothing here decides anything: the rows say what the program wrote, and the readers (:mod:`secondbrain.moc` for
the pillar pages, :mod:`secondbrain.reviews` and :mod:`secondbrain.service` for what waits to be looked at)
derive their answers from them.
"""
from __future__ import annotations

import json

from . import layout as layouts
from .store import Change, digest, read_bytes

#: Row kinds: a pointer from a domain's pillar page, a bridge between two cards of two domains.
POINTER = 'pointer'
BRIDGE = 'bridge'


def key(row: dict) -> tuple:
    """What makes two rows the same record: a pointer by where it points from, a bridge by its narrow card."""
    if row.get('kind') == POINTER:
        return (POINTER, row.get('domain'), row.get('pillar'), row.get('into'))
    return (BRIDGE, row.get('narrow'), row.get('into'))


def load(root) -> list[dict]:
    """The rows the vault holds, one per key (the last written), in the order they were first written."""
    raw = read_bytes(root, layouts.of(root).pointers)
    rows: dict = {}
    if not raw:
        return []
    for line in raw.decode('utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a half-written line: the rows before it stand
        if not isinstance(row, dict) or row.get('kind') not in (POINTER, BRIDGE):
            continue
        rows[key(row)] = row
    return list(rows.values())


def merge(rows: list[dict], new: list[dict]) -> list[dict]:
    """``rows`` with ``new`` rows written over their keys (a new key is appended, at the end)."""
    out = {key(row): row for row in rows}
    for row in new:
        out[key(row)] = row
    return list(out.values())


def dump(rows: list[dict]) -> bytes:
    """The file's bytes: one JSON line per row, keys sorted so the same rows give the same bytes."""
    return ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in rows).encode('utf-8')


def change(root, rows: list[dict]) -> Change | None:
    """The write that makes the file hold ``rows``, or None when it already does."""
    rel = layouts.of(root).pointers
    before = read_bytes(root, rel)
    after = dump(rows)
    if before == after:
        return None
    return Change(rel, None if before is None else digest(before), after)


def pointers(rows: list[dict], *, domain: str | None = None, pillar: str | None = None) -> list[dict]:
    """The pointer rows of one domain (and pillar, when given)."""
    return [row for row in rows if row.get('kind') == POINTER
            and (domain is None or row.get('domain') == domain)
            and (pillar is None or row.get('pillar') == pillar)]


def into(rows: list[dict], card: str) -> list[dict]:
    """Every row that points at ``card`` (a pointer, or a bridge of either end)."""
    return [row for row in rows if row.get('into') == card]


def changed(rows: list[dict], cards: dict) -> list[dict]:
    """The rows whose canonical card changed after the row was written (or is gone).

    ``cards`` maps a card id to its :class:`~secondbrain.vault.Card`. A pointer whose canonical card changed has
    to be written again (its link and title are read from that card, so the next map build is right); a bridge
    whose canonical card changed leaves its narrow card to be looked at again (:func:`waiting`).
    """
    out = []
    for row in rows:
        card = cards.get(row.get('into'))
        if card is None or card.sha256 != row.get('into_sha'):
            out.append(row)
    return out


def waiting(rows: list[dict], cards: dict) -> list[dict]:
    """The bridges waiting to be looked at: the canonical card changed, the narrow card did not.

    Editing the narrow card is the act of looking at it, so from then on the pair is not waiting any more (the
    record keeps the narrow card's digest as it was when the bridge was written).
    """
    out = []
    for row in changed(rows, cards):
        if row.get('kind') != BRIDGE:
            continue
        narrow = cards.get(row.get('narrow'))
        if narrow is None or narrow.sha256 == row.get('narrow_sha'):
            out.append(row)
    return out

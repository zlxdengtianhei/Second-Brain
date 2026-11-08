"""Candidate cards: the existing cards a model is shown next to a query, and how much of each.

One entry for every role of the ingest chain (producer, chunk reviewer, merge
check, split slot): which existing cards to show for a query text, where each
came from (``via``), and at which view (:data:`secondbrain.cards.VIEWS`) it
fits the packet's character budget. A candidate is only a card to look at;
whether a new card repeats it is the reviewer's judgement, never a score.

``via`` names the route a candidate came by:

- ``named``: the producer wrote it in a relation, a ``same`` coverage row, a
  conflict or an ``overlap_hint`` (all of them are shown, at ``full``, and do
  not count toward the budget);
- ``words``: the first route, a BM25 search of the query text (top ``k``);
- ``hop``: the cards the first ``hop`` words hits link (their relations);
- ``shelf``: the cards of the topic the card is filed under (``title`` view);
- ``written``: in a retry, the cards earlier jobs of the source wrote.

Packing order is the order above; past the budget the lowest-ranked retrieved
card loses one view level at a time (``full`` → ``brief`` → ``title``), and only
then are titles left out.
"""
from __future__ import annotations

from .cards import VIEWS, view, view_chars
from .executors.base import CHUNK_TOO_LARGE

VIAS = ('named', 'written', 'words', 'hop', 'shelf')
#: Shelf size: one map page lists at most this many cards of a topic (``moc.MAX_TOPIC``).
SHELF_MAX = 25


class RetrievalError(ValueError):
    """A request the retrieval cannot answer without dropping something silently."""


class TooLarge(RetrievalError):
    """The named cards alone exceed twice the budget: the caller splits the chunk in halves."""
    code = CHUNK_TOO_LARGE


def card_query(card: dict) -> str:
    """The query text for a proposed card: title, essence and the first three propositions."""
    return ' '.join([str(card.get('title') or ''), str(card.get('essence') or '')]
                    + [str(p) for p in (card.get('propositions') or [])[:3]])


def words(root, snapshot, query: str, k: int) -> list[str]:
    """First route: the ids of the ``k`` best BM25 hits for ``query`` (fewer when the vault has fewer)."""
    from .index import search
    if k <= 0 or not query.strip():
        return []
    return [h['id'] for h in search(root, snapshot, query, k)]


def hop(snapshot, start: list[str], n: int) -> list[str]:
    """Second route: the cards the first ``n`` ids of ``start`` link, in relation order."""
    out = []
    for cid in start[:max(0, n)]:
        card = snapshot.cards.get(cid)
        for edge in (card.fm.get('relations') if card else None) or []:
            target = edge.get('target') if isinstance(edge, dict) else None
            if target in snapshot.cards and target not in start and target not in out:
                out.append(target)
    return out


def shelf(snapshot, domain, pillar, topic, *, limit: int = SHELF_MAX, rank=None) -> list[str]:
    """The cards filed under (``domain``, ``pillar``, ``topic``), best ``rank`` first, at most ``limit``."""
    ids = sorted((cid for cid, c in snapshot.cards.items()
                  if c.domain == domain and c.fm.get('pillar') == pillar and c.fm.get('topic') == topic),
                 key=lambda cid: ((rank or {}).get(cid, len(snapshot.cards)), cid))
    return ids[:limit]


def candidates(root, snapshot, query: str, *, k: int, hop_from: int = 0, named=(), written=None,
               written_k: int | None = None, shelf_of: tuple | None = None, exclude=()) -> list[dict]:
    """``[{id, rank, via}]`` for one query, in packing order.

    ``named`` ids must exist (a missing one raises :class:`RetrievalError`;
    nothing is dropped silently). ``written``, in a retry, is a snapshot of the
    cards earlier jobs of the source wrote: they are ranked among themselves
    (top ``written_k``, else ``k``) and added with ``via: written``. ``shelf_of`` is a
    (domain, pillar, topic) whose shelf is added. ``exclude`` ids are left out
    of the retrieved routes (the card itself, cards shown elsewhere).
    """
    missing = [i for i in named if i not in snapshot.cards]
    if missing:
        raise RetrievalError(f'named cards do not exist: {", ".join(sorted(missing))}')
    found: dict = {}

    def add(cid, rank, via):
        entry = found.setdefault(cid, {'id': cid, 'rank': rank, 'via': []})
        entry['rank'] = min(entry['rank'], rank)
        if via not in entry['via']:
            entry['via'].append(via)
    for rank, cid in enumerate(dict.fromkeys(named), 1):
        add(cid, rank, 'named')
    if written is not None and written.cards:
        from .search import search as rank_written
        ranked = [h['id'] for h in rank_written(written, query, len(written.cards))] if query.strip() else []
        for rank, cid in enumerate(ranked[:written_k if written_k is not None else k], 1):
            add(cid, rank, 'written')
    first = [cid for cid in words(root, snapshot, query, k) if cid not in exclude]
    for rank, cid in enumerate(first, 1):
        add(cid, rank, 'words')
    for rank, cid in enumerate(hop(snapshot, first, hop_from), 1):
        if cid not in exclude:
            add(cid, rank, 'hop')
    if shelf_of is not None:
        order = {cid: n for n, cid in enumerate(first)}
        for rank, cid in enumerate(shelf(snapshot, *shelf_of, rank=order), 1):
            if cid not in exclude:
                add(cid, rank, 'shelf')
    return sorted(found.values(), key=_packing_key)


def _primary(entry: dict) -> str:
    return min(entry['via'], key=VIAS.index)


def _packing_key(entry: dict) -> tuple:
    return VIAS.index(_primary(entry)), entry['rank'], entry['id']


def merge(lists) -> list[dict]:
    """One entry per id over several queries' candidates: best rank, every route, in packing order."""
    found: dict = {}
    for entries in lists:
        for e in entries:
            entry = found.setdefault(e['id'], {'id': e['id'], 'rank': e['rank'], 'via': []})
            entry['rank'] = min(entry['rank'], e['rank'])
            entry['via'] += [v for v in e['via'] if v not in entry['via']]
    return sorted(found.values(), key=_packing_key)


def fit(snapshot, entries: list[dict], budget: int, levels: dict) -> tuple[dict, dict]:
    """``({id: view}, report)``: the candidates at the view levels that fit ``budget`` characters.

    ``levels`` maps a ``via`` to the view a candidate of that route gets
    (its first route in :data:`VIAS` order decides). Named cards keep their
    level and do not count; past twice the budget they raise :class:`TooLarge`.
    Retrieved cards lose one level at a time, the lowest-ranked card at the
    highest level first, so better-ranked cards keep more; when every one is
    at ``title`` and the budget is still exceeded, the lowest-ranked titles
    are left out. ``report`` says how many were lowered or left out.
    """
    order = sorted(merge([entries]), key=_packing_key)
    level = {e['id']: levels.get(_primary(e), 'brief') for e in order}
    for e in order:
        if level[e['id']] not in VIEWS:
            raise ValueError(f'unknown view level {level[e["id"]]!r}')
    cache: dict = {}

    def shown(cid):
        key = (cid, level[cid])
        if key not in cache:
            cache[key] = view(snapshot.cards[cid], level[cid])
        return cache[key]
    named = [e['id'] for e in order if 'named' in e['via']]
    named_chars = sum(view_chars(shown(cid)) for cid in named)
    if named_chars > 2 * budget:
        raise TooLarge(f'the {len(named)} named cards take {named_chars} characters, more than twice the budget '
                       f'of {budget}; split the chunk')
    others = [e['id'] for e in order if 'named' not in e['via']]
    total = sum(view_chars(shown(cid)) for cid in others)
    lowered, dropped = 0, []
    while total > budget and others:
        top = max(VIEWS.index(level[cid]) for cid in others)
        if top == 0:
            cid = others.pop()
            total -= view_chars(shown(cid))
            dropped.append(cid)
            continue
        cid = next(c for c in reversed(others) if VIEWS.index(level[c]) == top)
        before = view_chars(shown(cid))
        level[cid] = VIEWS[top - 1]
        total += view_chars(shown(cid)) - before
        lowered += 1
    table = {cid: shown(cid) for cid in named + others}
    report = {'chars': named_chars + total, 'named_chars': named_chars, 'budget': budget,
              'lowered': lowered, 'left_out': dropped}
    return table, report


def keep(entries: list[dict], table: dict) -> list[dict]:
    """``entries`` without the ones :func:`fit` left out."""
    return [{'id': e['id'], 'rank': e['rank'], 'via': list(e['via'])} for e in entries if e['id'] in table]


#: First-route cards per query with ``candidates_k = "auto"``: each role's count before the setting existed
#: (the producer's is in :func:`secondbrain.pipelines.routing.route`). In drill 2, 10 of 16 old cards a judge
#: said should have been linked were in a reviewer's top 5; the merge check links, it does not judge repeats.
AUTO_K = {'reviewer': 5, 'merge_check': 3, 'written': 5}
#: In a retry, written cards shown per card with ``"auto"``: the closest, then the ones it links, this many in all.
AUTO_WRITTEN_MAX = 8


def k_for(setting, role: str) -> int:
    """The first-route count of ``role`` for the frozen ``candidates_k`` setting."""
    return AUTO_K[role] if setting in (None, 'auto') else int(setting)

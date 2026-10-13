"""Deterministic full-vault candidate retrieval and neighbor lookup.

Used by ``prepare`` to surface a bounded set of existing-card candidates for
dedup/reference, and by the runtime read path to fetch full neighbor cards.

Design notes:

* Bilingual tokenization without any model: ASCII alphanumeric words plus
  CJK unigrams/bigrams, then BM25 (k1=1.2, b=0.75) scoring. This is
  *candidate retrieval*, not semantic dedup: a top score means lexical
  overlap, never that two cards are duplicates or the query is answered.
* Everything is deterministic: ties are broken by card id, results come back
  in a stable order, and the same snapshot+query yields the same bytes.
* Never truncates a card or reads outside ``snapshot.cards``.
"""
from __future__ import annotations

import math
import re

from .vault import Snapshot, card_packet

# ASCII words/numbers, lowercased later.
_LATIN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
# CJK Unified Ideographs (+ CJK Extension A) runs -> unigrams + bigrams.
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")

_K1 = 1.2
_B = 0.75
DEFAULT_LIMIT = 12
_SNIPPET_MAX = 300


class SearchError(ValueError):
    """Errors raised by this module (always about arguments, never content)."""


def _tokenize(text: str) -> list[str]:
    """Lowercase ASCII words + CJK unigrams/bigrams from ``text``."""
    if not text:
        return []
    text = text.lower()
    tokens: list[str] = []
    tokens.extend(_LATIN_RE.findall(text))
    for run in _HAN_RE.findall(text):
        for ch in run:
            tokens.append(ch)
        if len(run) >= 2:
            for i in range(len(run) - 1):
                tokens.append(run[i:i + 2])
    return tokens


def _corpus(snapshot):
    """Yield ``(card, {token: tf})`` for every card in the snapshot."""
    for card in snapshot.cards.values():
        text = (card.title + "\n" + card.body)
        counts: dict[str, int] = {}
        for tok in _tokenize(text):
            counts[tok] = counts.get(tok, 0) + 1
        yield card, counts


def _snippet(card, body_lower, qtokens) -> str:
    """A deterministic, bounded snippet with at least one query-token hit.

    Prefers a window around the earliest body match; falls back to the body
    head when the hit is only in the title. Result is guaranteed <=300 chars.
    """
    body = card.body
    if not body:
        return ""
    best = None
    for tok in qtokens:
        pos = body_lower.find(tok)
        if pos != -1 and (best is None or pos < best[0]):
            best = (pos, tok)
    if best is None:
        return "" if not body else body[:_SNIPPET_MAX]
    pos = best[0]
    start = max(0, pos - 40)
    piece = body[start:start + _SNIPPET_MAX]
    if start > 0:
        piece = "…" + piece[1:]
    return piece[:_SNIPPET_MAX]


def search(snapshot: Snapshot, query: str, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Return the top ``limit`` cards by BM25 over title+body.

    Each result is ``{id,title,domain,path,score,snippet}`` with a snippet no
    longer than 300 characters taken from the card body. Cards with no lexical
    overlap are excluded; ordering is deterministic (score desc, then id).
    """
    if not isinstance(snapshot, Snapshot):
        raise SearchError("search needs a vault Snapshot")
    if not isinstance(query, str):
        raise SearchError("query must be a string")
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise SearchError("limit must be an integer")
    if limit < 1:
        raise SearchError("limit must be positive")

    qtokens = _tokenize(query)
    if not qtokens:
        return []
    qf: dict[str, int] = {}
    for tok in qtokens:
        qf[tok] = qf.get(tok, 0) + 1

    docs = list(_corpus(snapshot))
    n = len(docs)

    df: dict[str, int] = {}
    total_len = 0
    for _card, counts in docs:
        for tok in counts:
            df[tok] = df.get(tok, 0) + 1
        total_len += sum(counts.values())
    avgdl = (total_len / n) if n else 1.0

    idf: dict[str, float] = {}
    for tok in qf:
        f = df.get(tok, 0)
        if f == 0:
            continue
        idf[tok] = math.log(1.0 + (n - f + 0.5) / (f + 0.5))

    ranked = []
    for card, counts in docs:
        dl = sum(counts.values())
        denom = (1.0 - _B + _B * (dl / avgdl)) if dl else 1.0
        score = 0.0
        for tok, f in qf.items():
            tf = counts.get(tok, 0)
            if tf == 0:
                continue
            score += (idf.get(tok) or 0.0) * f * (
                (tf * (_K1 + 1)) / (tf + _K1 * denom)
            )
        if score > 0.0:
            ranked.append((card, score))

    ranked.sort(key=lambda rc: (-rc[1], rc[0].id))
    out = []
    for card, score in ranked[:limit]:
        snippet = _snippet(card, card.body.lower() if card.body else "", list(qf))
        out.append({
            "id": card.id,
            "title": card.title,
            "domain": card.domain,
            "path": card.path,
            "score": round(score, 6),
            "snippet": snippet,
        })
    return out


def neighbors(snapshot: Snapshot, ids) -> list[dict]:
    """Full ``card_packet`` (full body + all relation edges) for ``ids``.

    ``ids`` is a list/tuple/set of existing card ids; order is preserved and
    duplicates collapsed. Missing ids raise ``SearchError`` so the caller never
    silently drops a requested card.
    """
    if isinstance(ids, str):
        raise SearchError("neighbors needs a list of ids, not one string")
    missing = [i for i in ids if i not in snapshot.cards]
    if missing:
        raise SearchError("unknown card ids: %s" % sorted(missing))
    seen = set()
    out = []
    for cid in ids:
        if cid in seen:
            continue
        seen.add(cid)
        out.append(card_packet(snapshot.cards[cid]))
    return out

"""S3 routing: the candidate domains and neighbour cards a chunk is shown with.

A chunk is split into passages (paragraphs, each heading joined to the text
under it). Every passage is searched on its own and votes for the domains of
its top cards, weighted by rank; short passages vote less. One long query for
the whole chunk would keep only its rarest words, so a single paragraph full
of unusual terms could decide the domain of a chunk about something else.

The summed votes are divided by the square root of each domain's share of
all cards (plus a small floor), so a domain is chosen for matching more than
its size predicts: a very large domain otherwise wins on generic words alone.
On the author's vault (one domain holds 21% of the cards) this moved the
expected domains of all pending sources into the top three.
"""
from __future__ import annotations

import math
import re

from ..search import _tokenize
from .chunking import _blocks

HITS_PER_PASSAGE = 10
#: Neighbour cards shown to the producer: the best ones for the whole chunk, then the
#: best few of every passage, so each part of a long chunk gets its own nearby cards.
NEIGHBORS_PER_PASSAGE = 3
MAX_NEIGHBORS = 40
#: Exponent and floor of the domain-size correction (votes / (share + floor) ** exponent).
SIZE_EXPONENT = 0.5
SIZE_FLOOR = 0.01
#: Passages with fewer query tokens than this vote with a proportionally smaller weight.
FULL_WEIGHT_TOKENS = 40


def passages(text: str) -> list[str]:
    """Paragraph-sized query texts; a heading line is joined to the paragraph after it."""
    out, heading = [], ''
    for start, end, is_heading in _blocks(text):
        part = text[start:end].strip()
        if not part:
            continue
        if is_heading and '\n' not in part:
            heading = (heading + ' ' + part.lstrip('#').strip()).strip()
            continue
        if is_heading:
            first, _, rest = part.partition('\n')
            heading, part = (heading + ' ' + first.lstrip('#').strip()).strip(), rest.strip()
        out.append((heading + '\n' + part).strip() if heading else part)
        heading = ''
    if heading:
        out.append(heading)
    return out


def route(root, snapshot, text: str, *, domains: int, neighbors: int = 8) -> dict:
    """``{"domains", "filled", "neighbors", "votes": {domain: share}, "passages"}``.

    ``domains`` always has ``min(domains, catalog size)`` entries: voted domains
    first, then ``filled`` ones without votes. A catalog no larger than the
    limit is therefore sent whole, which a vault without cards depends on.
    """
    from ..index import search_many
    known = set((snapshot.catalog or {}).get('domains', {}))
    parts = passages(text)
    weights = [min(1.0, len(_tokenize(p)) / FULL_WEIGHT_TOKENS) for p in parts]
    results = search_many(root, snapshot, parts, HITS_PER_PASSAGE) if parts else []
    votes: dict = {}
    card_score: dict = {}
    for weight, hits in zip(weights, results):
        ranked = [h for h in hits if h['domain'] in known]
        total = sum(1 / (r + 1) for r in range(len(ranked)))
        for rank, hit in enumerate(ranked):
            share = weight * (1 / (rank + 1)) / total
            votes[hit['domain']] = votes.get(hit['domain'], 0.0) + share
            card_score[hit['id']] = card_score.get(hit['id'], 0.0) + share
    sizes: dict = {}
    for card in snapshot.cards.values():
        sizes[card.domain] = sizes.get(card.domain, 0) + 1
    count = sum(sizes.values()) or 1
    votes = {d: v / ((sizes.get(d, 0) / count + SIZE_FLOOR) ** SIZE_EXPONENT) for d, v in votes.items()}
    order = sorted(votes, key=lambda d: (-votes[d], d))
    total = sum(votes.values()) or 1.0
    best = sorted(card_score, key=lambda c: (-card_score[c], c))[:neighbors]
    for hits in results:
        best += [h['id'] for h in hits[:NEIGHBORS_PER_PASSAGE]
                 if h['domain'] in known and h.get('score', 0) > 0 and h['id'] not in best]
    best = best[:MAX_NEIGHBORS]
    chosen = order[:domains]
    # Domains without votes (no cards yet, or no matching cards) still need a chance:
    # fill up to the limit, first those whose name or question shares words with the
    # text, then empty domains, then in catalog order.
    catalog_order = list((snapshot.catalog or {}).get('domains', {}))
    unvoted = [d for d in catalog_order if d not in chosen]
    match = name_match(snapshot, text)
    unvoted.sort(key=lambda d: (-match.get(d, 0.0), sizes.get(d, 0) > 0, catalog_order.index(d)))
    filled = unvoted[:max(0, domains - len(chosen))]
    return {'domains': chosen + filled, 'filled': filled,
            'neighbors': [{'id': cid, 'title': snapshot.cards[cid].title, 'domain': snapshot.cards[cid].domain,
                           'essence': essence(snapshot.cards[cid])} for cid in best],
            'votes': {d: round(votes[d] / total, 3) for d in order[:6]},
            'passages': len(parts)}


def name_match(snapshot, text: str) -> dict:
    """``{domain: score}`` for domains whose name or question shares words with ``text``.

    A domain's words are its slug, MOC title and question: Latin words and
    CJK bigrams (single CJK characters match almost anything). Each shared
    word counts log(D / df), where df is how many of the catalog's D domains
    use it, so words every domain uses count nothing; the sum is divided by
    the square root of the domain's word count, so a long question does not
    win by length alone.
    """
    words = {}
    for slug, entry in ((snapshot.catalog or {}).get('domains') or {}).items():
        entry = entry if isinstance(entry, dict) else {}
        own = ' '.join([slug.replace('-', ' ').replace('_', ' '), str(entry.get('moc') or ''),
                        str(entry.get('question') or '')])
        words[slug] = {t for t in _tokenize(own) if t.isascii() or len(t) > 1}
    df: dict = {}
    for tokens in words.values():
        for t in tokens:
            df[t] = df.get(t, 0) + 1
    present = set(_tokenize(text))
    total = len(words)
    scores = {d: sum(math.log(total / df[t]) for t in tokens & present) / math.sqrt(len(tokens))
              for d, tokens in words.items() if tokens}
    return {d: v for d, v in scores.items() if v > 0}


def essence(card) -> str:
    match = re.search(r'^## [^\n]*\n+([^\n#][^\n]*)', card.body, re.M)
    text = match.group(1) if match else card.body.strip().split('\n', 1)[0]
    return text.strip()[:200]

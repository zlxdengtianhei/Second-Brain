"""S3 routing: the candidate domains and candidate cards a chunk is shown with.

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

from ..search import _tokenize
from .chunking import _blocks

HITS_PER_PASSAGE = 10
#: Candidate cards shown to the producer with ``candidates_k = "auto"``: the best ones for the whole
#: chunk, then the best few of every passage, so each part of a long chunk gets its own nearby cards.
CHUNK_CANDIDATES = 8
CANDIDATES_PER_PASSAGE = 3
MAX_CANDIDATES = 40
#: Exponent and floor of the domain-size correction (votes / (share + floor) ** exponent).
SIZE_EXPONENT = 0.5
SIZE_FLOOR = 0.01
#: Passages with fewer query tokens than this vote with a proportionally smaller weight.
FULL_WEIGHT_TOKENS = 40
#: A passage longer than this, of at least LINE_SPLIT_LINES lines, is a list with no blank line between its
#: items (a summary chapter, a table) and is searched line by line: one query holding seven or more items
#: finds the cards of only some of them. In the plugin's sources no prose paragraph was longer than 350
#: characters; the summary chapter's item blocks were 294-722 characters of 5-12 lines, and searched as
#: blocks found 52% of the cards the producer named for them in the first 5 (90% in the first 15), line by
#: line all of them. The four blocks of 7 lines or more hold 39 of its 50 such items; 5 or 6 items fit in
#: the first 15 of one query. On the plugin's ten sources the queries go from 222 to 490.
LINE_SPLIT_CHARS = 400
LINE_SPLIT_LINES = 7


def passages(text: str) -> list[str]:
    """Paragraph-sized query texts; a heading line is joined to the paragraph after it, and a long block of
    lines without a blank line between them (:data:`LINE_SPLIT_CHARS`) gives one query per line."""
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
        lines = [line.strip() for line in part.split('\n') if line.strip()]
        if len(part) > LINE_SPLIT_CHARS and len(lines) >= LINE_SPLIT_LINES and not part.startswith(('```', '~~~')):
            out += [(heading + '\n' + lines[0]).strip() if heading else lines[0], *lines[1:]]
        else:
            out.append((heading + '\n' + part).strip() if heading else part)
        heading = ''
    if heading:
        out.append(heading)
    return out


def route(root, snapshot, text: str, *, domains: int, k='auto') -> dict:
    """``{"domains", "filled", "candidates", "votes": {domain: share}, "passages"}``.

    ``domains`` always has ``min(domains, catalog size)`` entries: voted domains
    first, then ``filled`` ones without votes. A catalog no larger than the
    limit is therefore sent whole, which a vault without cards depends on.

    ``candidates`` maps a query key to ``[{id, rank, via: ["words"]}]``: ``p<n>``
    is passage ``n`` (1-based) with its best ``k`` cards. With ``k = "auto"``
    (the default) the key ``chunk`` holds the :data:`CHUNK_CANDIDATES` best
    cards of the whole chunk by vote, each passage adds its best
    :data:`CANDIDATES_PER_PASSAGE`, and at most :data:`MAX_CANDIDATES`
    distinct cards are shown in all.
    """
    from ..index import search_many
    known = set((snapshot.catalog or {}).get('domains', {}))
    parts = passages(text)
    weights = [min(1.0, len(_tokenize(p)) / FULL_WEIGHT_TOKENS) for p in parts]
    depth = HITS_PER_PASSAGE if k == 'auto' else max(HITS_PER_PASSAGE, int(k))
    results = search_many(root, snapshot, parts, depth) if parts else []
    votes: dict = {}
    card_score: dict = {}
    for weight, hits in zip(weights, results):
        ranked = [h for h in hits[:HITS_PER_PASSAGE] if h['domain'] in known]
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
    candidates = _candidates(card_score, results, known, k)
    chosen = order[:domains]
    # Domains without votes (no cards yet, or no matching cards) still need a chance:
    # fill up to the limit, first those whose name or question shares words with the
    # text, then empty domains, then in catalog order.
    catalog_order = list((snapshot.catalog or {}).get('domains', {}))
    unvoted = [d for d in catalog_order if d not in chosen]
    match = name_match(snapshot, text)
    unvoted.sort(key=lambda d: (-match.get(d, 0.0), sizes.get(d, 0) > 0, catalog_order.index(d)))
    filled = unvoted[:max(0, domains - len(chosen))]
    return {'domains': chosen + filled, 'filled': filled, 'candidates': candidates,
            'votes': {d: round(votes[d] / total, 3) for d in order[:6]},
            'passages': len(parts)}


def _candidates(card_score: dict, results: list, known: set, k) -> dict:
    """The producer's first-route candidates per query key (see :func:`route`)."""
    def entries(ids):
        return [{'id': cid, 'rank': rank, 'via': ['words']} for rank, cid in enumerate(ids, 1)]
    out, shown = {}, []
    per_passage = CANDIDATES_PER_PASSAGE if k == 'auto' else int(k)
    if k == 'auto':
        best = sorted(card_score, key=lambda c: (-card_score[c], c))[:CHUNK_CANDIDATES]
        if best:
            out['chunk'] = entries(best)
            shown += best
    for n, hits in enumerate(results, 1):
        ids = [h['id'] for h in hits[:per_passage] if h['domain'] in known and h.get('score', 0) > 0]
        if k == 'auto':
            room = MAX_CANDIDATES - len(shown)
            fresh = [i for i in ids if i not in shown][:max(0, room)]
            shown += fresh
            ids = [i for i in ids if i in shown]
        if ids:
            out[f'p{n}'] = entries(ids)
    return out


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



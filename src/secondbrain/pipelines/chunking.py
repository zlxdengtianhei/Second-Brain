"""Split a source text into contiguous chunks (S2), deterministically.

Chunks are exact slices: ``text[start:end] == chunk text`` and the chunks
cover the text end to end, so verbatim quotes can be checked per chunk and a
chunk can always be traced back to its source offsets. Boundaries prefer
Markdown headings, then blank lines; a single paragraph longer than the limit
is split at sentence ends, and only text without any sentence end is cut at
the limit.
"""
from __future__ import annotations

import re

_HEADING = re.compile(r'(?m)^#{1,6}\s')
_SENTENCE_END = re.compile(r'(?<=[。！？!?；;])|(?<=[.!?])(?=\s)')


def _blocks(text: str) -> list[tuple[int, int, bool]]:
    """``(start, end, starts_with_heading)`` blocks separated by blank lines."""
    out = []
    pos = 0
    n = len(text)
    for match in re.finditer(r'\n[ \t]*\n+', text):
        end = match.end()
        out.append((pos, end))
        pos = end
    if pos < n:
        out.append((pos, n))
    blocks = []
    for start, end in out:
        # a heading inside a block starts a new block
        cuts = [m.start() for m in _HEADING.finditer(text, start, end) if m.start() > start]
        edges = [start, *cuts, end]
        for a, b in zip(edges, edges[1:]):
            if b > a:
                blocks.append((a, b, bool(_HEADING.match(text, a))))
    return blocks


def _split_long(text: str, start: int, end: int, limit: int) -> list[tuple[int, int, str]]:
    pieces = []
    cur = start
    while end - cur > limit:
        window_end = cur + limit
        cuts = [m.end() + cur for m in _SENTENCE_END.finditer(text[cur:window_end]) if m.end() > 0]
        cut = max((c for c in cuts if c > cur), default=None)
        reason = 'sentence'
        if cut is None or cut <= cur:
            cut, reason = window_end, 'hard_limit'
        pieces.append((cur, cut, reason))
        cur = cut
    pieces.append((cur, end, 'sentence' if pieces else 'paragraph'))
    return pieces


def chunk_text(text: str, max_chars: int) -> list[dict]:
    """Return ``[{chunk_id, start, end, split_reason}]`` covering ``text``."""
    if max_chars < 1:
        raise ValueError('max_chars must be positive')
    if len(text) <= max_chars:
        return [{'chunk_id': 'c01', 'start': 0, 'end': len(text), 'split_reason': 'whole'}]
    atoms: list[tuple[int, int, bool, str]] = []
    for start, end, heading in _blocks(text):
        if end - start > max_chars:
            for i, (a, b, reason) in enumerate(_split_long(text, start, end, max_chars)):
                atoms.append((a, b, heading and i == 0, reason))
        else:
            atoms.append((start, end, heading, 'paragraph'))
    spans: list[list] = []
    for start, end, heading, reason in atoms:
        blank = not text[start:end].strip()
        if spans and (blank and end - spans[-1][0] <= max_chars or
                      end - spans[-1][0] <= max_chars and not (heading and spans[-1][1] - spans[-1][0] >= max_chars // 2)):
            spans[-1][1] = end
            continue
        spans.append([start, end, 'heading' if heading else reason])
    width = max(2, len(str(len(spans))))
    return [{'chunk_id': f'c{i + 1:0{width}d}', 'start': a, 'end': b, 'split_reason': r}
            for i, (a, b, r) in enumerate(spans)]

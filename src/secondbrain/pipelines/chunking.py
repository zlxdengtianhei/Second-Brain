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


# --------------------------------------------------------------------------
# The context note: where a chunk sits in its source (design R-03)
# --------------------------------------------------------------------------
_ATX = re.compile(r'^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$')
_FENCE = re.compile(r'^[ \t]*(```|~~~)')
#: What a model is told about the note, every time it gets one.
NOTE_USE = ('Only for understanding this chunk: where it sits in the source and what came before. It is not part '
            'of the chunk: do not quote it, do not write cards from it, and it covers no passage.')
#: Caps of the note's parts, in the order they are packed (the outline gets the rest, at most OUTLINE_CHARS).
PATH_CHARS, TAIL_CHARS, SUMMARY_CHARS, CONCEPTS_CHARS, OUTLINE_CHARS = 200, 600, 300, 600, 1200
MARKER = '  ← this chunk'


def outline(text: str, levels: int = 3) -> list[dict]:
    """``[{level, title, start}]``: the Markdown headings of ``text`` up to ``levels`` deep, outside code fences."""
    out, pos, fence = [], 0, None
    for line in text.splitlines(keepends=True):
        bare = line.rstrip('\r\n')
        opened = _FENCE.match(bare)
        if opened:
            fence = opened.group(1) if fence is None else (None if bare.lstrip().startswith(fence) else fence)
        elif fence is None:
            heading = _ATX.match(bare)
            if heading and len(heading.group(1)) <= levels and heading.group(2).strip():
                out.append({'level': len(heading.group(1)), 'title': heading.group(2).strip(), 'start': pos})
        pos += len(line)
    return out


def _open_at(headings: list[dict], offset: int) -> list[dict]:
    """The headings in force at ``offset``: the last one of each level before it, outer first."""
    stack: list[dict] = []
    for h in headings:
        if h['start'] > offset:
            break
        while stack and stack[-1]['level'] >= h['level']:
            stack.pop()
        stack.append(h)
    return stack


def heading_path(text: str, offset: int, limit: int = PATH_CHARS) -> str:
    """The headings in force where a chunk starts, outer first (``A › B › C``), at most ``limit`` characters;
    outer ones are left out first."""
    titles = [h['title'] for h in _open_at(outline(text, 6), offset)]
    path = ' › '.join(titles)
    while len(path) > limit and len(titles) > 1:
        titles = titles[1:]
        path = '… › ' + ' › '.join(titles)
    return path if len(path) <= limit else path[:max(0, limit - 1)] + '…'


def previous_tail(text: str, start: int, end: int, limit: int = TAIL_CHARS) -> str:
    """The last one or two paragraphs of ``text[start:end]`` (the chunk before), whole paragraphs, at most ``limit``
    characters; a last paragraph longer than that is cut at a sentence start."""
    paragraphs = [p.strip() for p in re.split(r'\n[ \t]*\n', text[start:end]) if p.strip()]
    kept: list[str] = []
    for p in reversed(paragraphs[-2:]):
        if len('\n\n'.join([p, *kept])) > limit:
            break
        kept.insert(0, p)
    if kept or not paragraphs or limit <= 0:
        return '\n\n'.join(kept)
    last = paragraphs[-1][-limit:]
    cut = next((m.end() for m in _SENTENCE_END.finditer(last) if 0 < m.end() < len(last)), 0)
    return last[cut:].lstrip()


def outline_text(text: str, offset: int | None, limit: int = OUTLINE_CHARS) -> str:
    """The source's headings three levels deep as an indented list, ``offset``'s section marked; past ``limit``
    characters only the lines around the marked one are kept (``…`` where lines were left out)."""
    headings = outline(text, 3)
    lines = ['  ' * (h['level'] - 1) + '- ' + h['title'] for h in headings]
    here = None
    if offset is not None:
        inside = [i for i, h in enumerate(headings) if h['start'] <= offset]
        here = inside[-1] if inside else None
        if here is not None:
            lines[here] += MARKER
    if len('\n'.join(lines)) <= limit:
        return '\n'.join(lines)
    centre = here if here is not None else 0
    lo, hi = centre, centre + 1

    def size(a, b):
        return len('\n'.join((['…'] if a > 0 else []) + lines[a:b] + (['…'] if b < len(lines) else [])))
    if size(lo, hi) > limit:
        return lines[centre][:max(0, limit - 1)] + '…'
    grew = True
    while grew:
        grew = False
        for a, b in ((lo, hi + 1), (lo - 1, hi)):
            if 0 <= a and b <= len(lines) and (a, b) != (lo, hi) and size(a, b) <= limit:
                lo, hi, grew = a, b, True
    return '\n'.join((['…'] if lo > 0 else []) + lines[lo:hi] + (['…'] if hi < len(lines) else []))


def _cut_lines(lines: list[str], limit: int) -> str:
    """As many whole ``lines`` as fit in ``limit`` characters."""
    out: list[str] = []
    for line in lines:
        if len('\n'.join([*out, line])) > limit:
            break
        out.append(line)
    return '\n'.join(out)


def context_note(text: str, chunks: list[dict], chunk_id: str, chars: int, *, summary: str | None = None,
                 concepts: list[dict] | None = None) -> dict | None:
    """The program's note for chunk ``chunk_id`` of ``text`` (``chunks``: ``{chunk_id, start, end}`` in source
    order), or None for a source of one chunk.

    Packed in this order, all within ``chars`` characters: the heading path
    (≤200), the end of the chunk before (≤600), the model's summary of what
    came before (≤300, given only in that arm) or the source's concept list
    (≤600, likewise), and the source's outline (the rest, ≤1,200).
    """
    if len(chunks) < 2:
        return None
    index = next(i for i, c in enumerate(chunks) if c['chunk_id'] == chunk_id)
    chunk = chunks[index]
    left = max(0, int(chars))
    note: dict = {'use': NOTE_USE}
    note['heading_path'] = heading_path(text, chunk['start'], min(PATH_CHARS, left))
    left -= len(note['heading_path'])
    before = chunks[index - 1] if index else None
    note['previous_tail'] = previous_tail(text, before['start'], before['end'], min(TAIL_CHARS, left)) if before else ''
    left -= len(note['previous_tail'])
    if summary is not None:
        note['summary'] = summary.strip()[:min(SUMMARY_CHARS, left)]
        left -= len(note['summary'])
    if concepts is not None:
        rows = [f'- {c["name"]} ({", ".join(c["chunks"])})' for c in concepts]
        note['concepts'] = _cut_lines(rows, min(CONCEPTS_CHARS, left))
        left -= len(note['concepts'])
    note['outline'] = outline_text(text, chunk['start'], min(OUTLINE_CHARS, left)) if left > 0 else ''
    return note


def note_chars(note: dict | None) -> int:
    """The characters of a note that count toward ``context_note_chars`` (all but its fixed ``use`` line)."""
    return sum(len(v) for k, v in (note or {}).items() if k != 'use' and isinstance(v, str))

"""Verbatim-evidence check shared by every pipeline (one implementation)."""
from __future__ import annotations

import difflib


def check_verbatim(text: str, quote: object) -> str | None:
    """Return ``None`` when ``quote`` is a non-empty verbatim slice of ``text``.

    Otherwise return a short hint that points at the closest match, so a
    producer can copy the exact characters on the next attempt.
    """
    if not isinstance(quote, str) or not quote.strip():
        return 'quote is empty or not a string'
    if not isinstance(text, str):
        return 'source text is missing'
    if quote in text:
        return None
    match = difflib.SequenceMatcher(None, quote, text, autojunk=False).find_longest_match(
        0, len(quote), 0, len(text))
    start = max(0, match.b - 20)
    context = text[start:match.b + match.size + 20].replace('\n', ' ')
    return (f'not a verbatim slice (closest match {match.size}/{len(quote)} chars; '
            f'source near there: ...{context}...; copy the exact characters)')

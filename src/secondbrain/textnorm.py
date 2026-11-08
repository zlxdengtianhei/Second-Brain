"""When two pieces of text say the same words."""
from __future__ import annotations

import unicodedata


def plain(text) -> str:
    """``text`` in one form (NFKC), case folded, without spaces (Unicode Z*, and tabs and line breaks) and
    punctuation (Unicode P*). Letters, marks, digits and symbols (``<``, ``^``, ``+``, ``≤``, ``$``, ``→``) stay:
    two texts that differ in a symbol do not say the same thing."""
    folded = unicodedata.normalize('NFKC', str(text or '')).casefold()
    return ''.join(ch for ch in folded if not ch.isspace() and unicodedata.category(ch)[0] not in 'PZ')


def same_text(a, b) -> bool:
    """True when ``a`` and ``b`` are the same words, whatever their spaces, punctuation, case or character forms."""
    return plain(a) == plain(b)

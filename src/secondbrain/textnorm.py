"""When two pieces of text say the same words."""
from __future__ import annotations

import unicodedata


def plain(text) -> str:
    """``text`` in one form (NFKC), case folded, without spaces and punctuation."""
    folded = unicodedata.normalize('NFKC', str(text or '')).casefold()
    return ''.join(ch for ch in folded if ch.isalnum())


def same_text(a, b) -> bool:
    """True when ``a`` and ``b`` are the same words, whatever their spaces, punctuation, case or character forms."""
    return plain(a) == plain(b)

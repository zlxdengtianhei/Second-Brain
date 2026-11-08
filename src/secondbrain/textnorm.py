"""When two pieces of text say the same words."""
from __future__ import annotations

import re
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


#: For :func:`near_same`: the share of the shorter text's words (CJK: character pairs) the other must hold, and the
#: share of all their words both hold. Measured on the plugin vault's 197 cards (a card's own items are meant to be
#: distinct points): 2 of 2,309 pairs of propositions and 4 of 537 pairs of questions inside one card match.
NEAR = 0.7
NEAR_BOTH = 0.5
_CJK_RUN = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+')
_WORD = re.compile(r'[^\W_]+')


def _stem(word: str) -> str:
    for end in ('ing', 'ed', 'es', 's', 'al'):
        if word.endswith(end) and len(word) - len(end) >= 3:
            return word[:-len(end)]
    return word


def near_words(text) -> set:
    """What :func:`near_same` compares: character pairs of the CJK runs and stemmed Latin words, after NFKC and case
    folding, without the pointers, question words and function words of :data:`secondbrain.labels.NEAR_IGNORED`."""
    from .labels import NEAR_IGNORED
    folded = unicodedata.normalize('NFKC', str(text or '')).casefold()
    out = set()
    for run in _CJK_RUN.findall(folded):
        for word in NEAR_IGNORED['zh']:
            run = run.replace(word, ' ')
        for part in run.split():
            out |= {part[i:i + 2] for i in range(len(part) - 1)} or {part}
    for word in _WORD.findall(_CJK_RUN.sub(' ', folded)):
        if word not in NEAR_IGNORED['en']:
            out.add(_stem(word))
    return out


def near_same(a, b) -> bool:
    """True when ``a`` and ``b`` likely say or ask the same thing in other words: at least :data:`NEAR` of the
    shorter one's words (:func:`near_words`, at least three) are in the other, and :data:`NEAR_BOTH` of all their
    words are in both.

    Conservative and only a reason to ask again: a near match is never dropped or merged on its own.
    """
    x, y = near_words(a), near_words(b)
    small = min(len(x), len(y))
    both = len(x & y)
    return small >= 3 and both / small >= NEAR and both / len(x | y) >= NEAR_BOTH

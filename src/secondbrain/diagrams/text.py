"""Lexical helpers shared by the validator and the audit.

Grounding is measured on surface tokens: CJK character bigrams plus Latin
words and digit runs. It is a screening signal, not a semantic judgement:
paraphrases score low even when they are faithful.
"""
from __future__ import annotations

import re

_CJK_RUN = re.compile(r"[一-鿿]+")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]+|\d+")
_MARKUP = re.compile(r"<br\s*/?>|`|\*\*|\$", re.I)
_NON_WORD = re.compile(r"[\s\W_]+")


def strip_markup(s: str) -> str:
    """Drop line breaks, code ticks, bold markers and math dollars."""
    return _MARKUP.sub(" ", s or "")


def tokens(s: str) -> set[str]:
    """Lower-cased surface tokens: Latin words (2+ chars), digit runs, CJK bigrams.

    A CJK run of one character contributes that character.
    """
    s = strip_markup(s).lower()
    out = set(_LATIN.findall(s))
    for run in _CJK_RUN.findall(s):
        if len(run) == 1:
            out.add(run)
        out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


_WORD = re.compile(r"[a-z][a-z0-9]*|\d+")


def grounding_tokens(s: str) -> set[str]:
    """Tokens for the validator's grounding check (V4).

    Like tokens(), but Latin compounds are split at '-', '_', '.' and other
    punctuation, and single letters count, so a label copied from a
    proposition matches it part by part: 'Request' is grounded in
    'Discover-Offer-Request-ACK', and 'S_i' in 'S_i'. tokens() keeps the
    original rule because the audit reproduces published numbers with it.
    """
    s = strip_markup(s).lower()
    out = set(_WORD.findall(s))
    for run in _CJK_RUN.findall(s):
        if len(run) == 1:
            out.add(run)
        out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def overlap(label: str, source: str) -> float | None:
    """Share of the label's grounding tokens found in source; None if the label has none."""
    t = grounding_tokens(label)
    if not t:
        return None
    return len(t & grounding_tokens(source)) / len(t)


def display_width(s: str) -> float:
    """Width in full-width character units: CJK and other wide scripts count 1, the rest 0.5."""
    return sum(1 if ord(ch) > 0x2E80 else 0.5 for ch in s)


def norm_title(s: str) -> str:
    """Comparison key for titles: lower case, no markup, no spaces or punctuation."""
    return _NON_WORD.sub("", strip_markup(s).lower())


def title_match(label: str, titles: list[str]) -> bool:
    """True if label names one of the titles (equal, or containment for keys of 4+ chars)."""
    nl = norm_title(label)
    if len(nl) < 3:
        return False
    for t in titles:
        nt = norm_title(t)
        if len(nt) < 3:
            continue
        if nl == nt or (len(nl) >= 4 and (nl in nt or nt in nl)):
            return True
    return False

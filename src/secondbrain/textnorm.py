"""When two pieces of text say the same words."""
from __future__ import annotations

import re
import unicodedata


#: Decision D-15: besides spaces, the only characters same_text leaves out (a closed table, after NFKC): sentence
#: marks (, . ; : ? ! and the CJK full stop and enumeration comma; NFKC turns the full-width forms into the ASCII
#: ones), quotes (straight, curly, CJK corner brackets), the ellipsis (NFKC makes it three dots) and the em dash.
#: Everything else stays: brackets, # % & * @ \ _ ^ ~ + = < > | / -, digits and letters. Saying "the same" drops
#: knowledge (the costliest error); saying "different" only keeps a repeat.
DROP = frozenset(',.;:?!"\'' + ''.join(map(chr, (0x3002, 0x3001, 0x201C, 0x201D, 0x2018, 0x2019, 0x300C, 0x300D,
                                                  0x300E, 0x300F, 0x2026, 0x2014))))
#: Marks of the table kept between two digits, where they belong to a number (3.5, 1,000, 3:2).
IN_NUMBERS = frozenset('.,:')
#: Marks of the table kept right after a digit or a one-letter Latin name (D-15 4): a factorial (n!, 5!) or a prime
#: (f', A'). After a longer word they end a sentence or stand in a word (Done!, don't) and are left out.
AFTER_NAMES = frozenset("!'")
#: A formula ($$...$$, $...$) or inline code (`...`): compared as written, only its spaces made one.
_VERBATIM = re.compile(r'\$\$.+?\$\$|\$[^$]+\$|`[^`]+`', re.S)


def _latin(ch: str) -> bool:
    return ch.isascii() and ch.isalpha()


def _part_of_a_name(folded: str, i: int) -> bool:
    """Whether the mark at ``i`` follows a digit, or a one-letter Latin name (not the end of a longer word)."""
    if i == 0:
        return False
    before = folded[i - 1]
    if before.isdigit():
        return True
    return _latin(before) and not (i > 1 and (_latin(folded[i - 2]) or folded[i - 2].isdigit()))


def _prose(text: str) -> str:
    folded = unicodedata.normalize('NFKC', text).casefold()
    out = []
    for i, ch in enumerate(folded):
        if ch.isspace():
            continue
        if ch in DROP and not (ch in IN_NUMBERS and 0 < i < len(folded) - 1
                               and folded[i - 1].isdigit() and folded[i + 1].isdigit()) \
                and not (ch in AFTER_NAMES and _part_of_a_name(folded, i)):
            continue
        out.append(ch)
    return ''.join(out)


def plain(text) -> str:
    """``text`` in one form for comparing: outside formulas and code, NFKC, case folded, without spaces and the
    characters of :data:`DROP`; a formula or inline code as written, with its spaces made one."""
    raw, out, last = str(text or ''), [], 0
    for m in _VERBATIM.finditer(raw):
        out += [_prose(raw[last:m.start()]), ' '.join(m.group().split())]
        last = m.end()
    out.append(_prose(raw[last:]))
    return ''.join(out)


def same_text(a, b) -> bool:
    """True when ``a`` and ``b`` are the same words, whatever their spaces, sentence marks, quotes, case or
    character forms (:func:`plain`)."""
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

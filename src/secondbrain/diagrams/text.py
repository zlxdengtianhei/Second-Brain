"""Lexical helpers shared by the validator and the audit.

Grounding is measured on surface tokens: CJK character bigrams plus Latin
words and digit runs. It is a screening signal, not a semantic judgement:
paraphrases score low even when they are faithful.
"""
from __future__ import annotations

import re
import string

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
# Greek letters and the operators a plain-text formula label may contain.
_SYMBOLS = re.compile(r"[\u0391-\u03a9\u03b1-\u03c9+×·÷±=≤≥≠≈∞∈⊕⊗⊙∏√∂∇∫]")
_TEX_SYMBOLS = {
    **{n: chr(0x3b1 + i) for i, n in enumerate(
        "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho "
        "varsigma sigma tau upsilon phi chi psi omega".split())},
    "varepsilon": "ε", "vartheta": "θ", "varphi": "φ", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ",
    "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "sum": "Σ", "prod": "Π", "sqrt": "√", "partial": "∂", "nabla": "∇", "int": "∫", "infty": "∞",
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
    "neq": "≠", "ne": "≠", "approx": "≈", "in": "∈", "oplus": "⊕", "otimes": "⊗", "odot": "⊙",
}
# Commands that only format; their arguments stay.
_TEX_FORMAT = set("frac dfrac tfrac left right mathrm mathbf mathit mathcal mathbb boldsymbol text textbf "
                  "operatorname hat bar tilde vec dot ddot overline underline widehat displaystyle quad qquad "
                  "big Big bigg Bigg limits".split())
_TEX_CMD = re.compile(r"\\([A-Za-z]+)")
# Case folding stays within ASCII: lower() would turn the sum sign Σ into sigma.
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
_MATH_SPAN = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$", re.S)
_NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
_CN_NUMBERS = {ch: i for i, ch in enumerate("零一二三四五六七八九十", 0)} | {"两": 2}
_COUNT_CN = re.compile(r"([两二三四五六七八九十]|\d{1,2})\s*(?:级|层|个|种|类|路|条|步|阶段|部分|段|轮|项|块|位|道|组|级别)")
_COUNT_EN = re.compile(r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})[- ]"
                       r"(?:level|layer|stage|step|part|path|kind|type|phase|round|tier|way|block|head|branch|"
                       r"item|field|component|unit|table)s?\b", re.I)


def plain_math(s: str) -> str:
    """TeX commands to the plain-text symbols or names a label would use: \\sigma -> σ, \\sum -> Σ, \\log -> log."""
    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name in _TEX_SYMBOLS:
            return f" {_TEX_SYMBOLS[name]} "
        return " " if name in _TEX_FORMAT else f" {name} "
    # the n-ary signs and their Greek look-alikes are one token each
    return _TEX_CMD.sub(sub, s).replace("∑", "Σ").replace("∏", "Π").replace("−", "-")


def _stem(w: str) -> str:
    """Light English plural and third-person stemming, so 'outputs' meets 'output'."""
    if len(w) < 4 or not w.isalpha():
        return w
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("sses", "ches", "shes", "xes", "zes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def grounding_tokens(s: str) -> set[str]:
    """Tokens for the validator's grounding check (V4).

    Like tokens(), but Latin compounds are split at '-', '_', '.' and other
    punctuation, single letters count, English words are lightly stemmed,
    TeX commands become the symbols a plain-text label uses, and Greek
    letters and operators are tokens. A label copied from a proposition so
    matches it part by part: 'Request' is grounded in 'Discover-Offer-
    Request-ACK', 'S_i' in 'S_i', 'σ(Wx + b)' in '$\\sigma(Wx+b)$'. tokens()
    keeps the original rule because the audit reproduces published numbers.
    """
    s = strip_markup(plain_math(s)).translate(_ASCII_LOWER)  # Σ must stay Σ, not become σ
    out = {_stem(w) for w in _WORD.findall(s)}
    out.update(_SYMBOLS.findall(s))
    for run in _CJK_RUN.findall(s):
        if len(run) == 1:
            out.add(run)
        out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def source_tokens(s: str) -> set[str]:
    """grounding_tokens() of a cited text, plus what a label may fairly derive from it.

    Every single CJK character is also a token, so a one-character label such
    as 或 or 积 can match the text it comes from (a longer label is still
    compared by character pairs). Inside $...$ formulas every letter is also
    a token ('Wx' is W times x). A stated count such as 'four levels' or
    '两路' adds the numbers 1..n, so 'level 3' or '第 1 路' can name one of
    the counted items.
    """
    out = grounding_tokens(s)
    out.update(ch for run in _CJK_RUN.findall(s) for ch in run)
    for m in _MATH_SPAN.finditer(s):
        # letters of the formula itself, not of command names such as \log
        body = _TEX_CMD.sub(lambda c: f" {_TEX_SYMBOLS.get(c.group(1), '')} ", m.group(1) or m.group(2))
        out.update(ch for ch in body.translate(_ASCII_LOWER) if "a" <= ch <= "z")
    counts = [_CN_NUMBERS.get(x, None) if not x.isdigit() else int(x) for x in _COUNT_CN.findall(s)]
    counts += [int(x) if x.isdigit() else _NUMBER_WORDS[x.lower()] for x in _COUNT_EN.findall(s)]
    for n in counts:
        if n and 2 <= n <= 12:
            out.update(str(i) for i in range(1, n + 1))
    return out


def overlap(label: str, source: str) -> float | None:
    """Share of the label's grounding tokens found in source.

    A label with no word tokens (symbols only, such as # or ⊕) counts its
    characters instead: 1.0 if every one occurs in the source, else 0.0.
    None if the label has no visible characters at all.
    """
    t = grounding_tokens(label)
    if not t:
        chars = {ch for ch in label if not ch.isspace()}
        if not chars:
            return None
        return 1.0 if chars <= set(plain_math(source)) else 0.0
    return len(t & source_tokens(source)) / len(t)


_TEX = re.compile(r"\$|\\[A-Za-z]+|\\[{}()\[\],;!]|[_^]\{")


def has_tex(s: str) -> bool:
    """True if a label carries TeX source ($, \\commands, _{...}, ^{...}) instead of plain text."""
    return bool(_TEX.search(s))


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

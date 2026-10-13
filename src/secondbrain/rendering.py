"""Formula normalization for card bodies (pure functions, no I/O).

Every card body is passed through :func:`normalize_markdown` before it is
written, so formula notation is fixed in one place instead of relying on each
writer. It repairs three problems and is idempotent, so re-rendering also
heals text that is already on disk.

The three problems (all seen in real cards):
1. Bare subscripts: the body has ``W_Q, W_K, W_V`` instead of ``$W_Q$``. Markdown
   reads two ``_`` on one line as italics, so the subscripts do not render and
   the text between them turns italic.
2. Multi-character subscripts without braces: even inside ``$…$``, MathJax
   takes only ``m`` of ``d_model`` as the subscript and renders d·ₘodel. It
   must be ``d_{model}``.
3. Bare macro names: extractors write pseudo-LaTeX such as ``prod_t``, ``log``,
   ``q_{theta}``, which render as upright letter strings even inside ``$…$``;
   they need a backslash (names MathJax does not define use
   ``\\operatorname{}``).

Why wrap segments, not single tokens: wrapping ``P(w_t|h)=C(h,w_t)/C(h)``
token by token gives ``P($w_t$|h)=C(h,$w_t$)/C(h)``, which is harder to read
than no wrapping at all. So a line is first cut at CJK characters and protected
spans, each segment is split at spaces into chunks, and only consecutive math
chunks are joined into one wrapped segment.

Conservative rules (better to miss a formula than to break text):
- Only the body changes, never the front matter; code fences, inline ``code``,
  ``[[wiki]]`` links, URLs, HTML comments and markdown links are skipped.
- A line with an odd number of ``$``, a ``$`` followed by a digit (money) or a
  ``$$`` is left alone.
- A chunk is math when it has ``_`` or ``^``, is a known macro name, or each of
  its letter runs is a single letter; a segment is wrapped only when at least
  one chunk has a real subscript and is not an identifier.
- Spaces and stray punctuation are trimmed from both ends of a segment, and
  unbalanced brackets are trimmed until balanced (no space just inside ``$…$``).
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Constants and patterns. Each list was settled against counterexamples over several
# review rounds; do not extend them casually.

GREEK = "Ͱ-Ͽ"
# Segment boundaries: CJK characters and full-width punctuation. (`::` is a hard cut at
# the start of normalize_math instead: `:` is legal in `w_{1:T}`, so a character-class
# boundary would split that subscript.)
CJK = "\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef" \
      "\u2018\u2019\u201c\u201d\u2014\u2013"
# MATH_CHARS starts with ^, so it is a negated class: anything but a boundary or space
MATH_CHARS = rf"^{CJK}\s"
MATH_CHUNK_RE = re.compile(rf"^[{MATH_CHARS}]+$")
LETTER_RUN_RE = re.compile(r"[A-Za-z]+")

# Subscript anchors: Latin and Greek letters, also operators such as ∑∏∫∮ and combining
# marks (`C̃` = C+U+0303); otherwise `∑_x P(x)=1` has no "subscript" and stays outside math.
ANCHOR = rf"A-Za-z{GREEK}\u2200-\u22ff\u0300-\u036f"
BASE = rf"[{ANCHOR}][{ANCHOR}0-9]*"
# Subscript forms: a brace group; a command such as `\theta` (what normalization writes,
# so a second pass must still see it, or `$q_\theta$` loses its $ and the two passes
# disagree); a parenthesis group (`∂h^(k)`, else it has no "superscript"); bare letters/digits.
SUB = rf"(?:\{{[^}}\n]{{1,24}}\}}|\\[A-Za-z]+|\([^)\n]{{1,16}}\)|[<>]?[{ANCHOR}0-9]+(?:[+\-][0-9]+)?)"
MATH_TOKEN = re.compile(rf"{BASE}[_^]{SUB}")

# Bare macro names that get a backslash: frequent and unambiguous ones only
# (`pi`, `in` and other likely variable names or words are left out).
MACROS = [
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
    "iota", "kappa", "lambda", "mu", "nu", "xi", "rho", "sigma", "tau",
    "phi", "chi", "psi", "omega",
    "Gamma", "Delta", "Theta", "Lambda", "Sigma", "Phi", "Psi", "Omega",
    "sum", "prod", "int", "oint", "log", "ln", "exp", "max", "min",
    "sin", "cos", "tan", "tanh", "sinh", "cosh", "sigmoid", "relu",
    "sqrt", "frac", "infty", "approx", "cdot", "times", "neq", "leq", "geq",
    "sim", "propto", "partial", "nabla", "mathbb", "text",
    # Symbol commands: as bare words they render as italic letters (`bullet` → b·u·l·l·e·t).
    # Unambiguous ones only: `to`, `mid`, `cap`, `cup` are also common English words.
    "bullet", "circ", "oplus", "otimes", "ldots", "cdots", "vdots",
    "emptyset", "forall", "exists", "equiv", "parallel", "perp",
    "langle", "rangle", "mapsto", "subseteq", "supseteq", "bigl", "bigr",
]
# softmax, argmax and argmin are not MathJax commands: \softmax renders as a red
# undefined control sequence, so they become \operatorname{}.
OPERATORS = ["softmax", "argmax", "argmin"]
MACRO_RE = re.compile(r"(?<![\\A-Za-z])(" + "|".join(MACROS) + r")(?![A-Za-z])")
OPERATOR_RE = re.compile(r"(?<![\\A-Za-z{])(" + "|".join(OPERATORS) + r")(?![A-Za-z}])")
MACRO_WORDS = set(MACROS) | set(OPERATORS)

# Identifier shapes (front matter fields, relation types, paths) are never math
IDENT_PREFIX = ("kc_", "sr_", "cl_", "cr_", "co_", "req_", "task_", "session_")
IDENT_WORDS = {
    "depends_on", "part_of", "contrasts_with", "similar_to", "generalizes",
    "follows", "from_claims", "merged_claims", "claim_id", "source_file",
    "notes_registry", "review_events", "kb_review", "generated_by",
    "domain_hint", "merge_log", "skipped_claims", "links_only", "flashcards",
    "sr_due", "sr_interval", "sr_ease", "self_attention", "cross_entropy",
    "log_softmax", "10_Knowledge", "20_MOCs", "30_Exercises", "50_Reviews",
}
SNAKE = re.compile(r"^[a-z]{2,}(_[a-z]{2,})+$")

# Protected spans: inline code, [[wiki]] links, URLs, HTML comments, markdown links
PROTECT = [
    re.compile(p) for p in (
        r"`[^`\n]*`", r"\[\[[^\]]*\]\]", r"https?://\S+", r"<!--.*?-->",
        r"\[[^\]]*\]\([^)]*\)",
    )
]
DOLLAR_INLINE = re.compile(r"\$([^$\n]+)\$")

ENUM_RE = re.compile(r"^[A-Za-z]?\d*\.$")
LATEX_CMD = re.compile(r"\\[a-zA-Z]{2,}")
# Inside the braces of `\text{}` and similar commands any character is allowed (CJK too);
# the whole group is part of the formula
TEXTLIKE_CMD = re.compile(r"\\(?:text|mathrm|mathbf|mathit|operatorname)\{[^{}]*\}")


def _protected(line: str) -> list[tuple[int, int]]:
    """Spans of protected text: inline code, wiki links, URLs, HTML comments, markdown links."""
    spans: list[tuple[int, int]] = []
    for pat in PROTECT:
        spans += [(m.start(), m.end()) for m in pat.finditer(line)]
    return spans


def _is_identifier(tok: str) -> bool:
    return tok in IDENT_WORDS or tok.startswith(IDENT_PREFIX) or bool(SNAKE.match(tok))


def _is_math_chunk(chunk: str) -> bool:
    """Whether one space-separated chunk belongs to a formula.

    It does when it has `_` or `^` (a subscript may have several letters, as in
    `d_model`), is a known macro name, or each of its letter runs is a single
    letter. **Two letters are not enough**: that would pull English words such
    as `of`, `is`, `in` into formulas (it once wrapped `of d_model is 512` in
    $…$). List markers (`P1.`, `3.`) are never math, else `- P1. W_Q` is wrapped whole.
    """
    if not chunk or ENUM_RE.match(chunk):
        return False
    # Brace groups stay whole, so a chunk may contain spaces (`\mathbb{E}_{X\sim p}`).
    # Empty the brace groups before the character check, or such a chunk counts as
    # non-math and the whole formula loses its $.
    probe = re.sub(r"\{[^{}]*\}", "{}", chunk)
    if not MATH_CHUNK_RE.match(probe):
        return False
    if "_" in chunk or "^" in chunk or LATEX_CMD.search(chunk):
        return True
    # Letters of backslash commands do not count as letter runs: `mid` in `\mid` is a
    # command name, not a word. Otherwise `P(D\mid\theta)` is non-math and shown literally.
    stripped = re.sub(r"\\[a-zA-Z]+", "", chunk)
    return all(len(r) == 1 or r in MACRO_WORDS for r in LETTER_RUN_RE.findall(stripped))


def _carries_subscript(chunk: str) -> bool:
    """Whether the chunk shows it really is a formula: a real subscript or superscript, or a LaTeX command.

    Subscripts alone are not enough: `P(D\\mid\\theta)` has a command and no
    subscript, and without this check it stays outside math with `\\mid` shown
    literally (an independent check found this form on 14 lines).
    """
    if any(not _is_identifier(m.group(0)) for m in MATH_TOKEN.finditer(chunk)):
        return True
    return bool(LATEX_CMD.search(chunk))


def _balanced(s: str) -> bool:
    depth = {"(": 0, "[": 0, "{": 0}
    pair = {")": "(", "]": "[", "}": "{"}
    for ch in s:
        if ch in depth:
            depth[ch] += 1
        elif ch in pair:
            depth[pair[ch]] -= 1
            if depth[pair[ch]] < 0:
                return False
    return all(v == 0 for v in depth.values())


def _trim(expr: str) -> str:
    """Trim spaces and stray punctuation at both ends, then trim until the brackets balance."""
    expr = expr.strip()
    while expr and expr[-1] in ".,;:":
        expr = expr[:-1]
    while expr and not _balanced(expr):
        if expr[-1] in ")]}":
            expr = expr[:-1]
        elif expr[0] in "([{":
            expr = expr[1:]
        else:
            break
    return expr.strip()


def _latexify(expr: str) -> str:
    """Normalize notation inside a segment: braces around subscripts, backslashes before bare macro names."""
    expr = MACRO_RE.sub(lambda m: "\\" + m.group(1), expr)
    expr = OPERATOR_RE.sub(lambda m: "\\operatorname{" + m.group(1) + "}", expr)
    expr = re.sub(r"\\sqrt\(([^()\n]{1,30})\)",
                  lambda m: "\\sqrt{" + m.group(1) + "}", expr)
    # `√d_k` → `\sqrt{d_k}`. `√` is one character whose bar does not cover what follows,
    # so it renders as "root times d_k". A bare `√` inside `$…$` is almost always wrong.
    expr = re.sub(r"√\s*\(([^()\n]{1,30})\)", lambda m: "\\sqrt{" + m.group(1) + "}", expr)
    expr = re.sub(r"√\s*(\{[^{}\n]{1,30}\})", lambda m: "\\sqrt" + m.group(1), expr)
    expr = re.sub(rf"√\s*([{ANCHOR}][{ANCHOR}0-9]*(?:[_^](?:\{{[^}}\n]{{1,20}}\}}|[{ANCHOR}0-9]+))?)",
                  lambda m: "\\sqrt{" + m.group(1) + "}", expr)
    # `^(k)` → `^{(k)}`. TeX's `^` takes only the next token, and `(` is one token:
    # `R^(1×D)` renders as "R with a left parenthesis as superscript" and `1×D)` drops to
    # the baseline. This rule must go with the parenthesis group in SUB: if detection
    # accepts the form but normalization does not convert it, broken math is produced
    # (a fourth review round found 9 such lines).
    expr = re.sub(r"([_^])(\([^()\n]{1,20}\))",
                  lambda m: f"{m.group(1)}{{{m.group(2)}}}", expr)
    # `d_model` → `d_{model}`; `w_<t` → `w_{<t}`; braced or single-character subscripts stay
    expr = re.sub(rf"([_^])([<>]?[A-Za-z0-9{GREEK}]{{2,}})",
                  lambda m: f"{m.group(1)}{{{m.group(2)}}}", expr)
    expr = re.sub(rf"([_^])([<>][A-Za-z0-9{GREEK}])",
                  lambda m: f"{m.group(1)}{{{m.group(2)}}}", expr)
    # Nothing to do after commands such as \theta followed by }; the braces after \frac and
    # similar commands are already in the source text
    return expr


def _split_outside_braces(seg: str) -> list[str]:
    r"""Split at spaces, but not at spaces inside brace groups: `\mathbb{E}_{X\sim p}` is one
    chunk; splitting it would strand the second half and close the $ inside the braces."""
    out, cur, depth = [], [], 0
    for ch in seg:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        if ch == " " and depth == 0:
            out.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _wrap_segment(seg: str) -> str:
    """Split a segment into chunks at spaces, join consecutive math chunks, and wrap in $ only those with a real subscript."""
    parts = _split_outside_braces(seg)
    math_flag = [_is_math_chunk(p) for p in parts]
    outs: list[str] = []
    i = 0
    while i < len(parts):
        if not math_flag[i]:
            outs.append(parts[i])
            i += 1
            continue
        j = i
        while j < len(parts) and math_flag[j]:
            j += 1
        group = parts[i:j]
        if any(_carries_subscript(p) for p in group):
            raw = " ".join(group)
            expr = _trim(raw)
            if expr and _carries_subscript(expr):
                lead = raw[:raw.index(expr)]
                tail = raw[raw.index(expr) + len(expr):]
                outs.append(f"{lead}${_latexify(expr)}${tail}")
            else:
                outs.append(raw)
        else:
            outs.append(" ".join(group))
        i = j
    return " ".join(outs)


def normalize_math(line: str) -> tuple[str, int]:
    """Wrap the bare formula segments of one line in $…$ and normalize them; returns (new line, segment count)."""
    # The spaced-repetition `::` is a hard boundary: question and answer are separate texts,
    # and no formula segment may cross it (an earlier version swallowed `::` into a formula
    # and left an unclosed $ on both sides of 19 cards).
    if "::" in line and not line.lstrip().startswith("<!--"):
        head, tail = line.split("::", 1)
        a, na = normalize_math(head)
        b, nb = normalize_math(tail)
        return f"{a}::{b}", na + nb
    if "$$" in line or re.search(r"\$\d", line) or line.count("$") % 2:
        return line, 0  # display math, money, or an unpaired $: leave the line alone

    spans = _protected(line)
    # Unwrap existing inline $…$ first so neighbouring pieces can join one segment;
    # $ inside protected spans stays
    stripped = []
    cursor = 0
    out: list[str] = []
    for m in DOLLAR_INLINE.finditer(line):
        if any(s < m.end() and m.start() < e for s, e in spans):
            continue
        if not _carries_subscript(m.group(1)):
            continue   # it could not be wrapped again (e.g. `$A$`): keep it as it is
        out.append(line[cursor:m.start()])
        out.append(m.group(1))
        cursor = m.end()
        stripped.append(m.group(1))
    out.append(line[cursor:])
    plain = "".join(out)
    if stripped:
        spans = _protected(plain)

    # Cut into segments at protected spans and non-math characters
    mask = [True] * len(plain)  # True = may be part of a formula segment
    for s, e in spans:
        for i in range(s, min(e, len(plain))):
            mask[i] = False
    # The content of `\text{}`, `\mathrm{}` and `\operatorname{}` is part of the formula,
    # even when it is CJK text. Otherwise CJK characters act as segment boundaries, split
    # `C(\text{…},\text{…})` with CJK words inside, and put a $ inside the braces.
    opaque: list[tuple[int, int]] = []
    for m in TEXTLIKE_CMD.finditer(plain):
        opaque.append((m.start(), m.end()))
    for i, ch in enumerate(plain):
        if not mask[i]:
            continue
        if any(s <= i < e for s, e in opaque):
            continue
        if ch == " " or re.match(rf"[{MATH_CHARS}]", ch):
            continue
        mask[i] = False

    result: list[str] = []
    i = 0
    n = 0
    while i < len(plain):
        if not mask[i]:
            result.append(plain[i])
            i += 1
            continue
        j = i
        while j < len(plain) and mask[j]:
            j += 1
        seg = plain[i:j]
        result.append(_wrap_segment(seg))
        n += result[-1].count("$") // 2
        i = j
    new = "".join(result)
    # Guard: this function unwraps existing $…$ so that neighbouring pieces can join, then
    # wraps again. If a piece that was wrapped ends up outside $…$ (a single symbol such
    # as `$A$`), a correct line was made worse; then the line is returned unchanged.
    return (new, n) if new != line else (line, 0)


def normalize_markdown(text: str) -> tuple[str, int, list[str]]:
    """Normalize the formulas of a whole markdown text: skip front matter and code fences, run normalize_math per line.

    Returns (normalized text, number of changed segments, issues). ``issues`` holds
    up to 2 examples of a change, each the string "old line\\nnew line"; it is
    empty when nothing changed.

    Idempotent: normalizing the result again changes nothing (see
    tests/test_rendering.py), so a renderer can call it on every write; it
    fixes new text and heals old text.
    """
    if text.startswith("---\n") and "\n---\n" in text[4:]:
        cut = text.index("\n---\n", 4) + len("\n---\n")
        head, body = text[:cut], text[cut:]
    else:
        head, body = "", text
    lines = body.split("\n")
    in_fence = False
    total = 0
    issues: list[str] = []
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not ln.strip():
            continue
        new, k = normalize_math(ln)
        if k:
            total += k
            if len(issues) < 2:
                issues.append(f"    old {ln.strip()[:78]}\n    new {new.strip()[:78]}")
            lines[i] = new
    return head + "\n".join(lines), total, issues

"""Program checks on exported text (no model): citations, numbers, formulas, links.

A paragraph cites cards with ``[[link]]``; every check is made against the
cards that paragraph cites. Formulas are checked as written - this module never
rewrites them (the card formula normalizer is not used for exports).

Formula rules follow Pandoc's ``tex_math_dollars``: an inline ``$`` must not be
followed by white space, the closing ``$`` must not follow white space nor be
followed by a digit. Display math is ``$$`` alone on a line, then the formula,
then ``$$`` alone on a line (the one form Typora shows without settings).
A table row whose formula holds ``|`` and a ``$`` that opens no formula are
reported too. Whether a formula parses is asked of a :class:`FormulaChecker`
(MathJax through Node by default); without one, parsing is "not checked".
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_CITE = re.compile(r'\[\[([^\[\]\n]+?)\]\]')
_CODE = re.compile(r'`[^`\n]*`')
_NUMBER = re.compile(r'(?<![A-Za-z_\d.,])\d+(?:[.,]\d+)*(?![A-Za-z\d])')
_ANY_NUMBER = re.compile(r'\d+(?:[.,]\d+)*')
_WS = re.compile(r'\s+')


@dataclass(frozen=True)
class Math:
    tex: str
    display: bool
    start: int
    end: int
    #: Pandoc / Typora form problems, empty when the formula is well formed.
    problems: tuple = ()


def citations(text: str) -> list[str]:
    """``[[link]]`` targets in order (an ``|alias`` part is dropped)."""
    return [m.group(1).split('|', 1)[0].strip() for m in _CITE.finditer(text)]


def _line_bounds(text: str, pos: int) -> tuple[int, int]:
    start = text.rfind('\n', 0, pos) + 1
    end = text.find('\n', pos)
    return start, len(text) if end < 0 else end


def scan_math(text: str) -> tuple[list[Math], list[int]]:
    """Formulas in ``text`` and the positions of ``$`` signs that open none."""
    masked = _CODE.sub(lambda m: ' ' * len(m.group(0)), text)
    out, stray, i, n = [], [], 0, len(masked)
    while i < n:
        ch = masked[i]
        if ch == '\\':
            i += 2
            continue
        if ch != '$':
            i += 1
            continue
        if masked.startswith('$$', i):
            close = masked.find('$$', i + 2)
            if close < 0:
                stray.append(i)
                i += 2
                continue
            tex = text[i + 2:close]
            ls, _ = _line_bounds(masked, i)
            _, le = _line_bounds(masked, close + 2)
            before, after = masked[ls:i], masked[close + 2:le]
            own = (not before.strip() and not after.strip()
                   and tex.startswith('\n') and tex.rstrip(' \t').endswith('\n'))
            problems = () if own else ('display_math_not_own_line',)
            out.append(Math(tex.strip(), True, i, close + 2, problems))
            i = close + 2
            continue
        j, close = i + 1, -1
        while j < n:
            if masked[j] == '\\':
                j += 2
                continue
            if masked[j] == '\n' and masked[j + 1:j + 2] == '\n':
                break
            if masked[j] == '$':
                close = j
                break
            j += 1
        if close < 0 or close == i + 1:
            stray.append(i)
            i += 1 if close < 0 else 2
            continue
        tex = text[i + 1:close]
        problems = []
        if tex[0].isspace():
            problems.append('inline_math_space_after_open')
        if tex[-1].isspace():
            problems.append('inline_math_space_before_close')
        if close + 1 < n and masked[close + 1].isdigit():
            problems.append('inline_math_digit_after_close')
        ls, le = _line_bounds(masked, i)
        if masked[ls:le].lstrip().startswith('|') and '|' in tex:
            problems.append('table_math_pipe')
        out.append(Math(tex.strip(), False, i, close + 1, tuple(problems)))
        i = close + 1
    return out, stray


def prose(text: str) -> str:
    """``text`` without formulas, citations and inline code (what numbers are read from)."""
    maths, _ = scan_math(text)
    pieces, last = [], 0
    for m in maths:
        pieces.append(text[last:m.start])
        pieces.append(' ')
        last = m.end
    pieces.append(text[last:])
    return _CODE.sub(' ', _CITE.sub(' ', ''.join(pieces)))


def numbers(text: str) -> list[str]:
    """Numerals written in the prose of ``text`` (thousands commas removed)."""
    return [m.group(0).replace(',', '') for m in _NUMBER.finditer(prose(text))]


def card_numbers(text: str) -> set[str]:
    """Every numeral anywhere in a card (formulas included), leniently."""
    found = set()
    for m in _ANY_NUMBER.finditer(text):
        value = m.group(0).replace(',', '')
        found.add(value)
        found.update(part for part in re.split(r'[.]', value) if part)
    return found


def squash(tex: str) -> str:
    return _WS.sub('', tex)


# --------------------------------------------------------------------------
# Formula parsing (a seam: MathJax through Node, or anything that answers alike)
# --------------------------------------------------------------------------
class FormulaChecker(Protocol):
    name: str

    def check(self, items: list[tuple[str, bool]]) -> list[str | None]:
        """One answer per ``(tex, display)``: ``None`` when it parses, else the error."""


class CheckerUnavailable(ValueError):
    """The MathJax parse check cannot run on this machine (no node, no script, or no ``mathjax-full``)."""


#: How to make the check run, or to go on without it knowingly.
INSTALL = ('Install MathJax for the check: `npm install --prefix ~/.cache/sb-mathjax mathjax-full@3`, then set '
           'SB_MATHJAX_NODE_PATH=~/.cache/sb-mathjax/node_modules (SB_MATHJAX_CHECK names another checker script). '
           'Or run `sb export --no-formula-check`: the article then says at the top that its formulas were not '
           'parse-checked')


def preflight(checker) -> str | None:
    """Why ``checker`` cannot check formulas here, or None when it parses a plain formula and rejects a broken one.

    Run before an export asks any model, so a missing ``node`` or ``mathjax-full`` stops it before the calls
    are spent rather than after them, and never skips the check without a word.
    """
    if checker is None:
        return 'no checker: node is not on PATH, or tools/mathjax_check.js is missing (SB_MATHJAX_CHECK)'
    try:
        good, bad = checker.check([('a_{1}', False), ('\\frac{a}{b', False)])
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if good is not None or bad is None:
        return f'{getattr(checker, "name", "the checker")} does not tell a valid formula from a broken one'
    return None


class NodeMathJax:
    """``node tools/mathjax_check.js`` with ``mathjax-full`` on ``NODE_PATH``."""

    name = 'mathjax'

    def __init__(self, script: str | os.PathLike, node_path: str | None = None, node: str = 'node'):
        self.script = str(script)
        self.node_path = node_path
        self.node = node

    @classmethod
    def from_env(cls) -> 'NodeMathJax | None':
        """``SB_MATHJAX_CHECK`` = script path, ``SB_MATHJAX_NODE_PATH`` = its node_modules; else none."""
        script = os.environ.get('SB_MATHJAX_CHECK')
        if not script:
            here = Path(__file__).resolve().parents[3] / 'tools' / 'mathjax_check.js'
            script = str(here) if here.is_file() else None
        if not script or not shutil.which('node'):
            return None
        return cls(script, os.environ.get('SB_MATHJAX_NODE_PATH'))

    def check(self, items):
        if not items:
            return []
        env = dict(os.environ)
        if self.node_path:
            env['NODE_PATH'] = self.node_path
        lines = ''.join(json.dumps({'tex': t, 'display': d}, ensure_ascii=False) + '\n' for t, d in items)
        done = subprocess.run([self.node, self.script], input=lines, capture_output=True, text=True,
                              env=env, timeout=300)
        answers = [json.loads(x) for x in done.stdout.splitlines() if x.strip()]
        if done.returncode != 0 or len(answers) != len(items):
            raise CheckerUnavailable(f'MathJax check failed: {done.stderr.strip()[:500]}')
        return [None if a.get('ok') else str(a.get('error') or 'parse error') for a in answers]


# --------------------------------------------------------------------------
# Paragraph and article checks
# --------------------------------------------------------------------------
@dataclass
class CardIndex:
    """What the checks need to know about the cards in scope."""
    by_link: dict      # link or title -> card dict (id, title, cite, body)
    links: set         # every link target that exists in the vault

    @classmethod
    def from_input(cls, packet: dict) -> 'CardIndex':
        by_link = {}
        for card in packet['cards'].values():
            by_link[card['cite']] = card
            by_link.setdefault(card['title'], card)
        return cls(by_link, set(packet.get('links') or []) | set(by_link))

    def card_text(self, card: dict) -> str:
        return f"{card['title']}\n{card['body']}"


def check_paragraph(pid: str, text: str, index: CardIndex, parse_errors: dict | None = None) -> list[dict]:
    """Findings for one paragraph (``parse_errors`` maps a formula to its MathJax error)."""
    findings = []

    def add(code, detail):
        findings.append({'paragraph': pid, 'code': code, 'detail': detail})

    if re.search(r'^\s{0,3}#{1,6}\s', text, re.M):
        add('heading_in_paragraph', 'headings are made by the program; write paragraphs only')
    cited = citations(text)
    if not cited:
        add('no_citation', 'the paragraph names no card with [[...]]')
    unknown = [c for c in cited if c not in index.links]
    for name in unknown:
        add('unknown_link', f'[[{name}]] is not a card')
    cards = [index.by_link[c] for c in cited if c in index.by_link]
    known_numbers = set()
    formulas = set()
    for card in cards:
        body = index.card_text(card)
        known_numbers |= card_numbers(body)
        formulas.update(squash(m.tex) for m in scan_math(card['body'])[0])
    for value in numbers(text):
        if value not in known_numbers:
            add('number_not_in_cited_cards', value)
    maths, stray = scan_math(text)
    for m in maths:
        for problem in m.problems:
            add(problem, ('$$' if m.display else '$') + m.tex[:80])
        if squash(m.tex) and not any(squash(m.tex) in f for f in formulas):
            add('formula_not_in_cited_cards', m.tex[:120])
        error = (parse_errors or {}).get((m.tex, m.display))
        if error:
            add('formula_parse_error', f'{m.tex[:80]}: {error}')
    for pos in stray:
        add('stray_dollar', text[max(0, pos - 20):pos + 20])
    prose, last = [], 0  # outside formulas: a backslash and an n is a line break read wrongly (export reads TeX
    for m in maths:      # backslashes leniently, decision D-13), never text a card has
        prose.append(text[last:m.start])
        last = m.end
    prose.append(text[last:])
    for found in re.finditer(r'\\n', ''.join(prose)):
        whole = ''.join(prose)
        add('literal_newline', whole[max(0, found.start() - 20):found.end() + 20])
    return findings


def parse_errors(texts: list[str], checker: FormulaChecker | None) -> dict | None:
    """``{(tex, display): error}`` for every formula in ``texts``; ``None`` when not checked."""
    if checker is None:
        return None
    items = sorted({(m.tex, m.display) for t in texts for m in scan_math(t)[0]})
    return {item: err for item, err in zip(items, checker.check(items)) if err}


def char_count(text: str) -> dict:
    """Length of article prose: characters without white space, CJK characters, Latin words."""
    body = _CITE.sub('', text)
    return {'chars': len(_WS.sub('', body)),
            'cjk_chars': len(re.findall(r'[\u3400-\u9fff]', body)),
            'latin_words': len(re.findall(r'[A-Za-z][A-Za-z\-]*', prose(body)))}


def _grams(text: str, n: int) -> set[str]:
    flat = _WS.sub('', prose(text))
    return {flat[i:i + n] for i in range(max(0, len(flat) - n + 1))}


def duplicate_pairs(paragraphs: list[tuple[str, str, str]], *, n: int = 8, threshold: float = 0.35,
                    limit: int = 20) -> list[dict]:
    """Paragraph pairs from different units that share many character ``n``-grams.

    ``paragraphs`` is ``(unit, paragraph id, text)``. The score is the shared
    n-grams over the smaller paragraph's n-grams.
    """
    grams = [(u, pid, text, _grams(text, n)) for u, pid, text in paragraphs]
    out = []
    for i, (ua, pa, ta, ga) in enumerate(grams):
        for ub, pb, tb, gb in grams[i + 1:]:
            if ua == ub or not ga or not gb:
                continue
            score = len(ga & gb) / min(len(ga), len(gb))
            if score >= threshold:
                out.append({'a': pa, 'b': pb, 'score': round(score, 3)})
    out.sort(key=lambda d: (-d['score'], d['a'], d['b']))
    return out[:limit]

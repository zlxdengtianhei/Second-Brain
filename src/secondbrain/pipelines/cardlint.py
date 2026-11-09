"""The program's fixed checks of a card's text, raised as questions for its reviewer; never a verdict of its own.

Two checks, each measured on cards blind judges had judged before it was taken (the rest were not: they marked
cards at the base rate):

- ``points_at_source``: a phrase that points at the source instead of saying the thing (the source "says", the
  textbook "puts", "in the table", "in the example", "this section", "chapter 3" ...). Words that are also words
  of the subject (the original text an index keeps apart, a context window) are not counted; "in the table" is not
  when the card has a table, nor "in the example" when the card sets an example up on another line.
- ``symbol``: a one-letter symbol given a number, multiplied or raised to a power (``M=16``, ``N×D``) that the card
  never says what it stands for.

The reviewer answers each question yes or no with a reason (``answers`` in the review row, :data:`secondbrain.schemas.REVIEW_CARDS`); a yes sends an accepted card back as
``unreadable`` (:func:`turned`), a no leaves it as it is. The pattern tables are Chinese, the language the checks were
measured on; this module is one of the language tables.
"""
from __future__ import annotations

import re

# --- points at the source ------------------------------------------------------------------------------------------
POINTERS = (
    ('原文', re.compile(r'原文(?:里|中)?(?:说|强调|把|写|称|提到|指出|认为|列为|讲|的说法)')),
    ('教材', re.compile(r'(?:按|据|照)教材|教材(?:里|中)?(?:说|把|按|将|的概括|这句|论断|列|称|强调|写|归)')),
    ('对话', re.compile(r'对话(?=曾|里|中|说|第|提到|写|把)')),
    ('讲义/本节', re.compile(r'讲义|本节|本章|上一节|下一节')),
    ('上文/前文', re.compile(r'(?<!上)下文(?!本)|(?<![的])上文|前文')),
    ('表中', re.compile(r'(?<![词码])表中|上表|下表|这张表|该表')),
    ('例中', re.compile(r'(?<![正负])例中|这一例|上例|该例|示意算例|那个例子')),
    ('那卡/该题', re.compile(r'那卡|那张卡|该题|此题')),
    ('总图/前三类', re.compile(r'总图|前三类|前两类|前几类')),
    ('第N章', re.compile(r'第\s*\d+\s*章')),
    ('脚本', re.compile(r'脚本(?!\s*[`\w./-]*\.py)')),
)
_TABLE = re.compile(r'^\s*\|.*\|\s*$\n^\s*\|\s*:?-{2,}', re.M)
_EXAMPLE_LINE = re.compile(r'(?:^|\n)\s*(?:- P\d+\.\s*)?(?:例|示例|算例)[：:]|例如|比如')


def _lines(card: dict) -> list[str]:
    """The card's knowledge text, one string per field (a review card's question and answer each one)."""
    out = [card.get('essence'), card.get('intuition')]
    out += list(card.get('propositions') or []) + list(card.get('confusions') or [])
    for f in card.get('flashcards') or []:
        if isinstance(f, dict):
            out += [f.get('q'), f.get('a')]
    return [x for x in out if isinstance(x, str) and x.strip()]


def pointers(card: dict) -> list[tuple[str, str]]:
    """``(kind, the words around it)`` for each phrase of the card that may point at the source."""
    lines = _lines(card)
    text = '\n'.join(lines)
    hits = []
    for line in lines:
        for name, rx in POINTERS:
            m = rx.search(line)
            if not m:
                continue
            if name == '表中' and _TABLE.search(text):
                continue
            if name == '例中' and _EXAMPLE_LINE.search(text.replace(line, '')):
                continue
            hits.append((name, line[max(0, m.start() - 8):m.end() + 8].strip()))
    return hits


# --- one-letter symbols ---------------------------------------------------------------------------------------------
_GREEK = {'alpha': 'α', 'beta': 'β', 'gamma': 'γ', 'delta': 'δ', 'epsilon': 'ε', 'varepsilon': 'ε', 'theta': 'θ',
          'lambda': 'λ', 'mu': 'μ', 'sigma': 'σ', 'tau': 'τ', 'phi': 'φ', 'pi': 'π', 'ell': 'ℓ', 'omega': 'ω'}
_LETTER = r'[A-Za-zα-ωℓℏ]'
_WORDCMD = re.compile(r'\\(?:text|operatorname|mathrm|mathbf|mathit|mathcal|textbf|boldsymbol)\s*\{[^{}]*\}'
                      r'|\\mathbb\s*\{?[A-Z]\}?')
_OP = r'[=×*/^_<>≤≥≠±+(（|·\[]'
_MATH = re.compile(r'\$\$?([^$]+)\$\$?')
_NOUN_END = (r'(?:数|量|参数|个数|维数|维度|向量|矩阵|节点|点|层|层数|词|文档|集合|长度|阈值|温度|常数|系数|权重|分数|概率|值|'
             r'规则|查询|中心|样本|序列|位置|token|模型|空间|大小|宽度|上限|数目|数量|函数|损失|状态|编码|码本|输入|输出|结果|'
             r'距离|半径|角|超平面|平面|库|语料|列表|分|为|是|等于|记为|叫)')
_PARAM = r'(?<![A-Za-z\\])\$?{s}\$?\s*(?:=\s*\d|×|\\times|\^)|(?:×|\\times)\s*\$?{s}(?![A-Za-z])'


def _math_letters(seg: str) -> set:
    seg = _WORDCMD.sub(' ', seg)
    seg = re.sub(r'\\(' + '|'.join(_GREEK) + r')(?![A-Za-z])', lambda m: _GREEK[m.group(1)], seg)
    seg = re.sub(r'\\[A-Za-z]+', ' ', seg)            # \frac, \top, \le, \log ... are not symbols
    seg = re.sub(r'[_^]\s*\{[^{}]*\}', ' ', seg)       # sub- and superscript groups are indices
    seg = re.sub(r'[_^]\s*\\?' + _LETTER, ' ', seg)    # a one-letter sub- or superscript
    return {m.group(1) for m in re.finditer(r'(?<![A-Za-z0-9])(' + _LETTER + r')(?![A-Za-z])', seg)}


def _text_letters(line: str) -> set:
    line = _MATH.sub(' ', line)
    line = re.sub(r'`[^`]*`', ' ', line)
    out = set()
    for m in re.finditer(r'(?<![A-Za-z0-9_\-])(' + _LETTER + r')(?![A-Za-z0-9])', line):
        s, e = m.start(), m.end()
        before, after = line[max(0, s - 2):s], line[e:e + 2]
        if re.search(_OP + r'\s*$', before) or re.match(r'\s*' + _OP, after):
            if re.match(r'_', after) or re.search(r'[=×*/<>≤≥≠±+|·]', before + after) or \
                    re.match(r'\s*[(（]\s*[A-Za-z0-9,.\-]', after):
                out.add(m.group(1))
    out |= {m.group(1) for m in re.finditer(r'(?<![A-Za-z0-9])(' + _LETTER + r')_[A-Za-z0-9{]', line)}
    return out


def _defined(sym: str, text: str) -> bool:
    s = re.escape(sym)
    sub = r'(?:_\{?[^\s$，。；、]*\}?)?(?:\^\{?[^\s$，。；、]*\}?)?(?:\s*\([^()]{0,12}\))?'
    lead = r'(?<![A-Za-z\\])\|?\$?\s*' + s + sub + r'\s*\|?\$?'
    listed = r'(?:\s*[、,，和与及]\s*\$?[^\s$。；]{1,14}?\$?){0,5}'
    patterns = (
        lead + listed + r'\s*(?:都|分别|就)?(?:是|为|表示|指|代表|即|叫|记作|对应|[：:](?!\s*\d))',
        lead + r'\s*[（(][^）)]*[\u4e00-\u9fff]',
        _NOUN_END + r'\s*\$?\s*' + s + r'(?![A-Za-z])',
        r'(?:其中|设|令|记|这里|字母|符号)\s*\$?\s*' + s + r'(?![A-Za-z])',
        lead + r'\s*(?:个|维|层|次|条|篇|项|位|轮|步|段|簇|桶|张表|比特|bit)',
        r'第\s*\$?\s*' + s + r'\s*\$?\s*(?:个|层|维|次|项|段|位|轮|步|行|列)',
    )
    return any(re.search(p, text) for p in patterns)


def _labels(text: str) -> set:
    """Point labels: a line that gives three or more single capital letters a value (A=0, B=3, C=6)."""
    out = set()
    for line in text.split('\n'):
        hits = re.findall(r'(?<![A-Za-z])([A-Z])\s*=\s*[-\d(（]', line)
        if len(set(hits)) >= 3:
            out |= set(hits)
        for m in re.finditer(r'(?<![A-Za-z])([A-Z])(?:\s*[、,，和与]\s*([A-Z])(?![A-Za-z])){2,}', line):
            out |= set(re.findall(r'[A-Z]', m.group(0)))
    return out


def _function_letters(text: str) -> set:
    """A letter written as a function, f(x) or S(q,d), its arguments when the function has a name, and π, e."""
    out = set(re.findall(r'(?<![A-Za-z\\])([A-Za-z])(?:_\{?[^\s$(]*\}?)?\s*\(', text)) | {'π', 'e'}
    for m in re.finditer(r'(?:\\(?:mathrm|operatorname|text)\{)?[A-Za-z]{2,}\}?\s*\(([^()]{1,20})\)', text):
        out |= set(re.findall(r'(?<![A-Za-z])([A-Za-z])(?![A-Za-z])', m.group(1)))
    return out


def symbols(card: dict) -> list[str]:
    """The one-letter symbols the card gives a number, multiplies or raises to a power and never explains."""
    lines = _lines(card)
    text = '\n'.join(lines)
    used = set()
    for line in lines:
        for m in _MATH.finditer(line):
            used |= _math_letters(m.group(1))
        used |= _text_letters(line)
    skip = _labels(text) | _function_letters(text)
    return sorted(x for x in used if x not in skip and not _defined(x, text)
                  and re.search(_PARAM.format(s=re.escape(x)), text))


# --- the questions, and the answers ---------------------------------------------------------------------------------
def questions(card: dict) -> list[dict]:
    """The program's questions on ``card``, numbered K1, K2 ... (``id``, ``check``, ``question``)."""
    out = []
    for kind, words in pointers(card):
        out.append({'check': 'points_at_source',
                    'question': f'The card says "{words}" ({kind}): does it point at something only the source shows, '
                                'so that the card cannot be read on its own?'})
    for sym in symbols(card):
        out.append({'check': 'symbol',
                    'question': f'The card gives the symbol {sym} a value or uses it in a product, and does not say '
                                f'what {sym} stands for: does a reader need the source to know it?'})
    return [{'id': f'K{i}', **q} for i, q in enumerate(out, 1)]


def of_cards(cards) -> dict:
    """``{slug: questions}`` for the cards that have any."""
    out = {}
    for c in cards or []:
        if isinstance(c, dict) and isinstance(c.get('slug'), str):
            asked = questions(c)
            if asked:
                out[c['slug']] = asked
    return out


def answer_problems(row: dict, asked: list[dict]) -> list[str]:
    """What is wrong with a review row's ``answers`` to the questions ``asked`` of its card: each answered once, yes
    or no, with a reason."""
    where = row.get('slug')
    given = [a for a in row.get('answers') or [] if isinstance(a, dict)]
    ids = [a.get('id') for a in given]
    wanted = [q['id'] for q in asked]
    out = []
    if sorted(map(str, ids)) != sorted(wanted):
        out.append(f'{where}: answers must answer each of the program\'s questions on this card once '
                   f'({", ".join(wanted) or "none"}), each {{"id", "answer": "yes" | "no", "reason"}}')
    for a in given:
        if a.get('answer') not in ('yes', 'no') or not isinstance(a.get('reason'), str) or not a['reason'].strip():
            out.append(f'{where}: answer {a.get("id")!r} needs "answer": "yes" or "no" and a reason')
    return out


def turned(row: dict, asked: list[dict]) -> dict:
    """``row`` sent back as ``unreadable`` when it accepts and answers yes to a question; else ``row``. Kept as a
    turned note (``turned``: what the reviewer answered), as :func:`secondbrain.review_notes.turned` keeps one."""
    if row.get('decision') != 'accept':
        return row
    by_id = {q['id']: q for q in asked}
    yes = next((a for a in row.get('answers') or [] if isinstance(a, dict) and a.get('answer') == 'yes'
                and a.get('id') in by_id), None)
    if yes is None:
        return row
    note = {'category': 'unreadable', 'text': f"{by_id[yes['id']]['question']} The review answered yes: {yes['reason']}"}
    kept = {k: v for k, v in row.items() if k not in ('amend', 'decision', 'reason', 'blocking')}
    return {**kept, 'decision': 'revise', 'blocking': 'unreadable',
            'reason': f"the review answered yes to the program's question {yes['id']}: {note['text']}",
            'turned': {'from': 'accept', 'reason': row.get('reason'), 'note': note, 'question': yes['id']}}

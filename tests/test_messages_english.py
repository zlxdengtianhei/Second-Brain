"""Error and issue messages are English; Chinese text lives only in the language tables.

Scans every string literal (docstrings and comments excluded) in the package,
except the diagram package and the review registry, which have their own
owners. The allowed literals are page text by language or character classes.
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / 'src' / 'secondbrain'
CJK = re.compile(r'[　-〿一-鿿＀-￯]')
SKIP = {'labels.py', 'cards.py', 'review_registry.py'}   # language tables; separately owned
#: Files whose Chinese may stand only in patterns, the rules measured on Chinese text: a literal that is the pattern
#: of a call to ``re`` (``re.compile(r'...')``); their names, questions and messages are checked as any other.
PATTERN_ONLY = {'pipelines/cardlint.py'}
RE_CALLS = {'compile', 'search', 'match', 'fullmatch', 'finditer', 'findall', 'sub', 'subn', 'split'}
ALLOWED = {
    ('pipelines/chunking.py', '(?<=[。！？!?；;])|(?<=[.!?])(?=\\s)'),   # sentence ends
    ('rendering.py', '　-〿㐀-䶿一-鿿豈-﫿＀-￯‘’“”—–'),
}


def literals(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    parts = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts.update(id(v) for v in node.values)
            yield node.lineno, ''.join(v.value for v in node.values if isinstance(v, ast.Constant))
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in docstrings and id(node) not in parts):
            yield node.lineno, node.value


def outside_patterns(path):
    """``(line, text)`` of each string literal with Chinese that is not the pattern of a call to ``re`` (an f-string's
    text is never one)."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    patterns = {id(node.args[0]) for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in RE_CALLS
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 're' and node.args}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and CJK.search(node.value) \
                and id(node) not in patterns:
            out.append((node.lineno, node.value))
    return out


def test_no_chinese_message_literals_outside_the_language_tables():
    found = []
    for path in sorted(PACKAGE.rglob('*.py')):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel.startswith('diagrams/') or path.name in SKIP:
            continue
        if rel in PATTERN_ONLY:
            found += [f'{rel}:{line}: {text[:60]!r}' for line, text in outside_patterns(path)]
            continue
        for line, text in literals(path):
            if CJK.search(text) and (rel, text) not in ALLOWED:
                found.append(f'{rel}:{line}: {text[:60]!r}')
    assert found == []


#: Docstrings that quote a zh page label as an example.
QUOTED_LABELS = {('moc.py', '［跨域→…］')}


def test_a_file_of_patterns_holds_chinese_only_in_patterns(tmp_path):
    """The check of :data:`PATTERN_ONLY` turns red on a question or a message with Chinese in it, in any form of
    string, and not on the pattern of a call to ``re``."""
    path = tmp_path / 'rules.py'
    path.write_text("import re\nRX = re.compile(r'例中|上例')\nHIT = re.search(r'表中', 'x')\n"
                    "A = 'Does the card point at 原文 here?'\nB = lambda w: f'这张卡提到 {w}，它指向来源吗？'\n"
                    "C = r'这张卡是否指向来源？'\nD = lambda w: rf'这张卡提到 {w}，它指向来源吗？'\n"
                    "E = r'第' + re.compile(r'\\s*章').pattern\n", encoding='utf-8')
    assert sorted({line for line, _ in outside_patterns(path)}) == [4, 5, 6, 7, 8]


def test_comments_and_docstrings_are_english():
    found = []
    for path in sorted(PACKAGE.rglob('*.py')):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel.startswith('diagrams/') or path.name == 'review_registry.py':
            continue
        source = path.read_text(encoding='utf-8')
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT and CJK.search(token.string):
                found.append(f'{rel}:{token.start[0]}: {token.string[:60]!r}')
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False) or ''
                for label in (label for name, label in QUOTED_LABELS if name == rel):
                    doc = doc.replace(label, '')
                if CJK.search(doc):
                    found.append(f'{rel}:{node.body[0].lineno}: docstring {doc[:60]!r}')
    assert found == []


def test_install_hints_never_name_the_pypi_package():
    """`secondbrain` on PyPI is someone else's package: hints install the extras from the checkout."""
    root = PACKAGE.parents[1]
    wrong = re.compile(r'pip install\s+["\']?secondbrain\b')
    found = []
    for path in sorted(PACKAGE.rglob('*.py')) + sorted((root / 'skills').rglob('*.md')) + \
            sorted((PACKAGE / 'prompts').rglob('*.md')):
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if wrong.search(line):
                found.append(f'{path.relative_to(root)}:{number}: {line.strip()[:80]}')
    assert found == []
    from secondbrain import labels
    for language in ('en', 'zh'):
        assert 'pip install -e ".[review]"' in labels.install_hint('review', language)

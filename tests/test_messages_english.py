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


def test_no_chinese_message_literals_outside_the_language_tables():
    found = []
    for path in sorted(PACKAGE.rglob('*.py')):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel.startswith('diagrams/') or path.name in SKIP:
            continue
        for line, text in literals(path):
            if CJK.search(text) and (rel, text) not in ALLOWED:
                found.append(f'{rel}:{line}: {text[:60]!r}')
    assert found == []


#: Docstrings that quote a zh page label as an example.
QUOTED_LABELS = {('moc.py', '［跨域→…］')}


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

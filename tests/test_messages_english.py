"""Error and issue messages are English; Chinese text lives only in the language tables.

Scans every string literal (docstrings and comments excluded) in the package,
except the diagram package and the review registry, which have their own
owners. The allowed literals are page text by language or character classes.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / 'src' / 'secondbrain'
CJK = re.compile(r'[　-〿一-鿿＀-￯]')
SKIP = {'labels.py', 'cards.py', 'review_registry.py'}   # language tables; separately owned
ALLOWED = {
    ('pipelines/chunking.py', '(?<=[。！？!?；;])|(?<=[.!?])(?=\\s)'),   # sentence ends
    ('rendering.py', '　-〿㐀-䶿一-鿿豈-﫿＀-￯‘’“”—–'),
    ('domcreate.py', ' 的反向'),   # default gloss of an inverse relation word (catalog content)
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

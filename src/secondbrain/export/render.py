"""Write an assembled article as Markdown, and optionally as HTML (program only).

Markdown keeps formulas exactly as written: ``$…$`` inline and ``$$`` blocks
on their own lines (a displayed formula gets blank lines around it so every
editor sees a block). The first line after the front matter tells Typora users
how to switch on inline formulas.

HTML is made with Python-Markdown and ``pymdownx.arithmatex`` (generic mode:
``\\(…\\)`` and ``\\[…\\]`` in ``arithmatex`` elements) and typeset by MathJax 3
loaded by the page itself, so it does not depend on any editor setting. These
two packages are the ``export`` extra.
"""
from __future__ import annotations

import html as html_lib
import re

import yaml

from .. import labels
from .checks import scan_math

MATHJAX_URL = 'https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml-full.js'


def _blocks(text: str) -> str:
    """``text`` with blank lines around each displayed formula (content unchanged)."""
    maths = [m for m in scan_math(text)[0] if m.display and not m.problems]
    if not maths:
        return text.strip()
    out, last = [], 0
    for m in maths:
        before = text[last:m.start].strip()
        if before:
            out.append(before)
        out.append(text[m.start:m.end].strip())
        last = m.end
    after = text[last:].strip()
    if after:
        out.append(after)
    return '\n\n'.join(out)


def to_markdown(article: dict) -> str:
    """The article as Markdown with front matter (see the module docstring)."""
    lang = article.get('language', 'zh')
    front = yaml.safe_dump(article['meta'], allow_unicode=True, sort_keys=False, width=1000).strip()
    text = labels.export(lang)
    lines = ['---', front, '---', text['hint'], '', f"# {article['title']}", '']
    if article.get('unchecked'):
        lines += [text['unchecked'].format(reason=article['unchecked']), '']

    def paragraph(body, flag):  # a paragraph the review or the program did not pass says so, on the line before
        return ([text['flag'].format(reason=flag), ''] if flag else []) + [_blocks(body), '']
    flags = article.get('intro_flags') or []
    for i, para in enumerate(article.get('intro') or []):
        lines += paragraph(para, flags[i] if i < len(flags) else None)
    for section in article['sections']:
        lines += [f"## {section['pillar']}", '']
        if section.get('transition'):
            lines += paragraph(section['transition'], section.get('transition_flag'))
        for block in section['blocks']:
            if 'heading' in block:
                lines += [f"### {block['heading']}", '']
            elif 'missing' in block:  # a part its writer never wrote: what is missing, why, and its cards
                cards = text['card_sep'].join(f'[[{name}]]' for name in block['cards'])
                lines += [text['missing'].format(reason=block['missing'], cards=cards), '']
            else:
                lines += paragraph(block['text'], block.get('flag'))
    if article.get('unused'):
        lines += [f"## {text['unused']}", '']
        lines += [f'- [[{name}]]' for name in article['unused']] + ['']
    return '\n'.join(lines).rstrip() + '\n'


_STYLE = """
body { max-width: 46rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.75;
       font-family: -apple-system, "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; color: #222; }
h1 { font-size: 1.9rem; } h2 { margin-top: 2.4rem; border-bottom: 1px solid #ddd; padding-bottom: .2rem; }
h3 { margin-top: 1.6rem; } blockquote { color: #666; border-left: 3px solid #ccc; margin-left: 0; padding-left: 1rem; }
.cite { color: #6a737d; font-size: .82em; white-space: nowrap; }
.cite::before { content: "["; } .cite::after { content: "]"; }
table { border-collapse: collapse; } td, th { border: 1px solid #ccc; padding: .3rem .6rem; }
.arithmatex { overflow-x: auto; }
"""


def to_html(markdown_text: str, title: str, language: str = 'zh') -> str:
    """A standalone HTML page of the article; formulas are typeset by MathJax in the page."""
    try:
        import markdown
    except ImportError as exc:  # pragma: no cover - depends on the install
        from ..labels import install_hint
        raise RuntimeError(f'--html needs Python-Markdown and pymdown-extensions: {install_hint("export")}') from exc
    body = re.sub(r'\A---\n.*?\n---\n', '', markdown_text, count=1, flags=re.S)
    body = body.replace(labels.export(language)['hint'] + '\n', '', 1)
    rendered = markdown.markdown(body, extensions=['pymdownx.arithmatex', 'tables', 'sane_lists'],
                                 extension_configs={'pymdownx.arithmatex': {'generic': True}})
    rendered = re.sub(r'\[\[([^\[\]\n]+?)\]\]', lambda m: f'<span class="cite">{m.group(1)}</span>', rendered)
    config = ('window.MathJax = {tex: {inlineMath: [["\\\\(", "\\\\)"]], displayMath: [["\\\\[", "\\\\]"]]},'
              ' options: {ignoreHtmlClass: ".*|", processHtmlClass: "arithmatex"}};')
    return ('<!DOCTYPE html>\n<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<title>{title}</title>\n<style>{style}</style>\n<script>{config}</script>\n'
            '<script id="MathJax-script" async src="{url}"></script>\n</head>\n<body>\n{body}\n</body>\n</html>\n'
            ).format(lang='zh-CN' if language == 'zh' else 'en', title=html_lib.escape(title), style=_STYLE,
                     config=config, url=MATHJAX_URL, body=rendered)

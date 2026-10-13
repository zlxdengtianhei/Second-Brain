"""Deterministic card rendering: structured fields in, Markdown card out.

Models never write card Markdown. The pipeline hands validated fields to
:func:`render_card`, which writes the frontmatter contract and the body
sections (essence, propositions, optional diagram, intuition and contrasts,
related, review cards). The "related" section is generated from the
frontmatter relations only: one ``type:: [[Title]]`` inline field per
(type, target) pair, so Breadcrumbs can read it, never a bare card id, never a
duplicate line. Relations marked ``tier: extension`` are folded into a
``<details>`` block in the plain ``[[Title]] — type`` form that Breadcrumbs
ignores. The diagram section is filled only from the diagram step's output
(validated IR turned into Mermaid by code); models never write Mermaid here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from .rendering import normalize_markdown

HEADINGS = {
    'zh': {'essence': '本质', 'propositions': '命题', 'diagram': '图',
           'intuition': '直觉与易混对比', 'related': '相关', 'flashcards': '复习卡',
           'intuition_label': '直觉：', 'contrast_label': '易混对比：', 'none': '暂无。',
           'extension': '拓展：可能存在关系（{n} 条）'},
    'en': {'essence': 'Essence', 'propositions': 'Propositions', 'diagram': 'Diagram',
           'intuition': 'Intuition and contrasts', 'related': 'Related', 'flashcards': 'Review cards',
           'intuition_label': 'Intuition: ', 'contrast_label': 'Contrast: ', 'none': 'None yet.',
           'extension': 'Extension: possible relations ({n})'},
}

KINDS = ('concept', 'mechanism', 'contrast', 'derivation', 'fact')
STATUSES = ('confirmed', 'needs-review')
#: Characters that break Obsidian file names or wiki links.
TITLE_FORBIDDEN = set('/\\:*?"<>|#^[]')
MAX_TITLE = 120


def title_problem(title: object) -> str | None:
    """``None`` when ``title`` can be used verbatim as the file name."""
    if not isinstance(title, str) or not title.strip():
        return 'title is empty'
    if title != title.strip():
        return 'title has leading or trailing spaces'
    if '\n' in title or '\r' in title or '\t' in title:
        return 'title contains a line break or tab'
    if len(title) > MAX_TITLE:
        return f'title is longer than {MAX_TITLE} characters'
    bad = sorted(set(title) & TITLE_FORBIDDEN)
    if bad:
        return f'title contains characters not allowed in file names or links: {"".join(bad)}'
    if title.startswith('.') or title.endswith('.'):
        return 'title must not start or end with a dot'
    return None


def slugify(value: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')
    return slug[:60].strip('-')


@dataclass(frozen=True)
class Relation:
    type: str
    target: str            # card id
    target_title: str
    note: str | None = None
    tier: str | None = None


def _one_line(text: str) -> str:
    return ' '.join(str(text).split())


def render_body(fields: dict, relations: list[Relation], *, language: str = 'en',
                diagram: str | None = None) -> str:
    """Body Markdown from validated fields.

    ``diagram`` is the finished text for the diagram section (a fenced Mermaid
    block from :func:`secondbrain.diagrams.fenced`) or ``None`` for no section.
    """
    h = HEADINGS[language]
    lines = [f'## {h["essence"]}', _one_line(fields['essence']), '',
             f'## {h["propositions"]}']
    lines += [f'- P{i}. {_one_line(p)}' for i, p in enumerate(fields['propositions'], 1)]
    lines.append('')
    if diagram:
        lines += [f'## {h["diagram"]}', diagram.strip('\n'), '']
    lines.append(f'## {h["intuition"]}')
    intuition = fields.get('intuition')
    confusions = fields.get('confusions') or []
    if intuition:
        lines.append(f'- {h["intuition_label"]}{_one_line(intuition)}')
    lines += [f'- {h["contrast_label"]}{_one_line(c)}' for c in confusions]
    if not intuition and not confusions:
        lines.append(h['none'])
    lines.append('')
    lines += render_related(relations, language=language)
    lines.append(f'## {h["flashcards"]}')
    lines += [f'{_one_line(c["q"])}::{_one_line(c["a"])}' for c in fields['flashcards']]
    text = '\n'.join(lines) + '\n'
    return normalize_markdown(text)[0]


def render_related(relations: list[Relation], *, language: str = 'en') -> list[str]:
    h = HEADINGS[language]
    core, extension, seen = [], [], set()
    for rel in relations:
        key = (rel.type, rel.target)
        if key in seen or not rel.target_title:
            continue
        seen.add(key)
        note = _one_line(rel.note) if rel.note else ''
        if rel.tier == 'extension':
            suffix = (f'（{note}）' if language == 'zh' else f' ({note})') if note else ''
            extension.append(f'- [[{rel.target_title}]] — {rel.type}{suffix}')
        else:
            suffix = (f'（{note}）' if language == 'zh' else f' ({note})') if note else ''
            core.append(f'- {rel.type}:: [[{rel.target_title}]]{suffix}')
    lines = [f'## {h["related"]}']
    lines += core or ([h['none']] if not extension else [])
    if extension:
        lines += ['', f'<details><summary>{h["extension"].format(n=len(extension))}</summary>', '',
                  *extension, '', '</details>']
    lines.append('')
    return lines


def render_frontmatter(meta: dict) -> str:
    """YAML frontmatter text; key order follows the card contract."""
    order = ['id', 'title', 'kind', 'domain', 'pillar', 'topic', 'tags', 'aliases', 'sources',
             'relations', 'status', 'created']
    ordered = {k: meta[k] for k in order if k in meta}
    ordered.update({k: v for k, v in meta.items() if k not in ordered})
    text = yaml.safe_dump(ordered, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=1000000)
    return '---\n' + text + '---\n'


def render_card(meta: dict, fields: dict, relations: list[Relation], *, language: str = 'en',
                diagram: str | None = None) -> bytes:
    return (render_frontmatter(meta) + render_body(fields, relations, language=language,
                                                   diagram=diagram)).encode('utf-8')

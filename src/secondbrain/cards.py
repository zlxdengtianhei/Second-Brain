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
    """``text`` on one Markdown line: each line break, with the blanks around it, becomes one space.

    Runs of spaces inside a line stay: they can be the point of the card (a
    string literal, or input such as ``3  10 -2`` read with ``split()``).
    """
    return re.sub(r'[ \t]*(?:\r\n|\r|\n)+[ \t]*', ' ', str(text)).strip()


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
    intuition = fields.get('intuition')
    confusions = fields.get('confusions') or []
    if intuition or confusions:  # an empty section is left out, as in hand-kept vaults
        lines.append(f'## {h["intuition"]}')
        if intuition:
            lines.append(f'- {h["intuition_label"]}{_one_line(intuition)}')
        lines += [f'- {h["contrast_label"]}{_one_line(c)}' for c in confusions]
        lines.append('')
    lines += render_related(relations, language=language)
    lines.append(f'## {h["flashcards"]}')
    lines += [f'{_one_line(c["q"])}::{_one_line(c["a"])}' for c in fields['flashcards']]
    text = '\n'.join(lines) + '\n'
    return normalize_markdown(text)[0]


def _related_lines(relations: list[Relation], language: str) -> tuple[list[str], list[str]]:
    """(core lines, extension lines) for ``relations``, one per (type, target)."""
    core, extension, seen = [], [], set()
    for rel in relations:
        key = (rel.type, rel.target)
        if key in seen or not rel.target_title:
            continue
        seen.add(key)
        note = _one_line(rel.note) if rel.note else ''
        suffix = (f'（{note}）' if language == 'zh' else f' ({note})') if note else ''
        if rel.tier == 'extension':
            extension.append(f'- [[{rel.target_title}]] — {rel.type}{suffix}')
        else:
            core.append(f'- {rel.type}:: [[{rel.target_title}]]{suffix}')
    return core, extension


def _details(extension: list[str], language: str) -> list[str]:
    summary = HEADINGS[language]['extension'].format(n=len(extension))
    return [f'<details><summary>{summary}</summary>', '', *extension, '', '</details>']


def render_related(relations: list[Relation], *, language: str = 'en') -> list[str]:
    """The related section; none when there are no relations (an empty section is left out)."""
    core, extension = _related_lines(relations, language)
    if not core and not extension:
        return []
    lines = [f'## {HEADINGS[language]["related"]}', *core]
    if extension:
        lines += [''] + _details(extension, language)
    lines.append('')
    return lines


def add_related(body: str, relations: list[Relation], *, language: str = 'en',
                all_relations: list[Relation] | None = None) -> str:
    """``body`` with ``relations`` added to its related section; every byte outside that section stays.

    New lines have the renderer's form. Lines already in the section, in any
    form, are kept as they are, and a relation whose ``[[title]]`` and type are
    already on one line there is not added again. A section that only says
    there is nothing yet, or a missing one (inserted before the review-card
    section, or at the end), is rendered from ``all_relations`` (the card's
    whole relation list) when given.
    """
    lines = body.split('\n')
    related = {f'## {h["related"]}': name for name, h in HEADINGS.items()}
    start = next((i for i, line in enumerate(lines) if line.rstrip() in related), None)
    # the card's own headings decide the language of what is added
    known = {f'## {text}': name for name, h in HEADINGS.items() for key, text in h.items()
             if key in {'essence', 'propositions', 'intuition', 'related', 'flashcards'}}
    language = next((known[line.rstrip()] for line in lines if line.rstrip() in known), language)
    end = len(lines) if start is None else next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith('## ')), len(lines))
    section = [] if start is None else lines[start + 1:end]
    present = [line for line in section if line.strip()]
    fresh = [r for r in relations if r.target_title and not any(
        f'[[{r.target_title}]]' in line and r.type in line for line in present)]
    core, extension = _related_lines(fresh, language)
    if not core and not extension:
        return body
    core = normalize_markdown('\n'.join(core))[0].split('\n') if core else []
    if start is None or all(x.strip() in {h['none'] for h in HEADINGS.values()} for x in present):
        if all_relations is not None:
            core, extension = _related_lines(list(all_relations) + list(relations), language)
            core = normalize_markdown('\n'.join(core))[0].split('\n') if core else []
        block = [f'## {HEADINGS[language]["related"]}', *core]
        if extension:
            block += [''] + _details(extension, language)
        block.append('')
        if start is None:
            flash = {f'## {h["flashcards"]}' for h in HEADINGS.values()}
            at = next((i for i, line in enumerate(lines) if line.rstrip() in flash), None)
            if at is None:
                gap = '' if not body or body.endswith('\n\n') else ('\n' if body.endswith('\n') else '\n\n')
                return body + gap + '\n'.join(block)
            return '\n'.join(lines[:at] + block + lines[at:])
        return '\n'.join(lines[:start] + block + lines[end:])
    opened = next((i for i, line in enumerate(section) if line.startswith('<details>')), None)
    closed = next((i for i, line in enumerate(section) if line.strip() == '</details>'), None)
    if extension and opened is not None and closed is not None:
        at = closed
        while at > opened and not section[at - 1].strip():
            at -= 1
        section[at:at] = extension
        count = sum(1 for line in section[opened:closed + len(extension)] if line.startswith('- '))
        summary = re.sub(r'\d+', str(count), section[opened], count=1)
        section[opened] = summary
        extension = []
    limit = opened if opened is not None else len(section)
    last = max((i for i in range(limit) if section[i].strip()), default=-1)
    section[last + 1:last + 1] = core
    if extension:
        last = max((i for i in range(len(section)) if section[i].strip()), default=-1)
        section[last + 1:last + 1] = [''] + _details(extension, language)
    return '\n'.join(lines[:start + 1] + section + lines[end:])


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

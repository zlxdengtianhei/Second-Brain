"""Program-rendered cards: frontmatter contract, sections, related lines, diagram slot."""
from __future__ import annotations

from datetime import date

import pytest

from secondbrain import cards
from secondbrain.moc import build_mocs
from secondbrain.vault import parse_note, read_snapshot
from tests.helpers import MOC, build_vault

FIELDS = {'essence': 'One  sentence\nonly.', 'propositions': ['First.', 'Second.'],
          'intuition': 'Think of a ladder.', 'confusions': ['Not a stack.'],
          'flashcards': [{'q': 'Q1?', 'a': 'A1.'}, {'q': 'Q2?', 'a': 'A2.'}]}


def rel(type_, target, title, note=None, tier=None):
    return cards.Relation(type_, target, title, note, tier)


def test_related_section_uses_inline_fields_without_ids_or_duplicates():
    lines = cards.render_related([
        rel('depends_on', 'kc_a', 'Card A', 'needs A first'),
        rel('depends_on', 'kc_a', 'Card A', 'same pair again'),
        rel('similar_to', 'kc_a', 'Card A'),
        rel('part_of', 'kc_b', 'Card B', 'loose', 'extension'),
        rel('depends_on', 'kc_c', ''),  # unknown title: never fall back to the id
    ], language='zh')
    text = '\n'.join(lines)
    assert lines[1:3] == ['- depends_on:: [[Card A]]（needs A first）', '- similar_to:: [[Card A]]']
    assert '<details><summary>拓展：可能存在关系（1 条）</summary>' in text
    assert '- [[Card B]] — part_of（loose）' in text
    assert 'kc_' not in text and text.count('depends_on::') == 1


def test_related_section_when_empty_or_only_extension():
    assert cards.render_related([], language='en') == []  # an empty section is left out
    only = cards.render_related([rel('part_of', 'kc_b', 'Card B', None, 'extension')], language='en')
    assert only[1] == '' and '- [[Card B]] — part_of' in only


@pytest.mark.parametrize('language', ['en', 'zh'])
def test_body_sections_in_order_and_diagram_slot(language):
    h = cards.HEADINGS[language]
    body = cards.render_body(FIELDS, [rel('depends_on', 'kc_a', 'Card A')], language=language)
    order = [h['essence'], h['propositions'], h['intuition'], h['related'], h['flashcards']]
    positions = [body.index(f'## {name}\n') for name in order]
    assert positions == sorted(positions) and f'## {h["diagram"]}' not in body
    assert 'One  sentence only.' in body and '- P2. Second.' in body  # a line break joins; spaces stay
    assert body.rstrip().endswith('Q2?::A2.')
    with_diagram = cards.render_body(FIELDS, [], language=language,
                                     diagram='```mermaid\nflowchart LR\n  A --> B\n```\n')
    block = f'## {h["diagram"]}\n```mermaid\nflowchart LR\n  A --> B\n```\n'
    assert block in with_diagram
    assert with_diagram.index(block) > with_diagram.index(f'## {h["propositions"]}')
    assert with_diagram.index(block) < with_diagram.index(f'## {h["intuition"]}')


def test_runs_of_spaces_in_card_text_survive_rendering():
    """Drill 1 card 49 and drill 2 card 39: the source's `3  10 -2` came out as `3 10 -2`."""
    fields = {**FIELDS, 'essence': 'split() reads `3  10 -2` as three numbers.',
              'propositions': ['Input `3  10 -2`\n  sums to 11.', "split(' ') keeps an empty item for `a  b`."],
              'intuition': None, 'confusions': [],
              'flashcards': [{'q': 'What does split() make of `3  10 -2`?', 'a': "['3', '10', '-2']"},
                             {'q': "And split(' ')?", 'a': "['3', '', '10', '-2']"}]}
    body = cards.render_body(fields, [rel('depends_on', 'kc_a', 'Card A', 'note  with  spaces')], language='en')
    assert 'split() reads `3  10 -2` as three numbers.' in body
    assert '- P1. Input `3  10 -2` sums to 11.' in body and "empty item for `a  b`." in body
    assert 'What does split() make of `3  10 -2`?::' in body and '（' not in body
    assert '(note  with  spaces)' in body


def test_rendered_card_parses_back_with_the_contract_fields():
    meta = {'id': 'kc_x', 'title': 'Card X', 'kind': 'concept', 'domain': 'd', 'pillar': 'p', 'topic': 't',
            'tags': ['review', 'flashcards/d'], 'sources': [{'path': 's.md', 'sha256': 'ab', 'locator': 'q'}],
            'relations': [{'type': 'depends_on', 'target': 'kc_y'}], 'status': 'confirmed',
            'created': date(2026, 10, 13), 'aliases': ['X card']}
    raw = cards.render_card(meta, FIELDS, [rel('depends_on', 'kc_y', 'Card Y')], language='en')
    fm, body = parse_note(raw)
    assert list(fm)[:6] == ['id', 'title', 'kind', 'domain', 'pillar', 'topic']
    assert fm['created'] == date(2026, 10, 13) and fm['relations'] == meta['relations']
    assert '- depends_on:: [[Card Y]]' in body and 'kc_y' not in body


@pytest.mark.parametrize('title, ok', [
    ('Plain title', True), ('中文标题（带括号）', True), ('', False), (' padded', False), ('a/b', False),
    ('a:b', False), ('[[x]]', False), ('a#b', False), ('x' * 121, False), ('.hidden', False), ('two\nlines', False),
])
def test_title_rules(title, ok):
    assert (cards.title_problem(title) is None) is ok


def test_moc_domain_and_pillar_pages_carry_breadcrumbs_field(tmp_path):
    root = build_vault(tmp_path)
    projection = build_mocs(read_snapshot(root))
    pages = {p: b.decode('utf-8') for p, b in projection.files.items() if p.endswith('.md')}
    assert f'20_MOCs/{MOC}.md' in pages and any(p.startswith(f'20_MOCs/{MOC}/') for p in pages)
    for path, text in pages.items():
        assert text.startswith('---\nBC-list-note-field: down\n---\n# '), path


@pytest.mark.parametrize('language', ['en', 'zh'])
def test_empty_intuition_and_related_sections_are_left_out(language):
    h = cards.HEADINGS[language]
    bare = {**FIELDS, 'intuition': None, 'confusions': []}
    body = cards.render_body(bare, [], language=language)
    assert f'## {h["intuition"]}' not in body and f'## {h["related"]}' not in body and h['none'] not in body
    assert f'## {h["propositions"]}' in body and f'## {h["flashcards"]}' in body
    only_contrast = cards.render_body({**bare, 'confusions': ['Not a stack.']}, [], language=language)
    assert f'## {h["intuition"]}\n- {h["contrast_label"]}Not a stack.\n' in only_contrast


OLD_CARD = (  # a hand-kept card: other related-line forms, a fold of weak links, text after the section
    '## 本质\nA sentence.\n\n## 相关\n'
    '- [[Card A]] — depends_on（old note）\n- kc_raw — similar_to（kept as written）\n\n'
    '<details><summary>拓展：可能存在关系（1 条）</summary>\n\n- [[Card W]] — part_of\n\n</details>\n\n'
    '## 复习卡\nQ?::A.\n')


def outside(body, heading):
    start = body.index(heading)
    end = body.index('\n## ', start + 1)
    return body[:start], body[end:]


def test_add_related_changes_only_the_related_section():
    new = cards.add_related(OLD_CARD, [rel('tool_for', 'kc_n', 'New card'),
                                       rel('similar_to', 'kc_w', 'Weak card', None, 'extension'),
                                       rel('depends_on', 'kc_a', 'Card A')], language='en')
    assert outside(new, '## 相关') == outside(OLD_CARD, '## 相关')
    section = new[new.index('## 相关'):new.index('## 复习卡')]
    assert section == ('## 相关\n'
                       '- [[Card A]] — depends_on（old note）\n- kc_raw — similar_to（kept as written）\n'
                       '- tool_for:: [[New card]]\n\n'
                       '<details><summary>拓展：可能存在关系（2 条）</summary>\n\n- [[Card W]] — part_of\n'
                       '- [[Weak card]] — similar_to\n\n</details>\n\n')  # Card A depends_on was there already
    assert cards.add_related(new, [rel('tool_for', 'kc_n', 'New card')]) == new


@pytest.mark.parametrize('body', [
    '## 本质\nA sentence.\n\n## 相关\n暂无。\n\n## 复习卡\nQ?::A.\n',
    '## 本质\nA sentence.\n\n## 复习卡\nQ?::A.\n',
])
def test_add_related_fills_an_empty_or_missing_section(body):
    new = cards.add_related(body, [rel('used_by', 'kc_n', 'New card')], language='zh')
    assert new == '## 本质\nA sentence.\n\n## 相关\n- used_by:: [[New card]]\n\n## 复习卡\nQ?::A.\n'

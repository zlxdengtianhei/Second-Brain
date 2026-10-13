"""Appending relations keeps the existing frontmatter bytes."""
from __future__ import annotations

import difflib

import pytest

from secondbrain.vault import append_list_items, parse_note
from tests.ingest_helpers import SOURCE_NAME, build_ingest_vault
from tests.test_ingest import run_default

INDENTED = (b'---\nid: a\nrelations:\n  - type: depends_on\n    target: kc_x\n  # kept comment\n'
            b'  - type: similar_to\n    target: kc_y\n    tier: extension\nstatus: confirmed\n---\nbody\n')


def added_only(old: bytes, new: bytes) -> list[str]:
    diff = [d for d in difflib.ndiff(old.decode().splitlines(), new.decode().splitlines()) if d[:1] in '+-']
    assert all(d.startswith('+') for d in diff), diff
    return [d[2:] for d in diff]


FLUSH = INDENTED.replace(b'  # kept comment\n', b'').replace(b'\n  ', b'\n')


@pytest.mark.parametrize('raw, indent', [(INDENTED, '  '), (FLUSH, '')])
def test_items_are_appended_with_the_existing_indentation(raw, indent):
    out = append_list_items(raw, 'relations', [{'type': 'part_of', 'target': 'kc: odd', 'tier': 'extension'}])
    assert added_only(raw, out) == [f'{indent}- type: part_of', f"{indent}  target: 'kc: odd'",
                                    f'{indent}  tier: extension']
    fm, body = parse_note(out)
    assert fm['relations'][-1] == {'type': 'part_of', 'target': 'kc: odd', 'tier': 'extension'}
    assert body == 'body\n' and fm['status'] == 'confirmed'


@pytest.mark.parametrize('raw', [b'---\nid: a\n---\nb\n', b'---\nid: a\nrelations: []\n---\nb\n',
                                 b'---\nid: a\nrelations: [{type: x, target: y}]\n---\nb\n'])
def test_missing_empty_or_inline_lists_fall_back_to_a_rewrite(raw):
    out = append_list_items(raw, 'relations', [{'type': 'z', 'target': 'w'}])
    assert parse_note(out)[0]['relations'][-1] == {'type': 'z', 'target': 'w'}


def test_an_inverse_edge_only_adds_lines_to_an_existing_card(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    path = root / '10_Knowledge/attention/card_exist.md'
    seeded = path.read_bytes().replace(b'topic: Overview\n',
                                       b'topic: Overview\nrelations:\n  - type: part_of\n    target: card_pebble\n')
    path.write_bytes(seeded)
    _, result, _ = run_default(root, __import__('secondbrain.layout', fromlist=['DEFAULT']).DEFAULT)
    assert result['status'] == 'applied'
    lines = added_only(seeded, path.read_bytes())
    head = [line for line in lines if line.startswith('  ')]
    assert lines[:len(head)] == head and head[0] == '  - type: similar_to'
    # the card had no related section: it is rendered from the whole relation list, in the card's language
    assert lines[len(head):] == ['', '## 相关', '- part_of:: [[Pebble sizes]]', '- similar_to:: [[Lantern queue]]', '',
                                 '<details><summary>拓展：可能存在关系（1 条）</summary>', '',
                                 '- [[Lit marker memory cost]] — part_of', '', '</details>']
    assert (root / '00_Inbox').is_dir() and not (root / '00_Inbox' / SOURCE_NAME).exists()

from __future__ import annotations

from dataclasses import replace
from datetime import date
import re

import pytest

from secondbrain import config
from secondbrain.projections import generate
from tests.helpers import ENTRY_DOCS_LAYOUT, build_vault

DAY = date(2026, 9, 7)
NAMES = ('kb-stats', 'secondbrain-entry', 'kb-dirs')


def _block(name, body='old'):
    return f'<!-- GENERATED:BEGIN {name} -->{body}<!-- GENERATED:END {name} -->'


def _docs(root, guide, overview=None):
    (root / '使用指南.md').write_bytes(guide)
    (root / '系统总览.md').write_bytes(overview if overview is not None else guide)


def _mask_controlled(raw):
    text = raw.decode('utf-8')
    for name in NAMES:
        pattern = (rf'<!-- GENERATED:BEGIN {re.escape(name)} -->.*?'
                   rf'<!-- GENERATED:END {re.escape(name)} -->')
        text = re.sub(pattern, f'<MASK:{name}>', text, flags=re.S)
    return text.encode('utf-8')


def test_cross_name_nesting_and_bad_end_issue_without_writes(tmp_path):
    root = build_vault(tmp_path, lay=ENTRY_DOCS_LAYOUT)
    good = f'head\n{_block("kb-stats", _block("secondbrain-entry"))}\n{_block("kb-dirs")}\n'.encode()
    bad_end = f'head\n{_block("kb-stats")}\n<!-- GENERATED:END secondbrain-entry -->\n{_block("kb-dirs")}\n'.encode()
    _docs(root, good, bad_end)
    before = {name: (root / name).read_bytes() for name in ('使用指南.md', '系统总览.md')}

    result = generate(root, job_id='marker-cross-name', apply=True, today=DAY)

    assert result['issues']
    assert all((root / name).read_bytes() == raw for name, raw in before.items())


def test_duplicate_name_and_isolated_end_are_rejected(tmp_path):
    root = build_vault(tmp_path, lay=ENTRY_DOCS_LAYOUT)
    guide = (f'head\n{_block("kb-stats")}\n{_block("kb-stats")}\n'
             f'{_block("kb-dirs")}\n<!-- GENERATED:END secondbrain-entry -->\n').encode()
    _docs(root, guide)
    before = (root / '使用指南.md').read_bytes()

    result = generate(root, job_id='marker-duplicates', apply=True, today=DAY)

    assert result['issues']
    assert (root / '使用指南.md').read_bytes() == before


def test_existing_entry_preserves_all_bytes_after_each_controlled_end(tmp_path):
    root = build_vault(tmp_path, lay=ENTRY_DOCS_LAYOUT)
    body = ('prefix\r\n' + _block('kb-stats', 'stats') + '\r\n'
            + _block('secondbrain-entry', 'entry') + '\r\n'
            + _block('kb-dirs', 'dirs') + '\r\n\r\nTAIL\r\n').encode()
    _docs(root, body)
    before = {name: (root / name).read_bytes() for name in ('使用指南.md', '系统总览.md')}

    result = generate(root, job_id='marker-byte-preservation', apply=True, today=DAY)

    assert result['status'] == 'applied'
    for name, raw in before.items():
        assert _mask_controlled((root / name).read_bytes()) == _mask_controlled(raw)
        assert (root / name).read_bytes().endswith(b'\r\n\r\nTAIL\r\n')


def test_zero_entry_migration_does_not_accumulate_newlines(tmp_path):
    root = build_vault(tmp_path, lay=ENTRY_DOCS_LAYOUT)
    text = f'head\n{_block("kb-stats")}\n{_block("kb-dirs")}\nTAIL\n'.encode()
    _docs(root, text)

    first = generate(root, job_id='marker-migrate-1', apply=True, today=DAY)
    assert first['status'] == 'applied'
    after_first = {name: (root / name).read_bytes() for name in ('使用指南.md', '系统总览.md')}

    second = generate(root, job_id='marker-migrate-2', apply=True, today=DAY)
    assert second['status'] == 'applied'
    for name, raw in after_first.items():
        assert (root / name).read_bytes() == raw


@pytest.mark.parametrize('language, stats, entry', [('zh', '当前库存', '## 当前操作入口'),
                                                    ('en', 'Counted on', '## Commands')])
def test_three_independent_blocks_update_normally(tmp_path, language, stats, entry):
    config.activate(replace(config.Settings(), card_language=language))
    root = build_vault(tmp_path, lay=ENTRY_DOCS_LAYOUT)
    text = f'head\n{_block("kb-stats")}\n{_block("secondbrain-entry")}\n{_block("kb-dirs")}\nTAIL\n'.encode()
    _docs(root, text)

    result = generate(root, job_id='marker-normal', apply=True, today=DAY)

    assert result['status'] == 'applied'
    assert stats.encode() in (root / '使用指南.md').read_bytes()
    assert entry.encode() in (root / '使用指南.md').read_bytes()

from __future__ import annotations

import json
from datetime import date

import pytest

from secondbrain import jobs, projections as p, store
from secondbrain.cli import main
from secondbrain.projections import generate
from secondbrain.vault import read_snapshot
from tests.helpers import build_vault

DAY = date(2026, 9, 7)
STATS = '<!-- GENERATED:BEGIN kb-stats -->old stats<!-- GENERATED:END kb-stats -->'
ENTRY = '<!-- GENERATED:BEGIN secondbrain-entry -->old entry<!-- GENERATED:END secondbrain-entry -->'
DIRS = '<!-- GENERATED:BEGIN kb-dirs -->old dirs<!-- GENERATED:END kb-dirs -->'


def docs(root, suffix=''):
    text = f'head\n{STATS}\n{ENTRY}\n{DIRS}\nuser prose {suffix}\n'
    for name in ('使用指南.md', '系统总览.md'):
        (root / name).write_text(text, encoding='utf-8')


def test_f1_snapshot_card_reread_cannot_overwrite_frozen_hash(tmp_path, monkeypatch):
    root = build_vault(tmp_path)
    docs(root)
    card = root / '10_Knowledge/attention/card_exist.md'
    before = card.read_bytes()
    changed = before.replace(b'pillar: ', b'pillar: changed-')
    assert changed != before
    real = p.read_snapshot

    def race(*args, **kwargs):
        snapshot = real(*args, **kwargs)
        card.write_bytes(changed)
        return snapshot

    monkeypatch.setattr(p, 'read_snapshot', race)
    with pytest.raises(store.Conflict, match='准备后变化'):
        generate(root, job_id='f1', apply=True, today=DAY)
    assert card.read_bytes() == changed


def test_f2_only_controlled_blocks_change(tmp_path):
    root = build_vault(tmp_path)
    prose = '用户手写：`kb today`、`kb stats`。它会生成并直接在 Obsidian 里打开。'
    docs(root, prose)
    before = {name: (root / name).read_bytes() for name in ('使用指南.md', '系统总览.md')}
    result = generate(root, job_id='f2', apply=True, today=DAY)
    assert result['status'] == 'applied'
    for name, raw in before.items():
        after = (root / name).read_bytes()
        assert prose.encode() in after
        assert after != raw


def test_f3_bad_markers_block_all_document_writes(tmp_path):
    root = build_vault(tmp_path)
    docs(root)
    bad = root / '使用指南.md'
    bad.write_text(bad.read_text() + '<!-- GENERATED:BEGIN secondbrain-entry -->orphan\n', encoding='utf-8')
    before = {name: (root / name).read_bytes() for name in ('使用指南.md', '系统总览.md')}
    result = generate(root, job_id='f3', apply=True, today=DAY)
    assert any(i['code'] == 'missing_or_duplicate_entry_markers' for i in result['issues'])
    assert all((root / name).read_bytes() == raw for name, raw in before.items())


def test_f3_duplicate_entry_is_rejected(tmp_path):
    root = build_vault(tmp_path)
    docs(root)
    guide = root / '使用指南.md'
    guide.write_text(guide.read_text() + ENTRY + '\n', encoding='utf-8')
    before = guide.read_bytes()
    result = generate(root, job_id='f3-dup-entry', apply=True, today=DAY)
    assert any(i['code'] == 'missing_or_duplicate_entry_markers' for i in result['issues'])
    assert guide.read_bytes() == before


def test_f3_duplicate_dirs_and_isolated_begin_are_rejected(tmp_path):
    root = build_vault(tmp_path)
    docs(root)
    guide = root / '使用指南.md'
    guide.write_text(guide.read_text() + DIRS + '\n', encoding='utf-8')
    before = guide.read_bytes()
    result = generate(root, job_id='f3-dup-dirs', apply=True, today=DAY)
    assert any(i['code'] == 'missing_or_duplicate_dirs_markers' for i in result['issues'])
    assert guide.read_bytes() == before


def test_f4_prepared_cli_rebuilds_same_argv_and_rejects_changes(tmp_path, monkeypatch):
    root = build_vault(tmp_path)
    docs(root)
    argv = ['--vault', str(root), 'daily', '--date', str(DAY), '--apply', '--job', 'f4']
    original = p._projection_files
    monkeypatch.setattr(p, '_projection_files', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('before intent')))
    with pytest.raises(RuntimeError):
        main(argv)
    packet_before = store.read_state(root, jobs.location('f4', 'input.json'))
    monkeypatch.setattr(p, '_projection_files', original)
    assert main(argv) == 0
    assert jobs.read_input(root, 'f4')['date'] == str(DAY)
    assert store.read_state(root, jobs.location('f4', 'input.json')) == packet_before

    for index, kwargs in enumerate(({'today': date(2026, 9, 8)},
                                   {'reviews': False},
                                   {'generated_at': 'changed'})):
        other = tmp_path / f'changed-{index}'
        other.mkdir()
        build_vault(other)
        docs(other)
        monkeypatch.setattr(p, '_projection_files', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('before intent')))
        with pytest.raises(RuntimeError):
            generate(other, job_id='prepared', apply=True, today=DAY)
        packet = store.read_state(other, jobs.location('prepared', 'input.json'))
        monkeypatch.setattr(p, '_projection_files', original)
        with pytest.raises(store.Conflict):
            generate(other, job_id='prepared', apply=True, **kwargs)
        assert store.read_state(other, jobs.location('prepared', 'input.json')) == packet


def test_f5_review_receives_exact_final_target_set(tmp_path, monkeypatch):
    root = build_vault(tmp_path)
    docs(root)
    seen = []
    real = p.review_projection

    def spy(*args, **kwargs):
        seen.append(set(kwargs['extra_targets']))
        return real(*args, **kwargs)

    monkeypatch.setattr(p, 'review_projection', spy)
    result = generate(root, job_id='f5', apply=False, today=DAY, refresh_mocs=True)
    assert seen
    expected_review = {
        f'50_Reviews/今日复习-{DAY}.md',
        f'_data/projections/review_state_{DAY}.json',
        '_data/projections/kb_review.json',
    }
    assert seen[0] == set(result['paths'])
    assert expected_review <= seen[0]
    assert '使用指南.md' in seen[0] and '系统总览.md' in seen[0]

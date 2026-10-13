"""Folder names are configuration: the same flows run on a non-default layout."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date

import pytest

from secondbrain import classification, layout, runner, store
from secondbrain.executors import FakeExecutor
from secondbrain.projections import generate
from secondbrain.service import refresh
from secondbrain.vault import read_snapshot
from tests.helpers import (CUSTOM_LAYOUT, DOMAIN, EXISTING, MOC, PILLAR, TOPIC, build_vault)

L = CUSTOM_LAYOUT
DEFAULT_DIRS = ['10_Knowledge', '20_MOCs', '40_Sources', '00_Inbox', '50_Reviews', '_data']


def unclassified(root):
    path = root / L.card_path(DOMAIN, EXISTING + '.md')
    path.write_text(path.read_text(encoding='utf-8').replace(f'pillar: {PILLAR}', 'pillar: null')
                    .replace(f'topic: {TOPIC}', 'topic: null'), encoding='utf-8')
    return path


def files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file())


def test_layout_validation():
    with pytest.raises(ValueError, match='overlap'):
        layout.VaultLayout(knowledge='notes', mocs='notes/maps')
    with pytest.raises(ValueError, match='top-level'):
        layout.VaultLayout(home='sub/Home.md')
    assert L.is_card('Cards/attention/x.md') and not L.is_card('10_Knowledge/attention/x.md')
    assert L.is_moc_page('Maps/m.md') and L.is_moc_page('Maps/m/01-p--01.md')
    assert not L.is_moc_page('Maps/taxonomy/m.yaml') and L.is_taxonomy_file('Maps/taxonomy/m.yaml')
    assert L.source_path('ab' * 32, 'n.md') == 'Library/sources/' + 'ab' * 8 + '/n.md'


def test_classify_and_refresh_on_a_custom_layout(tmp_path):
    root = build_vault(tmp_path, lay=L)
    path = unclassified(root)
    classification.prepare(root, ids=[EXISTING], job_id='classify')
    row = {'id': EXISTING, 'disposition': 'assigned', 'pillar': PILLAR, 'topic': TOPIC,
           'evidence_quote': '已有卡正文，保持逐字不动。', 'reason': 'fits'}
    producer = FakeExecutor('gpt-test', outputs={'producer': {'items': [row]}})
    reviewer = FakeExecutor('claude-test', outputs={'reviewer': {'items': [
        {'id': EXISTING, 'decision': 'accept', 'reason': 'checked'}]}})
    result = runner.run_job(root, 'classify', producer=producer, reviewer=reviewer)
    assert result['status'] == 'applied'
    assert f'pillar: {PILLAR}' in path.read_text(encoding='utf-8')
    written = files(root)
    assert f'Maps/{MOC}.md' in written
    assert not any(f.split('/')[0] in DEFAULT_DIRS for f in written)
    assert 'Start.md' in written and 'HOME.md' not in written
    snap = read_snapshot(root)
    assert snap.issues == [] and snap.cards[EXISTING].path == f'Cards/{DOMAIN}/{EXISTING}.md'
    assert refresh(root, job_id='preview')['changed_files'] == 0


def test_daily_projection_writes_only_configured_folders(tmp_path):
    root = build_vault(tmp_path, lay=L)
    result = generate(root, job_id='daily', apply=True, today=date(2026, 10, 13))
    assert result['status'] == 'applied'
    paths = set(result['applied_paths'])
    assert 'Start.md' in paths and 'Daily/今日复习-2026-10-13.md' in paths
    assert 'meta/projections/review_state_2026-10-13.json' in paths
    assert not any(p.split('/')[0] in DEFAULT_DIRS or p == 'HOME.md' for p in paths)
    assert f'[[Daily/今日复习-2026-10-13|' in (root / 'Start.md').read_text(encoding='utf-8')


def test_store_follows_the_layout(tmp_path):
    root = build_vault(tmp_path, lay=L)
    with pytest.raises(ValueError, match='越界'):
        store.apply_changes(root, 'wrong', [store.Change('10_Knowledge/attention/x.md', None, b'x')])
    raw = b'# note\n'
    (root / 'Inbox').mkdir()
    (root / 'Inbox/n.md').write_bytes(raw)
    sha = store.digest(raw)
    with pytest.raises(ValueError, match='Inbox'):
        store.apply_changes(root, 'lose', [store.Change('Inbox/n.md', sha, None)])
    with pytest.raises(ValueError, match='Inbox'):
        store.apply_changes(root, 'alter', [store.Change('Inbox/n.md', sha, None),
                                            store.Change(L.source_path(sha, 'n.md'), None, b'other')])
    done = store.apply_changes(root, 'archive', [store.Change(L.source_path(sha, 'n.md'), None, raw),
                                                 store.Change('Inbox/n.md', sha, None)])
    assert done['status'] == 'applied' and not (root / 'Inbox/n.md').exists()
    assert (root / L.source_path(sha, 'n.md')).read_bytes() == raw
    assert store.recover(root, 'archive', 'rollback')['status'] == 'rolled_back'
    assert (root / 'Inbox/n.md').read_bytes() == raw and not (root / L.source_path(sha, 'n.md')).exists()


def test_cli_reads_the_layout_from_the_config_file(tmp_path):
    root = build_vault(tmp_path / 'vault', lay=L)
    layout.unregister_all()  # the CLI must learn the layout from the file alone
    cfg = tmp_path / 'sb.toml'
    cfg.write_text(f'''vault = "{root}"
[layout]
knowledge = "Cards"
mocs = "Maps"
taxonomy = "Maps/taxonomy"
sources = "Library/sources"
inbox = "Inbox"
reviews = "Daily"
data = "meta"
home = "Start.md"
entry_docs = []
extra_dirs = []
''', encoding='utf-8')
    env = {'PATH': '/usr/bin:/bin', 'SECONDBRAIN_STATE_DIR': str(tmp_path / 'state'),
           'SECONDBRAIN_CONFIG': str(tmp_path / 'unused.toml')}
    status = subprocess.run([sys.executable, '-m', 'secondbrain', '--config', str(cfg), 'status'],
                            capture_output=True, text=True, env=env)
    assert status.returncode == 0, status.stderr
    data = json.loads(status.stdout)
    assert data['cards'] == 1 and data['issues'] == []
    applied = subprocess.run([sys.executable, '-m', 'secondbrain', '--config', str(cfg), 'refresh', '--apply'],
                             capture_output=True, text=True, env=env)
    assert applied.returncode == 0, applied.stderr
    assert (root / f'Maps/{MOC}.md').is_file() and not (root / '20_MOCs').exists()

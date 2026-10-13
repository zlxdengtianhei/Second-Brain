from __future__ import annotations

import json
from datetime import date

from secondbrain import jobs
from secondbrain.cli import main
from secondbrain.projections import generate
from tests.helpers import build_vault


def test_daily_refresh_mocs_is_one_pure_preview_and_one_job(tmp_path):
    root = build_vault(tmp_path)
    before = {p.relative_to(root).as_posix(): p.read_bytes()
              for p in root.rglob('*') if p.is_file()}
    planned = generate(root, job_id='daily-preview', today=date(2026, 9, 7),
                       refresh_mocs=True)
    assert planned['status'] == 'planned'
    assert jobs.list_jobs(root) == []
    assert {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob('*') if p.is_file()} == before
    assert any(p.startswith('20_MOCs/') for p in planned['paths'])
    assert 'HOME.md' in planned['paths']


def test_daily_refresh_mocs_freezes_options_and_generated_at(tmp_path, capsys):
    root = build_vault(tmp_path)
    assert main(['--vault', str(root), 'daily', '--date', '2026-09-07',
                 '--refresh-mocs', '--apply', '--job', 'daily-mocs']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'applied'
    assert result['date'] == '2026-09-07'
    assert len([j for j in jobs.list_jobs(root) if j.get('job_id') == 'daily-mocs']) == 1
    packet = jobs.read_input(root, 'daily-mocs')
    assert packet['options']['refresh_mocs'] is True
    assert packet['generated_at']
    assert any(p.startswith('20_MOCs/') and p.count('/') == 2 for p in result['applied_paths']) or not any(p.startswith('20_MOCs/') for p in result['applied_paths'])
    site = json.loads((root / '_data/projections/kb_review.json').read_text())
    assert site['generated_at'] == packet['generated_at']

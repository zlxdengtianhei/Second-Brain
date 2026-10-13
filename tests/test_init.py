"""`sb init` leaves a vault that can ingest once its taxonomy files exist."""
from __future__ import annotations

import json

from secondbrain import cli
from tests.helpers import CATALOG, MOC, TAXONOMY


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip() else None), err


def test_init_writes_the_registry_and_folders_and_names_the_missing_taxonomy_files(tmp_path, capsys):
    root = tmp_path / 'vault'
    root.mkdir()
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps(CATALOG), encoding='utf-8')
    code, result, err = run(capsys, '--vault', str(root), 'init', '--catalog', str(catalog))
    assert code == 0, err
    assert result['created_folders'] == ['10_Knowledge', '20_MOCs/_pillars', '00_Inbox']
    assert result['taxonomy_files_missing'] == [f'20_MOCs/_pillars/{MOC}.yaml']
    assert json.loads((root / '_data/notes_registry.json').read_text(encoding='utf-8')) == {'notes': []}
    # before any taxonomy exists, an ingest stops before the model call and names the files to write
    (root / '00_Inbox/notes.md').write_text('# Notes\n\nAttention routes tokens.\n', encoding='utf-8')
    code, [refused], err = run(capsys, '--vault', str(root), 'ingest', str(root / '00_Inbox/notes.md'),
                               '--executor', 'host')
    assert code == 0 and refused['status'] == 'failed', err
    assert f'20_MOCs/_pillars/{MOC}.yaml' in refused['error'] and 'nothing was sent to a model' in refused['error']
    listed = run(capsys, '--vault', str(root), 'job', 'list')[1]
    assert [j['job_id'] for j in listed] == ['initialize-catalog']  # no ingest job was made
    (root / f'20_MOCs/_pillars/{MOC}.yaml').write_text(json.dumps(TAXONOMY), encoding='utf-8')
    code, status, _ = run(capsys, '--vault', str(root), 'status')
    assert code == 0 and status['issues'] == []
    code, [prepared], err = run(capsys, '--vault', str(root), 'ingest', str(root / '00_Inbox/notes.md'),
                                '--prepare-only')
    assert code == 0 and prepared['status'] == 'prepared', err
    code, again, _ = run(capsys, '--vault', str(root), 'init', '--catalog', str(catalog))
    assert again == {'status': 'unchanged', 'created_folders': [], 'taxonomy_files_missing': []}


def test_init_completes_a_vault_that_has_only_the_catalog(tmp_path, capsys):
    root = tmp_path / 'vault'
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps(CATALOG), encoding='utf-8')
    root.mkdir()
    assert run(capsys, '--vault', str(root), 'init', '--catalog', str(catalog))[0] == 0
    (root / '_data/notes_registry.json').unlink()
    code, result, err = run(capsys, '--vault', str(root), 'init', '--catalog', str(catalog))
    assert code == 0 and result['status'] == 'applied', err
    assert (root / '_data/notes_registry.json').is_file()

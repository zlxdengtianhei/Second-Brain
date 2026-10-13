"""The vault's language guards generated text: a missing setting follows the vault, a different one refuses."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from secondbrain import catalog as catalogs, cli, config, labels
from secondbrain.classification import writable_snapshot
from secondbrain.service import initialize, refresh
from secondbrain.vault import read_snapshot
from tests.helpers import CATALOG, MOC, build_vault

ROOT_PAGE = f'20_MOCs/{MOC}.md'


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file()}


def zh_vault(tmp_path):
    """A vault whose maps were generated in Chinese, then no configuration at all."""
    root = build_vault(tmp_path / 'vault')
    config.activate(replace(config.Settings(), card_language='zh', card_language_set=True))
    assert refresh(root, job_id='zh-maps', apply=True)['status'] == 'applied'
    config.activate(None)
    assert '## 分页索引（全部可达）' in (root / ROOT_PAGE).read_text(encoding='utf-8')
    return root


def run(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, json.loads(out) if out.strip() else None, err


def en_config(tmp_path, root):
    path = tmp_path / 'sb.toml'
    path.write_text(f'vault = "{root}"\n\n[cards]\nlanguage = "en"\n', encoding='utf-8')
    return path


def test_without_a_config_file_a_zh_vault_stays_zh(tmp_path, capsys):
    root = zh_vault(tmp_path)
    assert labels.vault_language(root) == 'zh' and config.current().card_language == 'en'
    before = (root / ROOT_PAGE).read_bytes()
    code, result, err = run(capsys, '--vault', root, 'refresh', '--apply')
    assert code == 0, err
    assert result['changed_files'] == 0 and (root / ROOT_PAGE).read_bytes() == before
    code, result, err = run(capsys, '--vault', root, 'docs', '--apply')
    assert code == 0, err
    assert '## 今日复习' in (root / 'HOME.md').read_text(encoding='utf-8')
    code, result, _ = run(capsys, '--vault', root, 'language')
    assert result['vault'] == 'zh' and result['detected'] == 'zh' and result['recorded'] is None
    assert result['configured'] is None and result['active'] == 'zh' and result['writes_allowed']


def test_library_callers_without_a_setting_follow_the_vault(tmp_path):
    root = zh_vault(tmp_path)
    before = (root / ROOT_PAGE).read_bytes()
    assert refresh(root, job_id='api-refresh', apply=True)['changed_files'] == 0
    assert (root / ROOT_PAGE).read_bytes() == before and labels.language() == 'zh'


def test_a_configured_language_that_differs_from_the_vault_refuses_every_write(tmp_path, capsys):
    root = zh_vault(tmp_path)
    cfg = en_config(tmp_path, root)
    before = tree(root)
    for argv in (['refresh', '--apply'], ['refresh'], ['docs', '--apply'], ['daily', '--apply']):
        code, _, err = run(capsys, '--config', cfg, *argv)
        assert code == 2, argv
        message = json.loads(err)['error']
        assert 'generated text is zh (found in the map pages)' in message
        assert str(cfg) in message and '`sb language --set en`' in message
    assert tree(root) == before
    config.activate(config.load(cfg))
    with pytest.raises(ValueError, match='nothing was written'):
        writable_snapshot(read_snapshot(root))
    code, result, _ = run(capsys, '--config', cfg, 'language')
    assert result['vault'] == 'zh' and result['configured'] == 'en' and not result['writes_allowed']


def test_sb_language_set_switches_the_vault_explicitly(tmp_path, capsys):
    root = zh_vault(tmp_path)
    cfg = en_config(tmp_path, root)
    code, result, err = run(capsys, '--config', cfg, 'language', '--set', 'en')
    assert code == 0, err
    assert result['status'] == 'applied' and result['previous'] == 'zh'
    assert json.loads((root / '_data/secondbrain/catalog.json').read_text())['language'] == 'en'
    assert labels.vault_language(root) == 'en'   # the record wins over the zh pages still on disk
    code, result, err = run(capsys, '--config', cfg, 'refresh', '--apply')
    assert code == 0, err
    assert '## Page index (every page)' in (root / ROOT_PAGE).read_text(encoding='utf-8')
    code, result, _ = run(capsys, '--vault', root, 'language', '--set', 'en')
    assert result['status'] == 'unchanged'


def test_init_records_the_language(tmp_path):
    fresh = tmp_path / 'fresh'
    fresh.mkdir()
    initialize(fresh, CATALOG)
    assert json.loads((fresh / '_data/secondbrain/catalog.json').read_text())['language'] == 'en'
    chosen = tmp_path / 'chosen'
    chosen.mkdir()
    config.activate(replace(config.Settings(), card_language='zh', card_language_set=True))
    initialize(chosen, CATALOG)
    assert labels.vault_language(chosen) == 'zh'


def test_init_on_a_vault_with_zh_maps_records_zh(tmp_path):
    root = zh_vault(tmp_path)
    (root / '_data/secondbrain/catalog.json').unlink()
    initialize(root, CATALOG)
    assert labels.recorded_language(json.loads((root / '_data/secondbrain/catalog.json').read_text())) == 'zh'


def test_an_empty_vault_has_no_language_and_accepts_either(tmp_path):
    root = build_vault(tmp_path / 'vault')
    assert labels.vault_language(root) is None
    config.activate(replace(config.Settings(), card_language='zh', card_language_set=True))
    writable_snapshot(read_snapshot(root))


def test_the_catalog_language_must_be_known():
    with pytest.raises(catalogs.CatalogError, match='language must be one of en/zh'):
        catalogs.validate_catalog({**CATALOG, 'language': 'fr'})

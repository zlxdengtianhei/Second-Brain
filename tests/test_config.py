"""TOML settings: defaults, validation, secrets only from env or command."""
from __future__ import annotations

import sys

import pytest

from secondbrain import config, layout


def write(tmp_path, text):
    path = tmp_path / 'config.toml'
    path.write_text(text, encoding='utf-8')
    return path


def test_missing_file_gives_defaults(tmp_path):
    settings = config.load(tmp_path / 'absent.toml')
    assert settings.vault is None and settings.layout == layout.DEFAULT
    assert settings.review.require_different_family is True
    assert settings.review.allow_same_family_in_host_mode is True
    assert settings.review.max_repair_rounds == 1
    assert settings.ingest.after_ingest == 'move' and settings.ingest.chunk_chars == 12000
    assert settings.taxonomy_extra_reviewers == 0 and settings.git_commit is False


def test_example_config_parses(tmp_path):
    settings = config.load(write(tmp_path, config.EXAMPLE))
    assert settings.producer.kind == 'openai' and settings.reviewer.kind == 'anthropic'
    assert settings.producer.api_key_env == 'SB_PRODUCER_API_KEY'
    assert settings.layout == layout.DEFAULT
    assert settings.card_language == 'en' and not settings.card_language_set  # follows the vault


def test_full_config_round_trip(tmp_path):
    settings = config.load(write(tmp_path, '''
vault = "~/notes"
state_dir = "/tmp/sb-state-example"
[layout]
knowledge = "Cards"
mocs = "Maps"
taxonomy = "Maps/taxonomy"
entry_docs = []
[executor]
default = "host"
[executor.producer]
kind = "anthropic"
base_url = "https://api.example.com"
model = "claude-x"
api_key_cmd = "pass show api/claude"
[review]
require_different_family = false
max_repair_rounds = 2
[model_families]
mylocal = "local"
[ingest]
after_ingest = "keep"
chunk_chars = 8000
[cards]
language = "zh"
[taxonomy]
extra_reviewers = 1
[git]
commit = true
'''))
    assert str(settings.vault).endswith('/notes') and settings.vault.is_absolute()
    assert settings.layout.knowledge == 'Cards' and settings.layout.entry_docs == ()
    assert settings.executor_default == 'host' and settings.producer.api_key_cmd == 'pass show api/claude'
    assert settings.reviewer is None
    assert settings.review.max_repair_rounds == 2 and not settings.review.require_different_family
    assert settings.review.model_families['mylocal'] == 'local'
    assert settings.review.model_families['claude'] == 'anthropic'
    assert settings.ingest.after_ingest == 'keep' and settings.card_language == 'zh' and settings.card_language_set
    assert settings.taxonomy_extra_reviewers == 1 and settings.git_commit is True


@pytest.mark.parametrize('text, message', [
    ('[executor.producer]\napi_key = "sk-secret"\n', 'api_key'),
    ('[review]\nmax_rounds = 3\n', 'Unknown'),
    ('colour = "blue"\n', 'Unknown'),
    ('[layout]\nknowledge = "/abs"\n', 'relative'),
    ('[layout]\nknowledge = "../up"\n', 'clean'),
    ('[layout]\nknowledge = "notes"\nmocs = "notes/maps"\n', 'overlap'),
    ('[ingest]\nafter_ingest = "delete"\n', 'after_ingest'),
    ('[executor.reviewer]\nkind = "gemini"\n', 'kind'),
    ('[cards]\nlanguage = "fr"\n', 'language'),
])
def test_invalid_config_is_rejected_with_a_reason(tmp_path, text, message):
    with pytest.raises(ValueError, match=message):
        config.load(write(tmp_path, text))


def test_api_key_comes_only_from_env_or_command(monkeypatch):
    endpoint = config.EndpointConfig(api_key_env='SB_TEST_KEY')
    monkeypatch.delenv('SB_TEST_KEY', raising=False)
    with pytest.raises(ValueError, match='SB_TEST_KEY'):
        endpoint.api_key()
    monkeypatch.setenv('SB_TEST_KEY', ' from-env \n')
    assert endpoint.api_key() == 'from-env'
    cmd = config.EndpointConfig(api_key_cmd=f'{sys.executable} -c "print(\'from-cmd\')"')
    assert cmd.api_key() == 'from-cmd'
    with pytest.raises(ValueError, match='api_key_cmd failed'):
        config.EndpointConfig(api_key_cmd=f'{sys.executable} -c "raise SystemExit(3)"').api_key()
    with pytest.raises(ValueError, match='neither'):
        config.EndpointConfig().api_key()
    assert 'from-env' not in repr(endpoint)


def test_configured_layout_applies_to_the_configured_vault(tmp_path, monkeypatch):
    vault = tmp_path / 'vault'
    vault.mkdir()
    path = write(tmp_path, f'vault = "{vault}"\n[layout]\nknowledge = "Cards"\n')
    monkeypatch.setenv('SECONDBRAIN_CONFIG', str(path))
    config.activate(None)
    assert layout.of(vault).knowledge == 'Cards'
    assert layout.of(tmp_path / 'other') == layout.DEFAULT


def test_state_dir_from_config_unless_env_overrides(tmp_path, monkeypatch):
    path = write(tmp_path, f'state_dir = "{tmp_path / "configured-state"}"\n')
    config.activate(config.load(path))
    monkeypatch.delenv('SECONDBRAIN_STATE_DIR')
    assert config.state_dir() == (tmp_path / 'configured-state')
    monkeypatch.setenv('SECONDBRAIN_STATE_DIR', str(tmp_path / 'env-state'))
    assert config.state_dir() == (tmp_path / 'env-state')

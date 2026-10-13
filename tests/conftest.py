"""Keep every test's state and configuration away from the real user setup."""
from __future__ import annotations

import pytest

from secondbrain import config, layout


@pytest.fixture(autouse=True)
def secondbrain_state_tmp(monkeypatch, tmp_path):
    state = tmp_path.parent / f"{tmp_path.name}-state"
    monkeypatch.setenv("SECONDBRAIN_STATE_DIR", str(state))
    monkeypatch.setenv("SECONDBRAIN_CONFIG", str(tmp_path.parent / f"{tmp_path.name}-no-config.toml"))
    monkeypatch.setenv("SECONDBRAIN_LOCK_WAIT", "2")
    monkeypatch.delenv("SECONDBRAIN_VAULT", raising=False)
    config.activate(None)
    layout.unregister_all()
    yield state
    config.activate(None)
    layout.unregister_all()

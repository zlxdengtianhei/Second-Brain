"""Keep every test's processing state out of the real state home."""
from __future__ import annotations

import pytest

from secondbrain import config


@pytest.fixture(autouse=True)
def secondbrain_state_tmp(monkeypatch, tmp_path):
    state = tmp_path.parent / f"{tmp_path.name}-state"
    monkeypatch.setenv("SECONDBRAIN_STATE_DIR", str(state))
    monkeypatch.setenv("SECONDBRAIN_LOCK_WAIT", "2")
    config.set_review_policy(None)
    yield state
    config.set_review_policy(None)

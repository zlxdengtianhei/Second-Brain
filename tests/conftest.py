"""Keep every test's state and configuration away from the real user setup, and stop a test that hangs."""
from __future__ import annotations

import os
import signal

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


#: Seconds a single test may run (SECONDBRAIN_TEST_TIMEOUT overrides; 0 turns it off). A loop that should stop
#: after a bounded number of rounds once ran for 19 minutes under a mutant instead of failing (V1).
TEST_TIMEOUT = int(os.environ.get('SECONDBRAIN_TEST_TIMEOUT', '300'))


@pytest.fixture(autouse=True)
def stop_a_hanging_test():
    if not TEST_TIMEOUT or not hasattr(signal, 'SIGALRM'):
        yield
        return

    def expire(signum, frame):
        pytest.fail(f'the test ran longer than {TEST_TIMEOUT} s (SECONDBRAIN_TEST_TIMEOUT)')
    previous = signal.signal(signal.SIGALRM, expire)
    signal.alarm(TEST_TIMEOUT)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)

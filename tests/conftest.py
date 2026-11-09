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
    monkeypatch.setenv("SECONDBRAIN_STRICT_MARKS", "1")  # a mark of the guard reached outside it fails the test
    monkeypatch.delenv("SECONDBRAIN_VAULT", raising=False)
    config.activate(None)
    layout.unregister_all()
    yield state
    config.activate(None)
    layout.unregister_all()


@pytest.fixture(autouse=True)
def program_fields_listed(monkeypatch):
    """Every field a registered check adds to an answer it keeps sits where the answer's own schema has a field, or
    is in ``jobs.PROGRAM_WRITTEN`` at a place the entry point takes such fields out (:mod:`tests.field_audit`, by
    path): a new field the program writes into an answer fails each test that makes it write it, until the field
    is listed (and so taken out of every model's answer)."""
    from secondbrain import jobs
    from tests.field_audit import unlisted
    found: list = []
    real = jobs._validator
    listed = {'': jobs.PROGRAM_WRITTEN_AT_TOP, '.cards[]': jobs.PROGRAM_WRITTEN_IN_ROWS,
              '.items[]': jobs.PROGRAM_WRITTEN_IN_ROWS}

    def validator(kind):
        check = real(kind)
        if check is None:
            return None

        def audited(packet, payload, **kw):
            kept = check(packet, payload, **kw)
            found.extend(unlisted(payload, kept, listed, kind, kw.get('role'), kw.get('chunk')))
            return kept
        return audited
    monkeypatch.setattr(jobs, '_validator', validator)
    yield
    assert not found, f'fields a check wrote into an answer that its schema does not describe and jobs.PROGRAM_WRITTEN ' \
                      f'does not list there: {sorted(set(found))}'


@pytest.fixture
def old_review_rules(monkeypatch, tmp_path):
    """A config file with the review rules the scripted flows of these tests were written for: one repair round
    and a full review in it (``max_repair_rounds = 1``, ``focused_review = false``; the defaults are now 2 rounds
    and a focused review, tested in test_review_rules.py)."""
    path = tmp_path.parent / f"{tmp_path.name}-old-rules.toml"
    path.write_text('[review]\nmax_repair_rounds = 1\nfocused_review = false\n', encoding='utf-8')
    monkeypatch.setenv("SECONDBRAIN_CONFIG", str(path))
    config.activate(None)
    return path


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

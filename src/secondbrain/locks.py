"""Per-job run lock: one controller drives a job at a time.

The lock is a non-blocking ``flock`` on ``jobs/<id>/run.lock`` in the vault's
external state root. It is re-entrant for the thread that already holds it, so
a controller may call into nested steps of the same job. Vault writes are
serialized separately by :func:`secondbrain.store.vault_lock`.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import stat
import threading
from pathlib import Path
from typing import Iterator

from .store import Conflict, _state_parent, job_name, state_root

_HELD: dict[tuple[str, str], tuple[int, int]] = {}
_GUARD = threading.Lock()


@contextlib.contextmanager
def job_lock(root: Path, job_id: str) -> Iterator[None]:
    key = (str(state_root(root)), job_name(job_id))
    me = threading.get_ident()
    with _GUARD:
        held = _HELD.get(key)
        if held is not None and held[0] == me:
            _HELD[key] = (me, held[1] + 1)
            reentrant = True
        else:
            reentrant = False
    if reentrant:
        try:
            yield
        finally:
            with _GUARD:
                owner, depth = _HELD[key]
                _HELD[key] = (owner, depth - 1)
        return
    with _state_parent(root, f'jobs/{job_name(job_id)}/run.lock', create=True) as (parent, name):
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise ValueError('Job lock is not a regular file')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Conflict(f'Job {job_id} is already being driven by another controller') from exc
            with _GUARD:
                _HELD[key] = (me, 1)
            try:
                yield
            finally:
                with _GUARD:
                    _HELD.pop(key, None)
        finally:
            os.close(fd)

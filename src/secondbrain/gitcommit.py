"""Optional S9: commit exactly the paths a transaction wrote (``[git] commit``)."""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise ValueError(f'git {args[0]} failed: {done.stderr.strip()[:500]}')
    return done.stdout.strip()


def commit_paths(root: Path, paths: list[str], message: str) -> dict:
    """Stage and commit only ``paths`` (deletions included); other changes stay untouched.

    A removed path that git never tracked (for example an inbox file that was
    not committed before it was archived) has nothing to commit and is left out.
    """
    root = Path(root)
    _git(root, 'rev-parse', '--is-inside-work-tree')
    tracked = set(filter(None, _git(root, 'ls-files', '-z', '--', *paths).split('\0'))) if paths else set()
    present = [p for p in paths if (root / p).exists() or p in tracked]
    if not present:
        return {'status': 'nothing_to_commit'}
    _git(root, 'add', '-A', '--', *present)
    _git(root, 'commit', '-q', '-m', message, '--only', '--', *present)
    return {'status': 'committed', 'commit': _git(root, 'rev-parse', 'HEAD'), 'paths': len(present)}

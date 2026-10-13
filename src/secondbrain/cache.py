"""Parse cache for vault snapshots.

Stores, per card file, ``(stat key, sha256, frontmatter, raw bytes, body
offset)`` in the vault's external state root (``cache/snapshot.pkl``), so a
warm snapshot only reads and parses files whose (size, mtime, ctime, inode)
changed. The cache never lives in the vault (vaults get synced) and is never
trusted for writes: transactions compare real file hashes before writing.

The file is unpickled with an allow-list of the types a YAML safe loader can
produce; anything else invalidates the cache instead of being constructed.
"""
from __future__ import annotations

import importlib
import io
import pickle
import sys

FORMAT = 1
CACHE_REL = 'cache/snapshot.pkl'

_ALLOWED = {
    ('datetime', 'date'), ('datetime', 'datetime'), ('datetime', 'time'),
    ('datetime', 'timedelta'), ('datetime', 'timezone'),
    ('builtins', 'set'), ('builtins', 'frozenset'),
}


class _Unpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) not in _ALLOWED:
            raise pickle.UnpicklingError(f'type not allowed in parse cache: {module}.{name}')
        return getattr(importlib.import_module(module), name)


def _header(loader: str) -> dict:
    from . import __version__
    return {'format': FORMAT, 'tool': __version__, 'loader': loader,
            'python': list(sys.version_info[:2])}


def load(root, loader: str) -> dict:
    """Return ``{relpath: entry}``; empty on any miss, mismatch or damage."""
    from .store import read_state
    try:
        raw = read_state(root, CACHE_REL)
    except (OSError, ValueError):
        return {}
    if raw is None:
        return {}
    try:
        data = _Unpickler(io.BytesIO(raw)).load()
    except Exception:  # a damaged or foreign cache is simply rebuilt
        return {}
    if not isinstance(data, dict) or data.get('header') != _header(loader):
        return {}
    entries = data.get('entries')
    return entries if isinstance(entries, dict) else {}


def save(root, loader: str, entries: dict) -> bool:
    """Best effort: a read-only state directory only costs speed."""
    from .store import _atomic_state
    try:
        blob = pickle.dumps({'header': _header(loader), 'entries': entries},
                            protocol=pickle.HIGHEST_PROTOCOL)
        _atomic_state(root, CACHE_REL, blob)
        return True
    except (OSError, ValueError, pickle.PicklingError):
        return False

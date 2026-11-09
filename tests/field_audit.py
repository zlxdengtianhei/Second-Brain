"""What the tests hold of the fields a check adds to an answer it keeps: each is a field of a model schema (a
default the check fills in) or one only the program writes (``jobs.PROGRAM_WRITTEN``), which no model schema names.

``conftest`` checks every answer any test sends through a registered check; ``test_program_fields`` checks the two
sets themselves.
"""
from __future__ import annotations

import functools
import importlib
import pkgutil

import secondbrain

ROWS = ('cards', 'items')


def _walk(schema, out: set) -> set:
    if isinstance(schema, dict):
        for name, sub in (schema.get('properties') or {}).items():
            out.add(name)
            _walk(sub, out)
        for key in ('items', 'additionalProperties', 'anyOf', 'oneOf', 'allOf'):
            sub = schema.get(key)
            for one in sub if isinstance(sub, list) else [sub]:
                _walk(one, out)
    return out


@functools.cache
def model_fields() -> frozenset:
    """The name of every field of every output schema the package keeps (a module-level JSON schema)."""
    out: set = set()
    for info in pkgutil.walk_packages(secondbrain.__path__, 'secondbrain.'):
        if info.name.endswith('__main__'):
            continue
        for value in vars(importlib.import_module(info.name)).values():
            if isinstance(value, dict) and isinstance(value.get('properties'), dict):
                _walk(value, out)
    return frozenset(out)


def _fields(answer) -> set:
    if not isinstance(answer, dict):
        return set()
    out = {f'.{k}' for k in answer}
    for key in ROWS:
        rows = answer.get(key)
        out |= {f'{key}[].{k}' for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)
                for k in row}
    if isinstance(answer.get('card'), dict):
        out |= {f'card.{k}' for k in answer['card']}
    return out


def unlisted(given, kept, listed) -> list[str]:
    """The fields ``kept`` has and ``given`` had not (at the top, in a row of ``cards``/``items``, in ``card``) that
    are neither a model schema's nor in ``listed``."""
    names = model_fields() | set(listed)
    return sorted(f for f in _fields(kept) - _fields(given) if f.rsplit('.', 1)[1] not in names)

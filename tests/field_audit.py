"""What the tests hold of the fields a check adds to an answer it keeps: each sits where the answer's own schema has
a field (a default the check fills in), or it is a field only the program writes (``jobs.PROGRAM_WRITTEN``) at a
place the entry point takes such fields out of every answer (the top, a row of ``cards`` or ``items``), which no
model schema names.

The comparison is by path, nested as deep as the answer goes: a field is not let through because another schema,
or another place, has a field of the same name. ``conftest`` checks every answer any test sends through a
registered check; ``test_program_fields`` checks the sets themselves.
"""
from __future__ import annotations

import functools
import importlib
import pkgutil

import secondbrain

#: Where the entry point takes the fields only the program writes out of an answer (``jobs._without_program_fields``).
STRIPPED = ('', '.cards[]', '.items[]')


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
def schemas() -> tuple:
    """Every output schema the package keeps (a module-level JSON schema)."""
    out = []
    for info in pkgutil.walk_packages(secondbrain.__path__, 'secondbrain.'):
        if info.name.endswith('__main__'):
            continue
        for value in vars(importlib.import_module(info.name)).values():
            if isinstance(value, dict) and isinstance(value.get('properties'), dict) and value not in out:
                out.append(value)
    return tuple(out)


@functools.cache
def model_fields() -> frozenset:
    """The name of every field of every output schema the package keeps."""
    out: set = set()
    for schema in schemas():
        _walk(schema, out)
    return frozenset(out)


def answer_schemas(kind: str, role: str, chunk) -> list:
    """The schemas an answer of ``role`` in slot ``chunk`` of a ``kind`` job may follow; every schema when unknown."""
    from secondbrain import runner
    from secondbrain import schemas as s
    from secondbrain.export import pipeline as export
    from secondbrain.pipelines import ingest
    try:
        if kind == 'ingest':
            return [ingest._answer_schema(chunk, role)] + ([s.CARD_FIX] if role == 'producer' else [])
        if kind == 'export':
            if role == 'reviewer':
                return [s.EXPORT_REVIEW]
            return [s.EXPORT_SMOOTH if chunk == export.SMOOTH else s.EXPORT_WRITE]
        spec = runner._spec(kind)
        return [spec.produce_schema if role == 'producer' else spec.review_schema]
    except Exception:  # noqa: BLE001 - a slot or kind a test made up: every schema
        return list(schemas())


def _paths(value, path: str = '') -> set:
    out = set()
    if isinstance(value, dict):
        for key, sub in value.items():
            here = f'{path}.{key}'
            out.add(here)
            out |= _paths(sub, here)
    elif isinstance(value, list):
        for sub in value:
            out |= _paths(sub, f'{path}[]')
    return out


def _segments(path: str) -> list:
    out = []
    for part in path.split('.')[1:]:
        name, rows = part.rstrip('[]'), part.count('[]')
        out += [name] + ['[]'] * rows
    return out


def _described(schema, segments: list) -> bool:
    if not segments:
        return True
    if not isinstance(schema, dict):
        return False
    for key in ('anyOf', 'oneOf', 'allOf'):
        if any(_described(one, segments) for one in schema.get(key) or []):
            return True
    head, rest = segments[0], segments[1:]
    if head == '[]':
        return _described(schema.get('items'), rest)
    props = schema.get('properties') or {}
    if head in props:
        return _described(props[head], rest)
    extra = schema.get('additionalProperties')
    return isinstance(extra, dict) and _described(extra, rest)


def _program_field(path: str, listed) -> bool:
    """A field only the program writes at a place it is taken out of answers, or anything inside one. ``listed``:
    the names, or ``{place: names}`` for each place of :data:`STRIPPED`."""
    parts = path.split('.')
    for i in range(1, len(parts)):
        where, name = '.'.join(parts[:i]), parts[i].rstrip('[]')
        names = listed.get(where, ()) if isinstance(listed, dict) else (listed if where in STRIPPED else ())
        if name in names:
            return True
    return False


def unlisted(given, kept, listed, kind: str | None = None, role: str | None = None, chunk=None) -> list[str]:
    """The paths ``kept`` has and ``given`` had not that neither the answer's schema describes nor are fields of
    ``listed`` at a place the entry point takes them out."""
    if not isinstance(kept, dict):
        return []
    added = _paths(kept) - _paths(given if isinstance(given, dict) else {})
    options = answer_schemas(kind, role, chunk) if kind else list(schemas())
    return sorted(p for p in added if not _program_field(p, listed)
                  and not any(_described(schema, _segments(p)) for schema in options))

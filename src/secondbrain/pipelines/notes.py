"""The model's part of the context note, for the arms of experiment H6 (``[ingest] context_note``).

- ``off``: no note;
- ``program`` (the default): the note the program takes from the source's
  structure (:func:`secondbrain.pipelines.chunking.context_note`), no model call;
- ``summary``: plus, for every chunk after the first, a cheap model call that
  says in two or three sentences what came before and what the chunk's
  references point at (slot ``nt-<chunk>``, ≤300 characters);
- ``concepts``: plus one cheap model call for the whole source that lists its
  concepts, each with the chunks it is in (slot ``nt-concepts``).

Both calls run as the producer, before the chunk's cards are produced; an
answer that keeps failing validation is skipped and the chunk gets the
program's note alone. Nothing a note says is quoted or covered.
"""
from __future__ import annotations

import re

from ..config import CONTEXT_NOTE_MODES as MODES  # noqa: F401  (the arms, kept with the setting)
from ..schemas import AnswerError, shape_checked
from .chunking import SUMMARY_CHARS

CONCEPTS_SLOT = 'nt-concepts'
#: Text before the chunk a summary call reads (its end), in characters.
BEFORE_CHARS = 12000
MAX_CONCEPTS = 100

SUMMARY_SCHEMA = {'type': 'object', 'required': ['summary'], 'properties': {'summary': {'type': 'string'}}}
CONCEPTS_SCHEMA = {'type': 'object', 'required': ['concepts'], 'properties': {'concepts': {
    'type': 'array', 'items': {'type': 'object', 'required': ['name', 'chunks'],
                               'properties': {'name': {'type': 'string'},
                                              'chunks': {'type': 'array', 'items': {'type': 'string'}}}}}}}


def summary_slot(chunk_id: str) -> str:
    return f'nt-{chunk_id}'


def is_slot(name) -> bool:
    return isinstance(name, str) and re.fullmatch(r'nt-[a-z0-9.-]+', name) is not None


def slot_for(mode: str, chunks: list[dict], chunk_id: str) -> str | None:
    """The model note a chunk waits for in ``mode`` (None: none)."""
    if len(chunks) < 2 or mode not in ('summary', 'concepts'):
        return None
    if mode == 'concepts':
        return CONCEPTS_SLOT
    first = chunks[0]['chunk_id']
    return None if chunk_id == first else summary_slot(chunk_id)


def summary_packet(text: str, chunks: list[dict], chunk_id: str, filename: str) -> dict:
    chunk = next(c for c in chunks if c['chunk_id'] == chunk_id)
    return {'source': {'filename': filename},
            'before': text[max(0, chunk['start'] - BEFORE_CHARS):chunk['start']],
            'chunk_start': text[chunk['start']:min(chunk['end'], chunk['start'] + 1000)],
            'limit_chars': SUMMARY_CHARS}


def concepts_packet(text: str, chunks: list[dict], filename: str) -> dict:
    return {'source': {'filename': filename},
            'chunks': [{'id': c['chunk_id'], 'text': text[c['start']:c['end']]} for c in chunks]}


@shape_checked(SUMMARY_SCHEMA, payload=0, what='summary')
def validate_summary(payload) -> dict:
    summary = payload.get('summary') if isinstance(payload, dict) else None
    if not isinstance(summary, str) or not summary.strip():
        raise AnswerError('summary must be a non-empty string')
    if len(summary.strip()) > SUMMARY_CHARS:
        raise AnswerError(f'summary must be at most {SUMMARY_CHARS} characters; it has {len(summary.strip())}')
    return payload


@shape_checked(CONCEPTS_SCHEMA, payload=0, what='concept list')
def validate_concepts(payload, chunk_ids) -> dict:
    rows = payload.get('concepts') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise AnswerError('concepts must list the concepts of the source')
    if len(rows) > MAX_CONCEPTS:
        raise AnswerError(f'concepts: at most {MAX_CONCEPTS}')
    known = set(chunk_ids)
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get('name'), str) or not row['name'].strip() \
                or len(row['name']) > 80:
            raise AnswerError(f'concepts[{i}].name must be a name of at most 80 characters')
        if not isinstance(row.get('chunks'), list) or not row['chunks'] or any(c not in known for c in row['chunks']):
            raise AnswerError(f'concepts[{i}].chunks must list chunk ids of this source ({", ".join(sorted(known))})')
    return payload

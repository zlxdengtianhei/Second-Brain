"""The focused check of a reviewer's ``minor`` note that may name a blocking problem.

Reviewers saw a problem, wrote it down as ``minor`` and accepted the card: the card does not say which one, a
condition of the chunk is left out, a statement says more than the chunk. In one blind judging this was 5 of the 11
problem cards of an arm. So when a chunk's reviewer accepts a card with a ``minor`` note whose words are a sign of
such a problem (:func:`secondbrain.pipelines.cardlint.cued`), the program does not send the card back by itself: it
asks the reviewer once more, in a slot of its own (:func:`slot_for`: ``nc-c01`` for the review of ``c01``), about
each such note. Does the problem the note names make the card unfaithful to the chunk (``fact``), not readable
without the source (``unreadable``), or not one unit (``not_a_unit``)?

- A yes sends the card back as a revise of that kind, with the note and the answer as its reason. It counts toward
  the content repair rounds like any revise, and once they are used up the card waits for the owner.
- A no leaves the card accepted. A ``minor`` note without a cue word is not asked about.
- The check is not a repair round of the chunk: its slot is not a round. An answer that keeps failing the checks
  is given up, and the cards stay as the review accepted them.

The answer is stored with the rows it turns (``turned``, a field only the program writes), the way a chunk review is
stored with its turned rows. Every later step reads the chunk's review with those rows put in (:func:`applied`).
"""
from __future__ import annotations

import re

from ..schemas import AnswerError, shape_checked
from . import cardlint

PREFIX = 'nc-'
#: The kinds a yes names: the blocking kinds of a note the check asks about.
KINDS = ('fact', 'unreadable', 'not_a_unit')
_SLOT = re.compile(r'nc-c\d+(?:-\d+)*(?:\.r[1-9]\d*)?')
_STR = {'type': 'string'}

#: The reviewer's answer.
SCHEMA = {'type': 'object', 'required': ['answers'], 'properties': {'answers': {'type': 'array', 'items': {
    'type': 'object', 'required': ['id', 'answer', 'reason'],
    'properties': {'id': _STR, 'answer': {'type': 'string', 'enum': ['yes', 'no']},
                   'category': {'type': ['string', 'null'], 'enum': [*KINDS, None]}, 'reason': _STR}}}}}


def slot_for(reviewed: str) -> str:
    """The slot of the check of the notes of the review in chunk slot ``reviewed``."""
    return PREFIX + reviewed


def is_slot(name) -> bool:
    return isinstance(name, str) and _SLOT.fullmatch(name) is not None


def reviewed(slot: str) -> str:
    """The chunk slot whose review check ``slot`` asks about."""
    return slot[len(PREFIX):]


def items(review: dict) -> list[dict]:
    """``[{"id", "slug", "note"}]``: each ``minor`` note with a cue word on a card ``review`` accepts and will write
    (a card accepted as the ``same`` as an existing card is not written), in order; ids ``N1``, ``N2`` ..."""
    out = []
    for row in review.get('cards') or []:
        if not isinstance(row, dict) or row.get('decision') != 'accept' or row.get('overlap') == 'same':
            continue
        for note in row.get('notes') or []:
            if isinstance(note, dict) and note.get('category') == 'minor' and cardlint.cued(note.get('text')):
                out.append({'id': f'N{len(out) + 1}', 'slug': row['slug'], 'note': note['text']})
    return out


@shape_checked(lambda: SCHEMA, what='note check')
def validate(frozen: dict, payload, chunk_review: dict) -> dict:
    """The answer, checked against what the check asked (``frozen``, its input): one answer for each note, a yes
    with its kind, each with a reason. Returns it with the rows of ``chunk_review`` (the review asked about) it
    turns."""
    asked = {n['id']: (c['slug'], n['text']) for c in frozen['cards'] for n in c['notes']}
    errors, given = [], {}
    for i, row in enumerate(payload.get('answers') or []):
        if not isinstance(row, dict):
            continue  # the shape check names it
        rid = row.get('id')
        if rid not in asked:
            errors.append(f'answers[{i}].id {rid!r} is not a note this check asks about ({", ".join(asked)})')
            continue
        if rid in given:
            errors.append(f'{rid} is answered twice; answer each note once')
            continue
        given[rid] = row
        if row.get('answer') == 'yes' and row.get('category') not in KINDS:
            errors.append(f'{rid}: a yes names the kind of problem: category one of {", ".join(KINDS)}')
        if not isinstance(row.get('reason'), str) or not row['reason'].strip():
            errors.append(f'{rid}: give the reason in one sentence')
    missing = [rid for rid in asked if rid not in given]
    if missing:
        errors.append(f'answer each note once: {", ".join(missing)} have no answer')
    if errors:
        raise AnswerError('Note check rejected by validation:\n- ' + '\n- '.join(errors))
    answers = [{'id': rid, 'answer': given[rid]['answer'],
                'category': given[rid].get('category') if given[rid]['answer'] == 'yes' else None,
                'reason': given[rid]['reason']} for rid in asked]
    return {'answers': answers, 'turned': _turned(chunk_review, asked, answers)}


def _turned(review: dict, asked: dict, answers: list[dict]) -> list[dict]:
    """The rows of ``review`` a yes sends back: a revise of the first yes's kind, the note and the answer its reason;
    ``turned`` keeps what the reviewer had answered, the note and each yes (the same form as
    :func:`secondbrain.review_notes.turned`, with ``check``)."""
    yes: dict = {}
    for a in answers:
        if a['answer'] == 'yes':
            yes.setdefault(asked[a['id']][0], []).append(a)
    rows = {r['slug']: r for r in review.get('cards') or [] if isinstance(r, dict)}
    out = []
    for slug, found in yes.items():
        row, first = rows[slug], found[0]
        note = {'category': first['category'], 'text': asked[first['id']][1]}
        kept = {k: v for k, v in row.items() if k not in ('amend', 'decision', 'reason', 'blocking')}
        out.append({**kept, 'decision': 'revise', 'blocking': first['category'],
                    'reason': f"the check of a minor note found a {first['category']} problem: {note['text']} "
                              f"({first['reason']})",
                    'turned': {'from': 'accept', 'reason': row.get('reason'), 'note': note,
                               'check': [{k: a[k] for k in ('id', 'category', 'reason')} for a in found]}})
    return out


def applied(review: dict, check: dict | None) -> dict:
    """``review`` with the rows the check's answer (``check``, as stored) sent back put in."""
    rows = {r['slug']: r for r in (check or {}).get('turned') or []}
    if not rows:
        return review
    return {**review, 'cards': [rows.get(r.get('slug'), r) if isinstance(r, dict) else r
                                for r in review.get('cards') or []]}

"""The diagram step of the ingest chain, on top of :mod:`secondbrain.diagrams`.

It runs after review, on the final fields of the accepted cards, as its own
model call (never part of card production). The model answers with an IR per
card; :func:`secondbrain.diagrams.process` validates it and generates the
Mermaid, asks for one rewrite with the errors, or skips. Cards whose kind
rules a diagram out never reach the model.

Everything here is a pure function of the stored card list and the stored
answers, so the write step can recompute the same diagrams at any time.
Slots: ``dgNN`` for the first answer of batch NN, ``dgNN.r1`` for its rewrite.
"""
from __future__ import annotations

from typing import Any, Callable

from ..diagrams import IR_JSON_SCHEMA, Card, build_packet, fenced, process, read_results

SLOT_PREFIX = 'dg'
#: Cards per model request (the diagram prompt is written for about ten).
BATCH = 10


def is_slot(name: str | None) -> bool:
    return bool(name) and name.startswith(SLOT_PREFIX) and name[len(SLOT_PREFIX):len(SLOT_PREFIX) + 2].isdigit()


def answer_schema() -> dict:
    return {'type': 'object', 'required': ['results'],
            'properties': {'results': {'type': 'array', 'items': {
                'type': 'object', 'required': ['ref', 'ir'],
                'properties': {'ref': {'type': 'string'}, 'ir': IR_JSON_SCHEMA}}}}}


def validate_answer(payload: Any) -> dict:
    """Shape check at submit time; per-card IR problems are handled by ``process``."""
    if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
        raise ValueError('a diagram answer must be {"results": [{"ref": ..., "ir": {...}}, ...]}')
    return payload


def cards_for(accepted: list[tuple[str, str, dict]], title_of: Callable[[str], str | None]) -> list[dict]:
    """Plain card data for the diagram step, in source order; ``ref`` is the card slug."""
    out = []
    for _chunk, slug, card in accepted:
        related = []
        for rel in card.get('relations') or []:
            title = title_of(rel.get('target'))
            if title and title not in related:
                related.append(title)
        out.append({'ref': slug, 'title': card['title'], 'kind': card.get('kind'), 'domain': card.get('domain'),
                    'essence': card['essence'], 'propositions': list(card['propositions']),
                    'related_titles': related})
    return out


def _summary(outcome) -> dict:
    row = {'ref': outcome.ref, 'status': 'draw' if outcome.status == 'draw' else 'skip',
           'attempt': outcome.attempt}
    if outcome.status != 'draw':
        row['reason'] = outcome.reason or 'no valid diagram'
    if outcome.errors:
        row['errors'] = list(outcome.errors)
    return row


def evaluate(cards: list[dict], answer: Callable[[str], tuple[bool, Any]]) -> dict:
    """Walk every batch; return the steps still due and the diagrams decided so far.

    ``answer(slot)`` returns ``(present, output)``: ``present`` is False while
    the slot has no answer yet; ``output`` is None when the answer was invalid
    (every card of that request then counts as unanswered).
    Result: ``{"pending": [(slot, step, prompt, input)], "diagrams": {ref: text or None},
    "report": [...]}``; ``text`` is the fenced Mermaid block for the card.
    """
    pending, diagrams, report = [], {}, []

    def settle(outcome):
        diagrams[outcome.ref] = fenced(outcome.mermaid) if outcome.status == 'draw' else None
        report.append(_summary(outcome))

    for n, start in enumerate(range(0, len(cards), BATCH), 1):
        batch = {c['ref']: Card.from_dict(c) for c in cards[start:start + BATCH]}
        packet, pregated = build_packet(batch.values())
        for outcome in pregated:
            settle(outcome)
        if packet is None:
            continue
        slot = f'{SLOT_PREFIX}{n:02d}'
        refs = [c['ref'] for c in packet['input']['cards']]
        present, output = answer(slot)
        if not present:
            pending.append((slot, 'produce', packet['prompt'], packet['input']))
            continue
        irs = read_results(output, refs) if output is not None else {r: None for r in refs}
        rewrites = []
        for ref in refs:
            outcome = process(batch[ref], irs.get(ref), 0)
            (rewrites.append if outcome.status == 'rewrite' else settle)(outcome)
        if not rewrites:
            continue
        slot = f'{slot}.r1'
        present, output = answer(slot)
        if not present:
            entries = [o.rewrite['input']['cards'][0] for o in rewrites]
            pending.append((slot, 'repair', rewrites[0].rewrite['prompt'], {'cards': entries, 'attempt': 1}))
            continue
        refs = [o.ref for o in rewrites]
        irs = read_results(output, refs) if output is not None else {r: None for r in refs}
        for o in rewrites:
            outcome = process(batch[o.ref], irs.get(o.ref), 1)
            if outcome.status == 'rewrite':  # only when more rewrites are configured than this step runs
                outcome.status, outcome.reason = 'skip', 'no valid diagram after the rewrite'
            settle(outcome)
    return {'pending': pending, 'diagrams': diagrams, 'report': report}

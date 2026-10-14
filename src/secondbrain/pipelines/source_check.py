"""The source check of the ingest chain: the accepted cards of every chunk, judged together.

Each chunk is produced and reviewed on its own. In host mode the first-round
producers of a source all run at once and see no other chunk's cards, and a
chunk reviewer judges only its own chunk, so two chunks can each write a card
on the same point, or each propose a new topic for the same subject (drill 3:
three confirmed cards on one-pass iterators from three chunks, and two
near-synonymous Python topics in two domains). The chunk reviewers did see the
other chunks' cards and still accepted both sides of every pair.

So once every chunk has settled, and before diagrams are drawn, one reviewer
gets every accepted card and new topic of the source side by side and gives one
decision per card (``keep``, ``same`` as another card, or ``overlap`` with one)
and per new topic (``keep`` or ``same`` as another). The program applies them
to the settled chunks before anything is written; see :func:`apply`.

The step runs only when accepted cards come from two or more chunks. Its input
is frozen as the slot's candidate (made by the program, not a producer), so the
answer is bound to exactly the cards it judged.
"""
from __future__ import annotations

from typing import Any

SLOT = 'xc01'
STEP = 'source_check'
DECISIONS = ('keep', 'same', 'overlap')
TOPIC_DECISIONS = ('keep', 'same')
#: Propositions shown per card; the drill's cards had at most 8.
PROPOSITIONS_PER_CARD = 12

_STR = {'type': 'string'}
_NSTR = {'type': ['string', 'null']}
SCHEMA = {
    'type': 'object', 'required': ['cards', 'new_topics'],
    'properties': {
        'cards': {'type': 'array', 'items': {
            'type': 'object', 'required': ['slug', 'decision'],
            'properties': {'slug': _STR, 'decision': {'type': 'string', 'enum': list(DECISIONS)},
                           'with': _NSTR, 'reason': _NSTR}}},
        'new_topics': {'type': 'array', 'items': {
            'type': 'object', 'required': ['id', 'decision'],
            'properties': {'id': _STR, 'decision': {'type': 'string', 'enum': list(TOPIC_DECISIONS)},
                           'with': _NSTR, 'reason': _NSTR}}},
    }}


def is_slot(name: str | None) -> bool:
    return name == SLOT


def needed(settled: list[dict]) -> bool:
    """True when accepted cards come from two or more chunks."""
    return sum(1 for o in settled if o['accepted']) >= 2


def frozen_input(settled: list[dict], source: dict) -> dict:
    """What the reviewer judges: every accepted card and accepted new topic, sorted by where they are filed."""
    cards = []
    counts: dict = {}
    for o in settled:
        for slug, c in o['accepted'].items():
            key = (c['domain'], c['pillar'], c['topic'])
            counts[key] = counts.get(key, 0) + 1
            cards.append({'slug': slug, 'chunk': o['chunk'], 'title': c['title'], 'domain': c['domain'],
                          'pillar': c['pillar'], 'topic': c['topic'], 'essence': c.get('essence'),
                          'propositions': list(c.get('propositions') or [])[:PROPOSITIONS_PER_CARD],
                          'relations': [{'type': r.get('type'), 'target': r.get('target')}
                                        for r in c.get('relations') or [] if isinstance(r, dict)]})
    cards.sort(key=lambda c: (c['domain'], c['pillar'], c['topic'], c['chunk'], c['slug']))
    topics = []
    for o in settled:
        for key, t in o['topics'].items():
            topics.append({'id': f'T{len(topics) + 1}', 'domain': key[0], 'pillar': key[1], 'name': key[2],
                           'boundary': t.get('boundary'), 'chunk': o['chunk'], 'cards': counts.get(key, 0)})
    return {'source': {'filename': source.get('filename'), 'chunks': len(settled)},
            'cards': cards, 'new_topics': topics}


def validate_answer(frozen: dict, payload: Any) -> dict:
    """Raise ``ValueError`` listing every problem; return the payload."""
    if not isinstance(payload, dict) or not isinstance(payload.get('cards'), list) or \
            not isinstance(payload.get('new_topics'), list):
        raise ValueError('the answer must be {"cards": [...], "new_topics": [...]}')
    errors = []
    slugs = [c['slug'] for c in frozen['cards']]
    rows = _rows(payload['cards'], 'cards', 'slug', set(slugs), DECISIONS, errors)
    missing = [s for s in slugs if s not in rows]
    if missing:
        errors.append(f'cards: give a decision for every card; missing: {", ".join(missing)}')
    for slug, row in rows.items():
        if row['decision'] == 'keep':
            continue
        other = rows.get(row.get('with'))
        if other is not None and other['decision'] == 'same':
            errors.append(f'cards: {slug} names {row["with"]}, which is itself "same" as {other.get("with")}; '
                          f'name {other.get("with")} instead')
    topic_ids = [t['id'] for t in frozen['new_topics']]
    topics = _rows(payload['new_topics'], 'new_topics', 'id', set(topic_ids), TOPIC_DECISIONS, errors)
    missing = [t for t in topic_ids if t not in topics]
    if missing:
        errors.append(f'new_topics: give a decision for every new topic; missing: {", ".join(missing)}')
    for tid, row in topics.items():
        other = topics.get(row.get('with'))
        if row['decision'] == 'same' and other is not None and other['decision'] == 'same':
            errors.append(f'new_topics: {tid} names {row["with"]}, which is itself "same"; name the topic that stays')
    if errors:
        raise ValueError('; '.join(errors))
    return payload


def _rows(items, where, key, known, decisions, errors) -> dict:
    out = {}
    for i, row in enumerate(items):
        at = f'{where}[{i}]'
        if not isinstance(row, dict):
            errors.append(f'{at} must be an object')
            continue
        name, decision, other = row.get(key), row.get('decision'), row.get('with')
        if name not in known:
            errors.append(f'{at}.{key} {name!r} is not in the packet')
            continue
        if name in out:
            errors.append(f'{at}: {name} has more than one decision')
            continue
        if decision not in decisions:
            errors.append(f'{at}.decision must be one of {", ".join(decisions)}')
            continue
        if decision == 'keep':
            if other:
                errors.append(f'{at}: "keep" takes no "with"')
                continue
        else:
            if other not in known or other == name:
                errors.append(f'{at}.with must name another {key} of the packet')
                continue
            if not isinstance(row.get('reason'), str) or not row['reason'].strip():
                errors.append(f'{at}.reason must say what the two share')
                continue
        out[name] = row
    return out


def apply(settled: list[dict], frozen: dict, answer: dict) -> tuple[list[dict], dict]:
    """``(settled, report)`` with the decisions applied; nothing in ``settled`` is changed in place.

    - ``same``: the card is not written. Relations to it point to the card it
      repeats instead, and its own relations move there, except where that
      card already links the target (in either direction) or would link itself.
    - ``overlap``: both cards are written; this one with ``status: needs-review``.
    - a topic ``same`` as another: its cards are filed under that topic (domain,
      pillar and topic), and it is not added to the taxonomy.
    """
    judged = {(c['chunk'], c['slug']) for c in frozen['cards']}
    now = {(o['chunk'], s) for o in settled for s in o['accepted']}
    if judged != now:
        raise ValueError('the source check judged a different set of cards than the chunks now accept')
    rows = {r['slug']: r for r in answer['cards']}
    merged = {s: r['with'] for s, r in rows.items() if r['decision'] == 'same'}
    topic_of = {t['id']: (t['domain'], t['pillar'], t['name']) for t in frozen['new_topics']}
    refile = {topic_of[r['id']]: topic_of[r['with']] for r in answer['new_topics'] if r['decision'] == 'same'}
    original = {s: c for o in settled for s, c in o['accepted'].items()}
    kept: dict = {}
    for o in settled:
        for slug, card in o['accepted'].items():
            if slug in merged:
                continue
            card = {**card, 'relations': [dict(r) for r in card.get('relations') or []]}
            key = (card['domain'], card['pillar'], card['topic'])
            if key in refile:
                card['domain'], card['pillar'], card['topic'] = refile[key]
            row = rows[slug]
            if row['decision'] == 'overlap' and not card.get('_overlap_with'):  # a review's needs-review stays
                card.update(_overlap_with=row['with'], _overlap_reason=row['reason'])
            kept[slug] = card
    for slug, card in kept.items():  # links to a merged card go to the card it repeats
        own = {r['target'] for r in card['relations']}
        out = [r for r in card['relations'] if r['target'] not in merged]
        for r in card['relations']:
            target = merged.get(r['target'])
            if target is None or target == slug or target in own:
                continue
            own.add(target)
            out.append({**r, 'target': target, '_moved': True})
        card['relations'] = out
    for slug, into in merged.items():  # a merged card's own links move to the card it repeats
        keep = kept[into]
        for r in original[slug].get('relations') or []:
            target = merged.get(r['target'], r['target'])
            if target == into or any(x['target'] == target for x in keep['relations']):
                continue
            keep['relations'].append({**r, 'target': target, '_moved': True, '_was_on': slug})
    moved = []
    for slug, card in kept.items():  # a moved link must not make a pair point both ways
        rels = [r for r in card['relations'] if not (r.get('_moved') and r['target'] in kept and any(
            x['target'] == slug for x in kept[r['target']]['relations']))]
        moved += [{'from': slug, 'type': r['type'], 'target': r['target'], 'was_on': r['_was_on']}
                  for r in rels if r.get('_was_on')]
        card['relations'] = [{k: v for k, v in r.items() if k not in ('_moved', '_was_on')} for r in rels]
    out = []
    for o in settled:
        accepted = {s: kept[s] for s in o['accepted'] if s in kept}
        topics = {k: v for k, v in o['topics'].items() if k not in refile}
        out.append({**o, 'accepted': accepted, 'topics': topics})
    report = {
        'status': 'checked', 'cards': len(frozen['cards']),
        'merged': [{'slug': s, 'title': original[s]['title'], 'into': into, 'reason': rows[s]['reason']}
                   for s, into in merged.items()],
        'overlap': [{'slug': s, 'with': rows[s]['with'], 'reason': rows[s]['reason']}
                    for s in kept if rows[s]['decision'] == 'overlap'],
        'refiled_topics': [{'from': dict(zip(('domain', 'pillar', 'name'), k)),
                            'into': dict(zip(('domain', 'pillar', 'name'), v)),
                            'cards': sum(1 for c in original.values() if (c['domain'], c['pillar'], c['topic']) == k),
                            'reason': next(r['reason'] for r in answer['new_topics'] if topic_of[r['id']] == k)}
                           for k, v in refile.items()],
        'moved_relations': moved,
        'merged_into': dict(merged)}
    return out, report

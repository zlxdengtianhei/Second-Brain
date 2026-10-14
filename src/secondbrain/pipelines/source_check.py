"""The source check of the ingest chain: the accepted cards of every chunk, judged together.

Each chunk is produced and reviewed on its own. In host mode the first-round
producers of a source all run at once and see no other chunk's cards, and a
chunk reviewer judges only its own chunk. So two chunks can each write a card
on the same point, or each propose a new topic for the same subject, and cards
of different chunks that belong together are not linked (drill 3: 14 pairs of
cards that said the same thing, two near-synonymous Python topics in two
domains, 18 cards with no relation at all, about half of their missing links
across chunks). The chunk reviewers did see the other chunks' cards and still
accepted both sides of every pair.

So once every chunk has settled, and before diagrams are drawn, one reviewer
gets every accepted card and new topic of the source side by side and gives one
decision per card and per new topic:

- ``keep``, with ``links`` to related cards of the source (or to the existing
  cards listed as its neighbours) that it does not link yet;
- ``same`` as another card: the two make the same core claim. The card is not
  written; the propositions and flashcards it adds (``carry``, copied exactly)
  go to the card it repeats, and so do its links;
- for a new topic, ``keep`` or ``same`` as another new topic, whose place
  (domain, pillar, topic) its cards then take.

A card with no relation at all must get a link, be linked by another card's
``links``, or say in ``alone`` why it stands alone. The program applies the
decisions to the settled chunks before anything is written; see :func:`apply`.

The step runs only when accepted cards come from two or more chunks. Its input
is frozen as the slot's candidate (made by the program, not a producer), so the
answer is bound to exactly the cards it judged.
"""
from __future__ import annotations

from typing import Any, Callable

SLOT = 'xc01'
STEP = 'source_check'
DECISIONS = ('keep', 'same')
TOPIC_DECISIONS = ('keep', 'same')
#: Limits a card keeps after cards are merged into it (the producer's own limits).
MAX_PROPOSITIONS, MAX_FLASHCARDS = 12, 4
#: Existing cards shown per card as possible link targets (drill 3: the Euclid card was second).
NEAR_EXISTING = 3

_STR = {'type': 'string'}
_NSTR = {'type': ['string', 'null']}
_FLASHCARD = {'type': 'object', 'required': ['q', 'a'], 'properties': {'q': _STR, 'a': _STR}}
SCHEMA = {
    'type': 'object', 'required': ['cards', 'new_topics'],
    'properties': {
        'cards': {'type': 'array', 'items': {
            'type': 'object', 'required': ['slug', 'decision'],
            'properties': {
                'slug': _STR, 'decision': {'type': 'string', 'enum': list(DECISIONS)},
                'with': _NSTR, 'reason': _NSTR,
                'carry': {'type': ['object', 'null'], 'properties': {
                    'propositions': {'type': 'array', 'items': _STR},
                    'flashcards': {'type': 'array', 'items': _FLASHCARD}}},
                'links': {'type': ['array', 'null'], 'items': {
                    'type': 'object', 'required': ['target', 'type'],
                    'properties': {'target': _STR, 'type': _STR, 'note': _NSTR}}},
                'alone': _NSTR}}},
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


def frozen_input(settled: list[dict], source: dict, *, relation_views: dict,
                 near_existing: Callable[[dict], list[dict]] = lambda card: []) -> dict:
    """What the reviewer judges: every accepted card and new topic, sorted by where they are filed.

    ``near_existing(card)`` gives the existing cards (id, title, domain) a card
    may link; ``relation_views`` the relation types and their meanings.
    """
    accepted = [(o['chunk'], s, c) for o in settled for s, c in o['accepted'].items()]
    slugs = {s for _, s, _ in accepted}
    linked_from: dict = {}
    for _, slug, card in accepted:
        for r in card.get('relations') or []:
            if isinstance(r, dict) and r.get('target') in slugs:
                linked_from.setdefault(r['target'], []).append(slug)
    cards, counts = [], {}
    for chunk, slug, c in accepted:
        key = (c['domain'], c['pillar'], c['topic'])
        counts[key] = counts.get(key, 0) + 1
        relations = [{'type': r.get('type'), 'target': r.get('target')}
                     for r in c.get('relations') or [] if isinstance(r, dict)]
        cards.append({'slug': slug, 'chunk': chunk, 'title': c['title'], 'domain': c['domain'],
                      'pillar': c['pillar'], 'topic': c['topic'], 'essence': c.get('essence'),
                      'propositions': list(c.get('propositions') or []),
                      'flashcards': [{'q': f.get('q'), 'a': f.get('a')} for f in c.get('flashcards') or []
                                     if isinstance(f, dict)],
                      'relations': relations, 'linked_from': sorted(linked_from.get(slug, [])),
                      'isolated': not relations and not linked_from.get(slug),
                      'near_existing': near_existing(c)[:NEAR_EXISTING]})
    cards.sort(key=lambda c: (c['domain'], c['pillar'], c['topic'], c['chunk'], c['slug']))
    topics = []
    for o in settled:
        for key, t in o['topics'].items():
            topics.append({'id': f'T{len(topics) + 1}', 'domain': key[0], 'pillar': key[1], 'name': key[2],
                           'boundary': t.get('boundary'), 'chunk': o['chunk'], 'cards': counts.get(key, 0)})
    return {'source': {'filename': source.get('filename'), 'chunks': len(settled)},
            **relation_views, 'cards': cards, 'new_topics': topics}


def validate_answer(frozen: dict, payload: Any, closed: Callable[[str, str, str], str | None] | None = None) -> dict:
    """Raise ``ValueError`` listing every problem; return the payload.

    ``closed(type, domain, target_domain)`` says why a link may not be written
    between cards of those domains (the catalog's rule), or None.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get('cards'), list) or \
            not isinstance(payload.get('new_topics'), list):
        raise ValueError('the answer must be {"cards": [...], "new_topics": [...]}')
    errors = []
    cards = {c['slug']: c for c in frozen['cards']}
    rows = _rows(payload['cards'], 'cards', 'slug', set(cards), DECISIONS, errors)
    missing = [s for s in cards if s not in rows]
    if missing:
        errors.append(f'cards: give a decision for every card; missing: {", ".join(missing)}')
    topic_ids = [t['id'] for t in frozen['new_topics']]
    topics = _rows(payload['new_topics'], 'new_topics', 'id', set(topic_ids), TOPIC_DECISIONS, errors)
    missing = [t for t in topic_ids if t not in topics]
    if missing:
        errors.append(f'new_topics: give a decision for every new topic; missing: {", ".join(missing)}')
    for tid, row in topics.items():
        other = topics.get(row.get('with'))
        if row['decision'] == 'same' and other is not None and other['decision'] == 'same':
            errors.append(f'new_topics: {tid} names {row["with"]}, which is itself "same"; name the topic that stays')
    merged = {s: r['with'] for s, r in rows.items() if r['decision'] == 'same'}
    for slug, into in merged.items():
        if into in merged:
            errors.append(f'cards: {slug} names {into}, which is itself "same" as {merged[into]}; '
                          f'name {merged[into]} instead')
    # what each kept card ends with: its own propositions and flashcards plus what merged cards carry to it
    totals = {s: [len(c['propositions']), len(c['flashcards'])] for s, c in cards.items()}
    for slug, into in merged.items():
        carry = rows[slug].get('carry') or {}
        props, flashcards = carry.get('propositions') or [], carry.get('flashcards') or []
        stray = [p for p in props if p not in cards[slug]['propositions']]
        if stray:
            errors.append(f'cards: {slug}.carry.propositions must be copied exactly from its propositions; '
                          f'not found: {stray[0]!r}')
        own = [(f['q'], f['a']) for f in cards[slug]['flashcards']]
        if any(not isinstance(f, dict) or (f.get('q'), f.get('a')) not in own for f in flashcards):
            errors.append(f'cards: {slug}.carry.flashcards must be copied exactly from its flashcards')
        if into in totals:
            totals[into][0] += len(props)
            totals[into][1] += len(flashcards)
    for slug, (props, flashcards) in totals.items():
        if props > MAX_PROPOSITIONS or flashcards > MAX_FLASHCARDS:
            errors.append(f'cards: {slug} would have {props} propositions and {flashcards} flashcards with what is '
                          f'carried to it; at most {MAX_PROPOSITIONS} and {MAX_FLASHCARDS}. Carry fewer')
    domain = _final_domains(frozen, topics)
    linked = {(s, r['target']) for s, c in cards.items() for r in c['relations']}
    added = set()
    for slug, row in rows.items():
        links = row.get('links') or []
        if links and slug in merged:
            errors.append(f'cards: {slug} is "same"; put its links on {merged[slug]}')
            continue
        existing = {e['id']: e.get('domain') for e in cards[slug]['near_existing']}
        for i, link in enumerate(links):
            at = f'cards: {slug}.links[{i}]'
            target, kind = link.get('target') if isinstance(link, dict) else None, (link or {}).get('type')
            if target in merged:
                errors.append(f'{at}: {target} is "same" as {merged[target]}; link {merged[target]}')
                continue
            if target == slug or (target not in cards and target not in existing):
                errors.append(f'{at}.target must be another card of the packet or one of its near_existing ids')
                continue
            if {(slug, target), (target, slug)} & (linked | added):
                errors.append(f'{at}: {slug} and {target} are linked already; one relation per pair')
                continue
            reason = closed(kind, domain[slug], domain.get(target) or existing.get(target)) if closed else None
            if not isinstance(kind, str) or reason:
                errors.append(f'{at}.type: {reason or "must be a relation type"}')
                continue
            added.add((slug, target))
    # a card is linked when another card's links reach it, or a merged card with relations moves into it
    targeted = {t for _, t in added} | {into for slug, into in merged.items() if cards[slug]['relations']}
    for slug, card in cards.items():
        row = rows.get(slug)
        if card['isolated'] and row and row['decision'] == 'keep' and not row.get('links') and \
                slug not in targeted and not (isinstance(row.get('alone'), str) and row['alone'].strip()):
            errors.append(f'cards: {slug} has no relation; link it to the cards it belongs with, or say in '
                          '"alone" why it stands alone')
    if errors:
        raise ValueError('; '.join(errors))
    return payload


def _final_domains(frozen: dict, topics: dict) -> dict:
    """Each card's domain once the topic decisions are applied."""
    place = {t['id']: t for t in frozen['new_topics']}
    moved = {(place[i]['domain'], place[i]['pillar'], place[i]['name']): place[r['with']]['domain']
             for i, r in topics.items() if r['decision'] == 'same' and r.get('with') in place}
    return {c['slug']: moved.get((c['domain'], c['pillar'], c['topic']), c['domain']) for c in frozen['cards']}


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
            if other or row.get('carry'):
                errors.append(f'{at}: "keep" takes no "with" or "carry"')
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

    - ``same``: the card is not written. Its carried propositions and
      flashcards, and its source quotes, are added to the card it repeats.
      Relations to it point to that card instead, and its own relations move
      there, except where that card already links the target (in either
      direction) or would link itself.
    - ``links``: added to the card, after its own relations.
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
            kept[slug] = card
    for slug, into in merged.items():  # what the merged card adds goes to the card it repeats
        carry = rows[slug].get('carry') or {}
        keep = kept[into]
        keep['propositions'] = list(keep.get('propositions') or []) + list(carry.get('propositions') or [])
        keep['flashcards'] = list(keep.get('flashcards') or []) + [
            f for f in original[slug].get('flashcards') or []
            if {'q': f.get('q'), 'a': f.get('a')} in (carry.get('flashcards') or [])]
        keep['source_quotes'] = list(dict.fromkeys(list(keep.get('source_quotes') or [])
                                                   + list(original[slug].get('source_quotes') or [])))
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
    added = []
    for slug, card in kept.items():
        for link in rows[slug].get('links') or []:
            if any(x['target'] == link['target'] for x in card['relations']) or (
                    link['target'] in kept and any(x['target'] == slug for x in kept[link['target']]['relations'])):
                continue  # linked meanwhile by a moved relation
            card['relations'].append({k: v for k, v in (('type', link['type']), ('target', link['target']),
                                                         ('note', link.get('note'))) if v})
            added.append({'from': slug, 'type': link['type'], 'target': link['target']})
    out = []
    for o in settled:
        accepted = {s: kept[s] for s in o['accepted'] if s in kept}
        topics = {k: v for k, v in o['topics'].items() if k not in refile}
        out.append({**o, 'accepted': accepted, 'topics': topics})
    report = {
        'status': 'checked', 'cards': len(frozen['cards']),
        'merged': [{'slug': s, 'title': original[s]['title'], 'into': into, 'reason': rows[s]['reason'],
                    'carried': {'propositions': len((rows[s].get('carry') or {}).get('propositions') or []),
                                'flashcards': len((rows[s].get('carry') or {}).get('flashcards') or [])}}
                   for s, into in merged.items()],
        'added_links': added,
        'alone': [{'slug': s, 'reason': rows[s]['alone']} for s in kept if rows[s].get('alone')],
        'refiled_topics': [{'from': dict(zip(('domain', 'pillar', 'name'), k)),
                            'into': dict(zip(('domain', 'pillar', 'name'), v)),
                            'cards': sum(1 for c in original.values() if (c['domain'], c['pillar'], c['topic']) == k),
                            'reason': next(r['reason'] for r in answer['new_topics'] if topic_of[r['id']] == k)}
                           for k, v in refile.items()],
        'moved_relations': moved,
        'merged_into': dict(merged)}
    return out, report

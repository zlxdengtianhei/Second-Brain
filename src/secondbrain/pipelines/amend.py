"""Mechanical fixes a reviewer gives with an ``accept`` (``amend``), applied by the program without a repair round.

A mechanical fix writes no new knowledge text: it adds, retypes or removes a
relation, refiles the card under a topic the taxonomy has, or drops one review
card. The program checks only that the result is legal (the type is open to the
card's domain and its inverse to the target's, the topic is curated, two review
cards or more stay, the link to the card an ``extends`` card extends stays); it
does not check that a direction or a drop is right. A fix that is not legal
turns the card's ``accept`` into an ordinary ``revise`` (:func:`refused`), and
the producer repairs it in a repair round. Each fix is listed in the job's
result (``amended``, before and after).
"""
from __future__ import annotations

from typing import Callable

OPS = ('add_relation', 'retype_relation', 'remove_relation', 'refile', 'drop_flashcard')
#: The steps of a card's relations each operation records (decisions.STEPS, the reviewer's step).
RELATION_OPS = ('add_relation', 'retype_relation', 'remove_relation')
_FIELDS = {'add_relation': ('type', 'target'), 'retype_relation': ('type', 'target'),
           'remove_relation': ('target',), 'refile': ('pillar', 'topic'), 'drop_flashcard': ('q',)}

SCHEMA = {'type': ['array', 'null'], 'items': {
    'type': 'object', 'required': ['op'],
    'properties': {'op': {'type': 'string', 'enum': list(OPS)}, 'type': {'type': 'string'},
                   'target': {'type': 'string'}, 'note': {'type': ['string', 'null']},
                   'tier': {'type': ['string', 'null']}, 'domain': {'type': ['string', 'null']},
                   'pillar': {'type': 'string'}, 'topic': {'type': 'string'}, 'q': {'type': 'string'}}}}


def shape_problems(slug: str, amend) -> list[str]:
    """What is wrong with the form of a card's ``amend`` (a list of operations with their fields)."""
    if amend is None:
        return []
    if not isinstance(amend, list):
        return [f'{slug}: amend must be a list of operations']
    out = []
    for i, item in enumerate(amend):
        op = item.get('op') if isinstance(item, dict) else None
        if op not in OPS:
            out.append(f'{slug}: amend[{i}].op must be one of {", ".join(OPS)}')
            continue
        missing = [k for k in _FIELDS[op] if not isinstance(item.get(k), str) or not item[k].strip()]
        if missing:
            out.append(f'{slug}: amend[{i}] ({op}) needs {", ".join(missing)}')
    return out


def apply(card: dict, amend) -> dict:
    """``card`` with its mechanical fixes made (relations, place, review cards); the card is not changed."""
    out = {**card, 'relations': [dict(r) for r in card.get('relations') or [] if isinstance(r, dict)],
           'flashcards': [dict(f) for f in card.get('flashcards') or [] if isinstance(f, dict)]}
    for item in amend or []:
        op = item['op']
        if op == 'add_relation':
            out['relations'].append({k: item[k] for k in ('type', 'target', 'note', 'tier') if item.get(k)})
        elif op == 'retype_relation':
            out['relations'] = [{**r, 'type': item['type'], **({'note': item['note']} if item.get('note') else {})}
                                if r.get('target') == item['target'] else r for r in out['relations']]
        elif op == 'remove_relation':
            out['relations'] = [r for r in out['relations'] if r.get('target') != item['target']]
        elif op == 'refile':
            out.update(domain=item.get('domain') or out.get('domain'), pillar=item['pillar'], topic=item['topic'])
        elif op == 'drop_flashcard':
            out['flashcards'] = [f for f in out['flashcards'] if f.get('q') != item['q']]
    return out


def problems(card: dict, amend, *, closed: Callable, legal: Callable, domain_of: Callable,
             keep_link: str | None = None) -> list[str]:
    """Why ``amend`` cannot be made on ``card``: each operation on the card as the ones before left it, then the
    result as a whole.

    ``closed(type, domain, target_domain)`` says why a relation type is not
    open (None: open); ``legal(domain, pillar, topic)`` whether a place is a
    curated one; ``domain_of(target)`` the domain of a card the relation may
    point at (None: no such card); ``keep_link`` the card an ``extends`` card
    extends, which it must keep linking.
    """
    out, now, slug = [], card, card.get('slug')
    for i, item in enumerate(amend or []):
        op, at = item['op'], f'amend[{i}] ({item["op"]})'
        targets = {r.get('target'): r for r in now.get('relations') or []}
        if op == 'add_relation':
            if item['target'] == slug or domain_of(item['target']) is None:
                out.append(f'{at}: {item["target"]} is not a card it can link')
            elif item['target'] in targets:
                out.append(f'{at}: it links {item["target"]} already; retype that relation instead')
        elif op in ('retype_relation', 'remove_relation') and item['target'] not in targets:
            out.append(f'{at}: it has no relation to {item["target"]}')
        elif op == 'retype_relation' and targets[item['target']].get('type') == item['type']:
            out.append(f'{at}: the relation to {item["target"]} is {item["type"]} already')
        elif op == 'remove_relation' and item['target'] == keep_link:
            out.append(f'{at}: it extends {keep_link} and keeps linking it')
        elif op == 'refile' and not legal(item.get('domain') or now.get('domain'), item['pillar'], item['topic']):
            out.append(f'{at}: {item["pillar"]} / {item["topic"]} is not a curated topic of '
                       f'{item.get("domain") or now.get("domain")}')
        elif op == 'drop_flashcard' and item['q'] not in {f.get('q') for f in now.get('flashcards') or []}:
            out.append(f'{at}: it has no review card asking {item["q"]!r}')
        now = apply(now, [item])
    if len(now.get('flashcards') or []) < 2:
        out.append('after the fixes it keeps fewer than 2 review cards')
    for r in now.get('relations') or []:
        if r.get('type') and domain_of(r.get('target')) is not None:
            why = closed(r['type'], now.get('domain'), domain_of(r['target']))
            if why and any(item.get('target') == r.get('target') or item['op'] == 'refile' for item in amend or []):
                out.append(f'the relation {r["type"]} to {r["target"]}: {why}')
    return out


def refused(row: dict, why: list[str]) -> dict:
    """A review row whose fixes cannot be made: an ordinary ``revise`` that names them (its level is not read)."""
    kept = {k: v for k, v in row.items() if k not in ('overlap', 'with', 'amend', 'decision', 'reason')}
    return {**kept, 'decision': 'revise',
            'reason': f'{row.get("reason", "").strip()} The mechanical fix could not be made: {"; ".join(why)}',
            'amend_refused': {'amend': row.get('amend'), 'problems': why}}


def changes(card: dict, amend) -> list[dict]:
    """``[{op, before, after}]`` of each fix, for the job's result."""
    out, now = [], card
    for item in amend or []:
        after = apply(now, [item])
        if item['op'] in RELATION_OPS:
            def rel(c):
                return next((r for r in c['relations'] if r.get('target') == item['target']), None)
            before_value, after_value = rel(apply(now, [])), rel(after)
        elif item['op'] == 'refile':
            before_value = {k: now.get(k) for k in ('domain', 'pillar', 'topic')}
            after_value = {k: after.get(k) for k in ('domain', 'pillar', 'topic')}
        else:
            before_value = next((f for f in now.get('flashcards') or [] if f.get('q') == item['q']), None)
            after_value = None
        out.append({'op': item['op'], 'before': before_value, 'after': after_value})
        now = after
    return out

"""The split slot: a card that extends an existing card that cannot take it is rewritten as a narrower card.

When the patch the merge check made for an existing card would leave it with
more than 12 propositions or 8 review cards, the existing card is not changed.
Each card of the source that extends it gets a split slot (``sp01``, ``sp02``
...), which works like a chunk slot: the producer rewrites the card as one that
holds only what the existing card lacks and answers a narrower question (how it
behaves in a case, one complete example), with a ``part_of`` or ``example_of``
relation to the existing card; the program checks it; a reviewer of another
run checks that it is faithful, readable on its own, one unit, and that it does
not restate the existing card's propositions (design D-09: the split card is
written as ``distinct`` once that holds; it is not judged on the scale again);
a rejected card is rewritten in a repair round (``sp01.r1`` ...) while
``max_repair_rounds`` lasts, else it stays pending. A split card is never
written word for word next to the card it extends.
"""
from __future__ import annotations

import re

from .. import decisions, review_notes
from ..cards import KINDS
from ..evidence import check_verbatim
from ..schemas import shape_checked
from ..textnorm import near_same, same_text

PREFIX = 'sp'
ROLE = 'split_reviewer'
#: Relation types a split card points at the card it narrows with, in order of preference.
NARROWER = ('part_of', 'example_of')
_SLOT = re.compile(r'sp\d{2,}(?:\.r[1-9]\d*)?')

_STR = {'type': 'string'}
_NSTR = {'type': ['string', 'null']}
CARD_SCHEMA = {
    'type': 'object', 'required': ['card'],
    'properties': {'card': {
        'type': 'object',
        'required': ['slug', 'title', 'kind', 'essence', 'propositions', 'flashcards', 'source_quotes', 'relations'],
        'properties': {'slug': _STR, 'title': _STR, 'kind': {'type': 'string', 'enum': list(KINDS)}, 'essence': _STR,
                       'propositions': {'type': 'array', 'items': _STR}, 'intuition': _NSTR,
                       'confusions': {'type': 'array', 'items': _STR},
                       'flashcards': {'type': 'array', 'items': {'type': 'object', 'required': ['q', 'a'],
                                                                 'properties': {'q': _STR, 'a': _STR}}},
                       'source_quotes': {'type': 'array', 'items': _STR},
                       'relations': {'type': 'array', 'items': {'type': 'object', 'required': ['type', 'target'],
                                                                'properties': {'type': _STR, 'target': _STR,
                                                                               'note': _NSTR}}},
                       'reason': _NSTR}}}}
REVIEW_SCHEMA = {
    'type': 'object', 'required': ['decision', 'restates', 'reason'],
    'properties': {'decision': {'type': 'string', 'enum': ['accept', 'revise']},
                   'restates': {'type': 'boolean'}, 'reason': _STR, 'notes': review_notes.NOTES,
                   # each proposition of the card, and the proposition of the existing card that says it (D-22)
                   'matches': {'type': 'array', 'items': {
                       'type': 'object', 'required': ['proposition', 'existing'],
                       'properties': {'proposition': _STR, 'existing': {'type': ['string', 'null']}}}}}}


def is_slot(name: str | None) -> bool:
    return isinstance(name, str) and _SLOT.fullmatch(name) is not None


def slot_name(n: int) -> str:
    return f'{PREFIX}{n:02d}'


@shape_checked(CARD_SCHEMA, what='split card')
def validate_card(item: dict, payload, *, text: str, taken: set, catalog_types, siblings=()) -> dict:
    """Raise ``ValueError`` listing every problem; return the payload.

    ``item`` is the split plan's entry (the card, its existing card's
    propositions and id), ``text`` the card's chunk, ``taken`` the slugs and ids
    the new card may not use.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get('card'), dict):
        raise ValueError('the answer must be {"card": {...}}')
    card, errors = payload['card'], []
    slug = card.get('slug')
    if not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,59}', slug) or slug in taken:
        errors.append('card.slug must be a new slug ([a-z0-9-]) no other card of the source uses')
    props, flashcards = card.get('propositions'), card.get('flashcards')
    if not isinstance(props, list) or not 1 <= len(props) <= 12 or any(not isinstance(p, str) or not p.strip()
                                                                       for p in props):
        errors.append('card.propositions must be 1-12 non-empty strings')
        props = []
    if not isinstance(flashcards, list) or not 2 <= len(flashcards) <= 4 or any(
            not isinstance(f, dict) or not all(isinstance(f.get(k), str) and f[k].strip() for k in ('q', 'a'))
            or '::' in f['q'] + f['a'] for f in flashcards):
        errors.append('card.flashcards must hold 2-4 question/answer pairs without "::"')
    for key in ('title', 'essence'):
        if not isinstance(card.get(key), str) or not card[key].strip():
            errors.append(f'card.{key} must be a non-empty string')
    if card.get('kind') not in KINDS:
        errors.append(f'card.kind must be one of {", ".join(KINDS)}')
    quotes = card.get('source_quotes')
    if not isinstance(quotes, list) or not quotes:
        errors.append('card.source_quotes must list verbatim passages of the chunk')
    else:
        errors += [f'card.source_quotes: {problem}' for problem in (check_verbatim(text, q) for q in quotes) if problem]
    for p in props:  # D-22: the narrower cards of one card hold different points (asked again, never cut)
        hit = next(((slug_, q, same_text(p, q)) for slug_, qs in siblings for q in qs
                    if same_text(p, q) or near_same(p, q)), None)
        if hit:
            errors.append(f'card.propositions: {p!r} {"says" if hit[2] else "may say"} what the narrower card '
                          f'{hit[0]} of {item["with"]} says ({hit[1]!r}); each narrower card holds its own points: '
                          'leave it to that card')
    restated = [p for p in props if any(same_text(p, old) for old in item['existing_propositions'])]
    if restated:
        errors.append(f'card.propositions restate the propositions of {item["with"]} ({restated[0]!r}); keep only what '
                      'that card does not say')
    links = [r for r in card.get('relations') or [] if isinstance(r, dict) and r.get('target') == item['with']]
    wanted = [t for t in NARROWER if t in catalog_types]
    if wanted and not any(r.get('type') in wanted for r in links):
        errors.append(f'card.relations must link {item["with"]} with {" or ".join(wanted)}: the narrower card is a '
                      'part or an example of it')
    if any(not isinstance(r, dict) or r.get('type') not in catalog_types for r in card.get('relations') or []):
        errors.append(f'card.relations: type must be one of {sorted(catalog_types)}')
    if errors:
        raise ValueError('Split card rejected by validation:\n- ' + '\n- '.join(errors))
    return payload


@shape_checked(REVIEW_SCHEMA, payload=0, what='split review')
def validate_review(payload, *, card: dict | None = None, existing: int | None = None) -> dict:
    """The split review, checked; an accept goes back when it notes a blocking problem (D-18 3) or when ``matches``
    pairs a proposition of ``card`` with one of the ``existing`` propositions of the card it narrows (D-22: the
    program sends it back to be written again without it, never leaves it out). With ``card`` given, ``matches``
    must name each of its propositions once."""
    if not isinstance(payload, dict) or payload.get('decision') not in ('accept', 'revise') or \
            not isinstance(payload.get('restates'), bool) or not isinstance(payload.get('reason'), str) or \
            not payload['reason'].strip():
        raise ValueError('the review must be {"decision": "accept"|"revise", "restates": true|false, "reason": "..."}')
    if payload['decision'] == 'accept' and payload['restates']:
        raise ValueError('a card that restates the propositions of the card it narrows is not accepted; revise it')
    wrong = review_notes.problems(payload.get('notes'), 'review')
    matched = []
    if card is not None:
        want = [f'P{i}' for i in range(1, len(card.get('propositions') or []) + 1)]
        rows = payload.get('matches')
        named = [r.get('proposition') for r in rows or [] if isinstance(r, dict)]
        if rows is None or sorted(named) != sorted(want):
            wrong.append(f'matches must name each proposition of the card once ({", ".join(want)}), each with the '
                         'proposition of existing_card that says it in any words, or null')
        for r in rows or []:
            ref = r.get('existing') if isinstance(r, dict) else None
            if ref is None:
                continue
            number = int(ref[1:]) if isinstance(ref, str) and re.fullmatch(r'P[1-9]\d*', ref) else 0
            if not 1 <= number <= (existing or 0):
                wrong.append(f'matches: {ref!r} for {r.get("proposition")} must be a proposition of existing_card '
                             f'(P1–P{existing or 0}) or null')
            else:
                matched.append((r.get('proposition'), ref))
    if wrong:
        raise ValueError('the review was rejected by validation:\n- ' + '\n- '.join(wrong))
    if matched and payload['decision'] == 'accept':  # D-22: a proposition the existing card says goes back
        said = '; '.join(f'{p} says what the existing card {e} says' for p, e in matched)
        payload = {**payload, 'decision': 'revise', 'restates': True,
                   'reason': f'{said}: write the card again without it, keeping only what the existing card does '
                             'not say',
                   'turned': {'from': 'accept', 'reason': payload.get('reason'), 'matches': matched}}
    return review_notes.turned(payload)  # an accept with a blocking note goes back (D-18 3)


def record(item: dict, card: dict, review: dict, entries: list, *, slot: str, run: str | None,
           producer_run: str | None, facts) -> None:
    """The split reviewer's final values, and the first steps of the split card's relations and place."""
    slug = card['slug']
    earlier = decisions.current(entries, 'split.verdict', item['slug'])
    reason = 'repair_round' if earlier is not None else None
    decisions.record(entries, 'split.verdict', item['slug'], {'decision': review['decision'], 'card': slug,
                                                             'reason': review['reason']},
                     role=ROLE, slot=slot, run=run, reason=reason, facts=facts)
    if review['decision'] != 'accept':
        return
    decisions.record(entries, 'overlap.split', item['slug'], {'overlap': 'distinct', 'with': [item['with']],
                                                             'card': slug}, role=ROLE, slot=slot, run=run,
                     reason='repair_round' if decisions.current(entries, 'overlap.split', item['slug']) else None,
                     facts=facts)
    decisions.step(entries, 'relations', slug, 'create', role='producer', slot=slot, run=producer_run,
                   data={'relations': [{k: v for k, v in r.items() if k in ('type', 'target', 'note') and v}
                                       for r in card.get('relations') or []]})
    decisions.step(entries, 'classification', slug, 'propose', role='producer', slot=slot, run=producer_run,
                   data=dict(item['place']))
    decisions.step(entries, 'classification', slug, 'accept', role=ROLE, slot=slot, run=run, data=dict(item['place']))

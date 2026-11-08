"""The merge check of the ingest chain: the cards of one source judged together, before anything is written.

Each chunk is produced and reviewed on its own. In host mode the first-round
producers of a source all run at once and see no other chunk's cards, and a
chunk reviewer judges only its own chunk. So two chunks can each write a card
on the same point, or each propose a new topic for the same subject, and cards
of different chunks that belong together are not linked (drill 3: 14 pairs of
cards that said the same thing, two near-synonymous Python topics in two
domains, 18 cards with no relation at all, about half of their missing links
across chunks).

The chunk reviewer is the final judge of a card against the vault's cards
(:mod:`secondbrain.overlap`): a card it found ``same`` is not written and never
reaches this step. Once every chunk has settled, one reviewer gets the cards the
chunk reviewers found ``distinct`` (the *decision set*) and the source's new
topics side by side, and gives each card its level against the other cards of
the source, on the shared scale:

- ``distinct``, with ``links`` to related cards of the source (or to existing
  cards among its candidates) that it does not link yet;
- ``extends`` one card of the source (``with``): the card is not written; what it
  adds (``carry``: propositions and review cards copied exactly, and
  ``replace`` for a fuller wording of one of that card's propositions) goes to
  that card, and so do its links;
- ``same`` as cards of the source: it is not written and nothing is carried;
  its links go to the first card in ``with``;
- for a new topic, ``distinct``, or ``same`` as another new topic, whose place
  (domain, pillar, topic) its cards then take.

A card with no relation at all must get a link, be linked by another card's
``links``, or say in ``alone`` why it stands alone. The answer's values go to the
job's decision record (``overlap.source``, ``topic.same``, the ``links`` as steps
of ``relations``); :func:`apply` reads them only from there.

The step runs when cards of the decision set come from two or more chunks. Its
input is frozen as the slot's candidate (made by the program, not a producer),
so the answer is bound to exactly the cards it judged.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from .. import decisions
from ..overlap import LEVELS, TOPIC_LEVELS, with_problem
from ..schemas import shape_checked
from ..textnorm import near_same, plain, same_text

SLOT = 'xc01'
STEP = 'source_check'
#: The decision record's name for this step's role.
ROLE = 'merge_check'
#: Limits a card keeps after cards are merged into it: the producer's 12 propositions, and twice its 4
#: flashcards, so a point carried over keeps its question (J4 on the drill 3 replay: a cap of 4 left 7 points
#: without any question in the vault).
MAX_PROPOSITIONS, MAX_FLASHCARDS = 12, 8
#: ``[[Title]]``, ``[[Title#heading]]`` and ``[[Title|alias]]`` in card text.
_WIKILINK = re.compile(r'\[\[([^\[\]|#]+)((?:#[^\[\]|]*)?)((?:\|[^\[\]]*)?)\]\]')

_STR = {'type': 'string'}
_NSTR = {'type': ['string', 'null']}
_FLASHCARD = {'type': 'object', 'required': ['q', 'a'], 'properties': {'q': _STR, 'a': _STR}}
#: Carry completeness: a proposition of an ``extends`` card that is not carried (nor used in ``replace``) is named
#: with the proposition of the target that already says it (``{"text": <word for word>, "as": "P3"}``).
_ALREADY = {'type': 'array', 'items': {'type': 'object', 'required': ['text', 'as'],
                                       'properties': {'text': _STR, 'as': _STR}}}
#: A carried proposition or question that only looks like one of the existing card's (``P3``, ``Q2``), and why
#: it is another point (the program sends close wordings back otherwise; the same words always).
_DIFFERS = {'type': 'array', 'items': {'type': 'object', 'required': ['text', 'from', 'reason'],
                                       'properties': {'text': _STR, 'from': _STR, 'reason': _STR}}}

SCHEMA = {
    'type': 'object', 'required': ['cards', 'new_topics'],
    'properties': {
        'cards': {'type': 'array', 'items': {
            'type': 'object', 'required': ['slug', 'overlap'],
            'properties': {
                'slug': _STR, 'overlap': {'type': 'string', 'enum': list(LEVELS)},
                'with': {'type': ['array', 'null'], 'items': _STR}, 'reason': _NSTR,
                'carry': {'type': ['object', 'null'], 'properties': {
                    'propositions': {'type': 'array', 'items': _STR},
                    'flashcards': {'type': 'array', 'items': _FLASHCARD},
                    'replace': {'type': 'array', 'items': {
                        'type': 'object', 'required': ['proposition', 'by'],
                        'properties': {'proposition': _STR, 'by': _STR}}},
                    'already': _ALREADY, 'differs': _DIFFERS}},
                'links': {'type': ['array', 'null'], 'items': {
                    'type': 'object', 'required': ['target', 'type'],
                    'properties': {'target': _STR, 'type': _STR, 'note': _NSTR}}},
                'alone': _NSTR}}},
        'new_topics': {'type': 'array', 'items': {
            'type': 'object', 'required': ['id', 'overlap'],
            'properties': {'id': _STR, 'overlap': {'type': 'string', 'enum': list(TOPIC_LEVELS)},
                           'with': _NSTR, 'reason': _NSTR}}},
        'patches': {'type': 'array', 'items': {
            'type': 'object', 'required': ['target'],
            'properties': {'target': _STR,
                           'propositions': {'type': 'array', 'items': _STR},
                           'flashcards': {'type': 'array', 'items': _FLASHCARD},
                           'replace': {'type': 'array', 'items': {
                               'type': 'object', 'required': ['proposition', 'by'],
                               'properties': {'proposition': _STR, 'by': _STR}}},
                           'already': _ALREADY, 'differs': _DIFFERS}}},
        'disputes': {'type': 'array', 'items': {
            'type': 'object', 'required': ['slug', 'reason'], 'properties': {'slug': _STR, 'reason': _STR}}},
    }}


def is_slot(name: str | None) -> bool:
    """True for the slot of any round of the merge check (``xc01``, after a dispute ``xc01.r1`` ...)."""
    return isinstance(name, str) and re.fullmatch(re.escape(SLOT) + r'(?:\.r[1-9]\d*)?', name) is not None


def topic_key(domain, pillar, name) -> str:
    """The decision record's name of a new topic."""
    return json.dumps([domain, pillar, name], ensure_ascii=False)


def needed(settled: list[dict]) -> bool:
    """True when cards of the decision set (the ones still accepted) come from two or more chunks, or a card of the
    patch set (``repeats``: it extends an existing card that can take a patch) waits for its patch."""
    return sum(1 for o in settled if o['accepted']) >= 2 or any(o.get('repeats') for o in settled)


def round_slot(n: int) -> str:
    """The slot of the merge check's round ``n`` (0: the first; a later one after a dispute)."""
    return SLOT if n == 0 else f'{SLOT}.r{n}'



def frozen_input(settled: list[dict], source: dict, *, relation_views: dict,
                 candidates: dict | None = None, candidate_cards: dict | None = None,
                 existing: Callable[[str], dict] | None = None) -> dict:
    """What the reviewer judges: every card of the decision set and every new topic, sorted by where they are filed.

    ``candidates`` gives, by slug, the ids of the existing cards a card may link, closest first, and
    ``candidate_cards`` shows each of them once (``{id: view}`` with its domain, packed within the budget by
    :func:`secondbrain.retrieval.fit`); ``relation_views`` the relation types and their meanings.
    """
    candidates = candidates or {}
    accepted = [(o['chunk'], s, c) for o in settled for s, c in o['accepted'].items()]
    slugs = {s for _, s, _ in accepted}
    linked_from: dict = {}
    for _, slug, card in accepted:
        for r in card.get('relations') or []:
            if isinstance(r, dict) and r.get('target') in slugs:
                linked_from.setdefault(r['target'], []).append(slug)
    notes_of: dict = {}
    for o in settled:  # what the chunk reviewers noted without sending a card back, for this check to read
        for n in o.get('notes') or []:
            notes_of.setdefault(n['slug'], []).append(f"[{n['category']}] {n['note']}" if n.get('category') else n['note'])
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
                      'candidates': list(candidates.get(slug) or []),
                      **({'amended': c['_amended']} if c.get('_amended') else {}),
                      **({'review_notes': notes_of[slug]} if notes_of.get(slug) else {})})
    cards.sort(key=lambda c: (c['domain'], c['pillar'], c['topic'], c['chunk'], c['slug']))
    topics = []
    for o in settled:
        for key, t in o['topics'].items():
            topics.append({'id': f'T{len(topics) + 1}', 'domain': key[0], 'pillar': key[1], 'name': key[2],
                           'boundary': t.get('boundary'), 'chunk': o['chunk'], 'cards': counts.get(key, 0)})
    patch_set = {}
    for o in settled:  # the cards the chunk reviewers found extend an existing card, by that card
        for slug, c in (o.get('repeats') or {}).items():
            target = c['_with']
            group = patch_set.setdefault(target, {'target': target, 'card': existing(target) if existing else None,
                                                  'from': []})
            group['from'].append({'slug': slug, 'chunk': o['chunk'], 'title': c['title'], 'essence': c.get('essence'),
                                  'propositions': list(c.get('propositions') or []),
                                  'flashcards': [{'q': f.get('q'), 'a': f.get('a')}
                                                 for f in c.get('flashcards') or [] if isinstance(f, dict)]})
    return {'source': {'filename': source.get('filename'), 'chunks': len(settled)},
            **relation_views, 'cards': cards, 'candidate_cards': dict(candidate_cards or {}), 'new_topics': topics,
            'patch_set': [patch_set[t] for t in sorted(patch_set)]}


@shape_checked(SCHEMA, what='merge check answer')
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
    patch_cards = {c['slug']: c for g in frozen.get('patch_set') or [] for c in g['from']}
    rows = _card_rows(payload['cards'], cards, errors, also=patch_cards)
    missing = [s for s in cards if s not in rows]
    if missing:
        errors.append(f'cards: give a level for every card; missing: {", ".join(missing)}')
    topic_ids = [t['id'] for t in frozen['new_topics']]
    topics = _topic_rows(payload['new_topics'], set(topic_ids), errors)
    missing = [t for t in topic_ids if t not in topics]
    if missing:
        errors.append(f'new_topics: give a level for every new topic; missing: {", ".join(missing)}')
    merged = {s: r['with'][0] for s, r in rows.items() if r['overlap'] != 'distinct'}
    for slug, row in rows.items():
        for other in row['with'] if row['overlap'] != 'distinct' else []:
            if other in merged:
                errors.append(f'cards: {slug} names {other}, which is itself "{rows[other]["overlap"]}"; '
                              f'name the card that stays')
    # what each kept card ends with: its own propositions and flashcards plus what extends cards carry to it
    totals = {s: [len(c['propositions']), len(c['flashcards'])] for s, c in cards.items()}  # patch set: a split
    replaced = set()
    for slug, into in merged.items():
        carry = rows[slug].get('carry') or {}
        props, flashcards = carry.get('propositions') or [], carry.get('flashcards') or []
        stray = [p for p in props if p not in cards[slug]['propositions']]
        if stray:
            errors.append(f'cards: {slug}.carry.propositions must be copied exactly from its propositions; '
                          f'not found: {stray[0]!r}')
        own = [(f['q'], f['a']) for f in cards[slug]['flashcards']]  # noqa: the card's own review cards
        if any(not isinstance(f, dict) or (f.get('q'), f.get('a')) not in own for f in flashcards):
            errors.append(f'cards: {slug}.carry.flashcards must be copied exactly from its flashcards')
        for i, swap in enumerate(carry.get('replace') or []):
            at = f'cards: {slug}.carry.replace[{i}]'
            ref, by = (swap.get('proposition'), swap.get('by')) if isinstance(swap, dict) else (None, None)
            number = int(ref[1:]) if isinstance(ref, str) and re.fullmatch(r'P[1-9]\d*', ref) else 0
            target = cards.get(into) or patch_cards.get(into)
            if target and not 1 <= number <= len(target['propositions']):
                errors.append(f'{at}.proposition must name one of the propositions of {into} (P1–P'
                              f'{len(target["propositions"])})')
            elif (into, number) in replaced:
                errors.append(f'{at}: {into} {ref} is replaced twice')
            if by not in cards[slug]['propositions'] or by in props:
                errors.append(f'{at}.by must be one of its propositions, copied exactly, and not also in '
                              'carry.propositions')
            replaced.add((into, number))
        if rows[slug]['overlap'] == 'extends':
            target = cards.get(into) or patch_cards.get(into)
            errors += _incomplete(f'cards: {slug}.carry', cards[slug]['propositions'], props,
                                  carry.get('replace') or [], carry.get('already'),
                                  len(target['propositions']) if target else 0, into)
            if into in cards:  # D-21: what goes into a card of the source is checked as a patch is, never cut
                errors += _said_twice(f'cards: {slug}.carry', into, cards[into], props, carry.get('replace') or [],
                                      flashcards, carry.get('differs'), exact_ok=True)
        if into in totals:
            totals[into][0] += len(props)
            totals[into][1] += len(flashcards)
    # past 12 propositions or 8 flashcards the program writes the extends card as a narrower card of its own
    # (a split slot, decision D-15), so the size of a kept card is no reason to send an answer back
    domain = _final_domains(frozen, topics)
    linked = {(s, r['target']) for s, c in cards.items() for r in c['relations']}
    added = set()
    for slug, row in rows.items():
        links = row.get('links') or []
        if links and slug in merged:
            errors.append(f'cards: {slug} is "{row["overlap"]}"; put its links on {merged[slug]}')
            continue
        existing = {e['id']: e.get('domain') for e in link_candidates(cards[slug], frozen)}
        for i, link in enumerate(links):
            at = f'cards: {slug}.links[{i}]'
            target, kind = (link.get('target'), link.get('type')) if isinstance(link, dict) else (None, None)
            if target in merged:
                errors.append(f'{at}: {target} is "{rows[target]["overlap"]}"; link {merged[target]}')
                continue
            if target == slug or (target not in cards and target not in existing):
                errors.append(f'{at}.target must be another card of the packet or one of its candidates')
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
        if card['isolated'] and row and row['overlap'] == 'distinct' and not row.get('links') and \
                slug not in targeted and not (isinstance(row.get('alone'), str) and row['alone'].strip()):
            errors.append(f'cards: {slug} has no relation; link it to the cards it belongs with, or say in '
                          '"alone" why it stands alone')
    _check_patches(frozen, payload, rows, merged, errors)
    if errors:
        raise Refused(errors)
    return payload


class Refused(ValueError):
    """A merge-check answer the program checks refuse; ``problems`` lists each one."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__('; '.join(problems))


def _check_patches(frozen: dict, payload: dict, rows: dict, merged: dict, errors: list) -> None:
    """Each existing card of the patch set gets one patch, from the cards that extend it, or its cards are disputed."""
    groups = {g['target']: g for g in frozen.get('patch_set') or []}
    owner = {c['slug']: t for t, g in groups.items() for c in g['from']}
    disputed = {}
    for i, row in enumerate(payload.get('disputes') or []):
        slug = row.get('slug') if isinstance(row, dict) else None
        if slug not in owner:
            errors.append(f'disputes[{i}].slug must be a card of the patch set (one that extends an existing card)')
        elif not isinstance(row.get('reason'), str) or not row['reason'].strip():
            errors.append(f'disputes[{i}]: say why {slug} cannot go into {owner[slug]}')
        else:
            disputed[slug] = row
    seen = set()
    for i, row in enumerate(payload.get('patches') or []):
        at = f'patches[{i}]'
        target = row.get('target') if isinstance(row, dict) else None
        if target not in groups:
            errors.append(f'{at}.target must be an existing card of the patch set')
            continue
        if target in seen:
            errors.append(f'{at}: {target} gets one patch; put everything for it in one')
            continue
        seen.add(target)
        sources = [c for c in groups[target]['from'] if c['slug'] not in disputed]
        merged_in = [s for s, into in merged.items() if into in {c['slug'] for c in sources}]
        props = {p for c in sources for p in c['propositions']} | {p for s in merged_in
                                                                   for p in _row_items(rows, s, 'propositions')}
        cards_ = {(f['q'], f['a']) for c in sources for f in c['flashcards']} | {
            (f.get('q'), f.get('a')) for s in merged_in for f in _row_items(rows, s, 'flashcards')}
        if not sources:
            errors.append(f'{at}: every card that extends {target} is disputed; give it no patch')
            continue
        added, flashcards = row.get('propositions') or [], row.get('flashcards') or []
        if not (added or flashcards or row.get('replace')):
            errors.append(f'{at}: an empty patch; when nothing can go into {target}, dispute its cards')
        stray = [x for x in added if x not in props]
        if stray:
            errors.append(f'{at}.propositions must be copied exactly from the cards that extend {target}; not found: '
                          f'{stray[0]!r}')
        if any(not isinstance(f, dict) or (f.get('q'), f.get('a')) not in cards_ for f in flashcards):
            errors.append(f'{at}.flashcards must be copied exactly from the cards that extend {target}')
        total = len((groups[target]['card'] or {}).get('propositions') or [])
        wanted = [p for c in sources for p in c['propositions']] + [p for s in merged_in
                                                                   for p in _row_items(rows, s, 'propositions')]
        errors += _incomplete(at, list(dict.fromkeys(wanted)), added, row.get('replace') or [], row.get('already'),
                              total, target)
        errors += _said_twice(at, target, groups[target]['card'] or {}, added, row.get('replace') or [],
                              flashcards, row.get('differs'))
        swapped = set()
        for k, swap in enumerate(row.get('replace') or []):
            ref, by = (swap.get('proposition'), swap.get('by')) if isinstance(swap, dict) else (None, None)
            number = int(ref[1:]) if isinstance(ref, str) and re.fullmatch(r'P[1-9]\d*', ref) else 0
            if not 1 <= number <= total:
                errors.append(f'{at}.replace[{k}].proposition must name one of the propositions of {target} '
                              f'(P1–P{total})')
            elif number in swapped:
                errors.append(f'{at}.replace[{k}]: {target} P{number} is replaced twice')
            if by not in props or by in added:
                errors.append(f'{at}.replace[{k}].by must be a proposition of a card that extends {target}, copied '
                              'exactly, and not also in propositions')
            swapped.add(number)
    for target, group in groups.items():
        left = [c['slug'] for c in group['from'] if c['slug'] not in disputed]
        if left and target not in seen:
            errors.append(f'patches: give {target} its patch from {", ".join(left)}, or dispute them')


def _incomplete(at: str, wanted: list, carried: list, replace: list, already, total: int, target: str) -> list[str]:
    """Carry completeness (design section 4.1, H7): every proposition in ``wanted`` is carried, used in
    ``replace``, or named in ``already`` with the proposition of ``target`` (P1-P``total``) that says it. The size
    of the target is no reason to leave one out: the program splits a patch that does not fit."""
    errors, named = [], set()
    for k, row in enumerate(already or []):
        text, ref = (row.get('text'), row.get('as')) if isinstance(row, dict) else (None, None)
        number = int(ref[1:]) if isinstance(ref, str) and re.fullmatch(r'P[1-9]\d*', ref) else 0
        if text not in wanted:
            errors.append(f'{at}.already[{k}].text must be one of the propositions it would carry, copied exactly')
        elif not 1 <= number <= total:
            errors.append(f'{at}.already[{k}].as must name the proposition of {target} that says it (P1–P{total})')
        else:
            named.add(text)
    covered = set(carried) | {r.get('by') for r in replace if isinstance(r, dict)} | named
    left = [p for p in wanted if p not in covered]
    if left:
        errors.append(f'{at} leaves out {left[0]!r}' + (f' and {len(left) - 1} more' if len(left) > 1 else '')
                      + f': carry every proposition, use it in replace, or name in already the proposition of '
                        f'{target} that says it ({{"text": ..., "as": "P<n>"}}). Never leave one out for the size of '
                        f'{target}: the program splits what does not fit')
    return errors


def exact(a, b) -> bool:
    """The same words once spaces are put right. A reason to send an item back (a question asked again, a
    proposition the card has); what the program leaves out by itself is decided by :func:`repeat` alone."""
    return _spaced(a) == _spaced(b)


def _key(item):
    """What :func:`repeat` compares: a proposition's words, spaces put right; a review card's question and answer."""
    if isinstance(item, dict):
        return 'flashcard', _spaced(item.get('q')), _spaced(item.get('a'))
    return 'proposition', _spaced(item)


def repeat(a, b) -> bool:
    """The one test of a repeat the program leaves out by itself (decision D-21): two propositions that are the same
    words once spaces are put right, or two review cards (``{"q", "a"}``) whose question and answer both are.

    Every place that leaves an item out without asking (a patch into an existing card, a carry into a card of the
    source, an item a patch or carry holds twice) asks this, and nothing else. Anything else the comparison finds
    the same goes back to the merge check (:func:`_said_twice`), never out; and :func:`lost` checks before a card
    is written that nothing else was left out.
    """
    return _key(a) == _key(b)


def lost(propositions: list, flashcards: list, patch: dict) -> list[str]:
    """What of ``patch`` (``propositions``, ``flashcards``, the ``by`` of each ``replace``) a card that took it does
    not hold, by the rule of :func:`repeat`; each as a message naming it. Empty when every item is there.

    The check the program runs before it writes a card that took a patch or a carry: an item is in the card, or
    the same as one of its items. It does not call :func:`repeat`, so a fault there cannot pass it.
    """
    have = {_key(p) for p in propositions or []} | {_key(f) for f in flashcards or [] if isinstance(f, dict)}
    out = []
    for text in list(patch.get('propositions') or []) + [r.get('by') for r in patch.get('replace') or []
                                                         if isinstance(r, dict)]:
        if _key(text) not in have:
            out.append(f'proposition {text!r}')
    for card in patch.get('flashcards') or []:
        if isinstance(card, dict) and _key(card) not in have:
            out.append(f'review card {card.get("q")!r} ({card.get("a")!r})')
    return out


def _said_twice(at: str, target: str, old: dict, added: list, replace: list, flashcards: list, differs,
                exact_ok: bool = False) -> list[str]:
    """A patch (or a carry into a card of the source) must not make ``target`` say a point twice (H7 4, decision
    D-14): each carried proposition (and each replacement, against the card's other propositions) and each carried
    question is compared with the card, and with the other ones it carries.

    A program finding is only a reason to ask again, never to leave anything out (decision D-21). The same words
    once spaces are put right (:func:`exact`) always go back. Words the comparison finds the same once marks, case
    and width are set aside (:func:`same_text`), and close wordings (:func:`near_same`), go back unless ``differs``
    names the pair and says why it is another point; ``from`` names a proposition (``P3``) or review card (``Q2``)
    of ``target``, or another item this one carries, copied exactly. A review card that asks a question of
    ``target`` (or another one it carries) in the same words with another answer goes back too, both answers named,
    and ``differs`` may keep it (for two of its own, ``from`` is the question itself). Only a :func:`repeat` is left
    out by the program: two of the same items it carries become one, and with ``exact_ok`` (a carry into a card of
    the source) an item ``target`` has is not sent back but left out when the program merges.
    """
    props = [str(p) for p in old.get('propositions') or []]
    old_flashcards = [f for f in old.get('flashcards') or [] if isinstance(f, dict)]
    questions = [str(f.get('q')) for f in old_flashcards]
    errors, allowed = [], set()
    bys = [r.get('by') for r in replace if isinstance(r, dict) and isinstance(r.get('by'), str)]
    own_cards = [f for f in flashcards if isinstance(f, dict) and isinstance(f.get('q'), str)]
    asked = [f['q'] for f in own_cards]
    carried = set(added) | set(bys) | set(asked)
    for k, row in enumerate(differs or []):
        text, ref, why = (row.get('text'), row.get('from'), row.get('reason')) if isinstance(row, dict) else (None,) * 3
        kind, number = (ref[0], int(ref[1:])) if isinstance(ref, str) and re.fullmatch(r'[PQ][1-9]\d*', ref) else ('', 0)
        if text not in carried:
            errors.append(f'{at}.differs[{k}].text must be a proposition, replacement or question of this patch, '
                          'copied exactly')
        elif not (1 <= number <= len(props if kind == 'P' else questions if kind == 'Q' else [])
                  or (ref in carried and (ref != text or asked.count(text) > 1))):
            errors.append(f'{at}.differs[{k}].from must name a proposition (P1–P{len(props)}) or review card '
                          f'(Q1–Q{len(questions)}) of {target}, or another item of this patch copied exactly')
        elif not isinstance(why, str) or not why.strip():
            errors.append(f'{at}.differs[{k}]: say why it is another point than {ref}')
        else:
            allowed |= {(text, ref), (ref, text)}

    def against(text, olds, skip=None):
        for how, same in (('exact', exact), ('same', same_text), ('near', near_same)):
            for n, other in enumerate(olds, 1):
                if n != skip and same(text, other):
                    return n, how
        return None, None

    def looks(how, n):
        return f'looks the same as {target} P{n}' if how == 'same' else f'may say again what {target} P{n} says'
    for text in added:
        if exact_ok and any(repeat(text, p) for p in props):
            continue  # the card says it already: the program leaves it out when it merges
        n, how = against(text, props)
        if how == 'exact':
            errors.append(f'{at}.propositions: {text!r} says what {target} P{n} says word for word ({props[n - 1]!r}): '
                          f'name it in already as P{n}')
        elif how and (text, f'P{n}') not in allowed:
            errors.append(f'{at}.propositions: {text!r} {looks(how, n)} ({props[n - 1]!r}): when it says '
                          f'more, put it in replace for P{n}; when it says the same, name it in already; when it makes '
                          f'another point (a symbol, a case, a mark that changes what it says), list it in differs '
                          f'({{"text": ..., "from": "P{n}", "reason": ...}})')
    for k, swap in enumerate(replace):
        by, ref = (swap.get('by'), swap.get('proposition')) if isinstance(swap, dict) else (None, None)
        skip = int(ref[1:]) if isinstance(ref, str) and re.fullmatch(r'P[1-9]\d*', ref) else None
        n, how = against(by, props, skip) if isinstance(by, str) else (None, None)
        if how == 'exact' and exact_ok:
            continue
        if how == 'exact' or (how and (by, f'P{n}') not in allowed):
            errors.append(f'{at}.replace[{k}].by {"says" if how == "exact" else "may say"} again what {target} P{n} '
                          f'says ({props[n - 1]!r}): replace P{n} instead, or leave that part out'
                          + ('' if how == 'exact' else ', or list it in differs with "from": "P' + str(n) + '"'))
    for carried_card in own_cards:
        question, answer = carried_card['q'], carried_card.get('a')
        if exact_ok and any(repeat(carried_card, f) for f in old_flashcards):
            continue  # the same question and answer: the program leaves it out when it merges
        n, how = against(question, questions)
        if how == 'exact' and exact(answer, old_flashcards[n - 1].get('a')):
            errors.append(f'{at}.flashcards: {question!r} is {target} Q{n}, answer and all: leave it out')
        elif how == 'exact' and (question, f'Q{n}') not in allowed:
            errors.append(f'{at}.flashcards: {question!r} asks what {target} Q{n} asks, but their answers differ '
                          f'({old_flashcards[n - 1].get("a")!r} and {answer!r}): '
                          + ('carry one complete pair' if exact_ok else f'leave it out when Q{n} answers it')
                          + f', or list it in differs with "from": "Q{n}" and why both answers are needed')
        elif how in ('same', 'near') and (question, f'Q{n}') not in allowed:
            errors.append(f'{at}.flashcards: {question!r} {"looks the same as" if how == "same" else "may ask again what"}'
                          f' {target} Q{n}{"" if how == "same" else " asks"} ({questions[n - 1]!r}): leave it out, or '
                          f'list it in differs with "from": "Q{n}" and why it asks something else')
    # within what it carries: an item twice (a repeat) is carried once, by the program; two that only look the same
    # go back, as against the card (they used to be left out silently, K4''), and so does a question asked twice
    # with two answers (it was left out silently on the patch path, K4''')
    group = list(added) + bys
    for i, a in enumerate(group):
        for b in group[:i]:
            if not repeat(a, b) and same_text(a, b) and (a, b) not in allowed:
                errors.append(f'{at}.propositions: {a!r} looks the same as {b!r}, which it carries too: carry one of '
                              f'them, or list it in differs ({{"text": {a!r}, "from": {b!r}, "reason": ...}})')
    for i, card in enumerate(own_cards):
        for other in own_cards[:i]:
            a, b = card['q'], other['q']
            if repeat(card, other) or (a, b) in allowed:
                continue
            if exact(a, b):
                errors.append(f'{at}.flashcards: {a!r} is asked twice, with two answers ({other.get("a")!r} and '
                              f'{card.get("a")!r}): carry one complete pair, or list it in differs ({{"text": {a!r}, '
                              f'"from": {b!r}, "reason": ...}}) and why both answers are needed')
            elif same_text(a, b):
                errors.append(f'{at}.flashcards: {a!r} looks the same as {b!r}, which it carries too: carry one of '
                              f'them, or list it in differs ({{"text": {a!r}, "from": {b!r}, "reason": ...}})')
    return errors


def _row_items(rows: dict, slug: str, key: str) -> list:
    """What a card judged against the source carries (its ``carry``), for a patch that takes it on."""
    return list(((rows.get(slug) or {}).get('carry') or {}).get(key) or [])


def link_candidates(card: dict, frozen: dict | None = None) -> list:
    """The existing cards a card of the frozen input may link, each with its view from ``candidate_cards``
    (inputs frozen before it carry the view in the row; before ``candidates``, the rows are ``near_existing``)."""
    table = (frozen or {}).get('candidate_cards') or {}
    rows = [{'id': e} if isinstance(e, str) else e for e in card.get('candidates', card.get('near_existing')) or []]
    return [{**table.get(e.get('id'), {}), **e} for e in rows if isinstance(e, dict)]


def _final_domains(frozen: dict, topics: dict) -> dict:
    """Each card's domain once the topic decisions are applied."""
    place = {t['id']: t for t in frozen['new_topics']}
    moved = {(place[i]['domain'], place[i]['pillar'], place[i]['name']): place[r['with']]['domain']
             for i, r in topics.items() if r['overlap'] == 'same' and r.get('with') in place}
    return {c['slug']: moved.get((c['domain'], c['pillar'], c['topic']), c['domain']) for c in frozen['cards']}


def _card_rows(items, cards: dict, errors: list, also: dict | None = None) -> dict:
    """The answer's card rows by slug, each with a level against the source and a fitting ``with``.

    ``also``: the patch set's cards, which ``with`` may name (a card of the source that extends or repeats one of
    them goes, with it, into its existing card) but which are not judged here.
    """
    also = also or {}
    out = {}
    for i, row in enumerate(items):
        at = f'cards[{i}]'
        if not isinstance(row, dict):
            errors.append(f'{at} must be an object')
            continue
        slug, level = row.get('slug'), row.get('overlap')
        if slug not in cards:
            errors.append(f'{at}.slug {slug!r} is not in the packet (a card the chunk reviewer found the same as an '
                          'existing card, or one that extends an existing card, is not judged here)')
            continue
        if slug in out:
            errors.append(f'{at}: {slug} has more than one level')
            continue
        ids = row.get('with') or []
        problem = with_problem(level, ids)
        if problem:
            errors.append(f'{at}: {problem}')
            continue
        if level != 'distinct':
            stray = [x for x in ids if (x not in cards and x not in also) or x == slug]
            if stray:
                errors.append(f'{at}.with must name other cards of this packet (slugs), not {", ".join(stray)}')
                continue
            if not isinstance(row.get('reason'), str) or not row['reason'].strip():
                errors.append(f'{at}.reason must say what the two share')
                continue
        carry = row.get('carry') or {}
        carried = any(carry.get(k) for k in ('propositions', 'flashcards', 'replace'))
        if level == 'extends' and not carried:
            errors.append(f'{at}: "extends" carries what the card adds: give a non-empty "carry" (or judge it "same")')
            continue
        if level != 'extends' and carried:
            errors.append(f'{at}: only an "extends" card carries; "{level}" takes no "carry"')
            continue
        out[slug] = {**row, 'with': list(ids) if level != 'distinct' else []}
    return out


def _topic_rows(items, known: set, errors: list) -> dict:
    out = {}
    for i, row in enumerate(items):
        at = f'new_topics[{i}]'
        if not isinstance(row, dict):
            errors.append(f'{at} must be an object')
            continue
        tid, level, other = row.get('id'), row.get('overlap'), row.get('with')
        if tid not in known:
            errors.append(f'{at}.id {tid!r} is not in the packet')
        elif tid in out:
            errors.append(f'{at}: {tid} has more than one level')
        elif level not in TOPIC_LEVELS:
            errors.append(f'{at}.overlap must be one of {", ".join(TOPIC_LEVELS)}')
        elif level == 'distinct' and other:
            errors.append(f'{at}: "distinct" takes no "with"')
        elif level == 'same' and (other not in known or other == tid):
            errors.append(f'{at}.with must name another new topic of the packet')
        elif level == 'same' and (not isinstance(row.get('reason'), str) or not row['reason'].strip()):
            errors.append(f'{at}.reason must say why the two are one subject')
        else:
            out[tid] = row
    for tid, row in out.items():
        partner = out.get(row.get('with'))
        if row['overlap'] == 'same' and partner is not None and partner['overlap'] == 'same':
            errors.append(f'new_topics: {tid} names {row["with"]}, which is itself "same"; name the topic that stays')
    return out


def salvage(frozen: dict, payload: Any, closed: Callable[[str, str, str], str | None] | None = None
            ) -> dict | None:
    """The last answer the merge check may give, kept patch by patch (decision D-20), or None.

    When the answer fails the program checks only in some of its patches, those patches are held back: the answer
    without them is checked again with their existing cards taken out of the patch set, and when it passes it is
    the answer, with ``held`` naming each held existing card, its cards and the problems found in its patch (the
    lines of the refusal that name it). Nothing of a held patch is written into its card; its cards are written
    next to it for the owner. Any problem outside the patches, or one the answer without them still has, gives
    None: the answer is refused as before.
    """
    try:
        validate_answer(frozen, payload, closed)
        return payload
    except Refused as exc:
        lines = exc.problems
    except ValueError:  # the shape, or the answer's frame
        return None
    if not isinstance(payload.get('patches'), list):
        return None
    patches = payload['patches']
    bad = {int(n) for line in lines for n in re.findall(r'^patches\[(\d+)\]', line)}
    groups = {g['target']: g for g in frozen.get('patch_set') or []}
    if not bad or any(i >= len(patches) or not isinstance(patches[i], dict)
                      or patches[i].get('target') not in groups for i in bad):
        return None
    held = [{'target': patches[i]['target'], 'cards': [c['slug'] for c in groups[patches[i]['target']]['from']],
             'problems': [line for line in lines if line.startswith(f'patches[{i}]')]} for i in sorted(bad)]
    gone = {h['target'] for h in held}
    held_cards = {c['slug'] for target in gone for c in groups[target]['from']}
    # A dispute names a patch-set card, not its existing target. Once that card's patch is held back, its dispute
    # must go too: the card is already waiting for the owner, and the reduced answer no longer has that patch set.
    kept = {**payload, 'patches': [p for i, p in enumerate(patches) if i not in bad],
            'disputes': [row for row in payload.get('disputes') or []
                         if not isinstance(row, dict) or row.get('slug') not in held_cards]}
    try:
        validate_answer({**frozen, 'patch_set': [g for g in frozen.get('patch_set') or [] if g['target'] not in gone]},
                        kept, closed)
    except ValueError:
        return None
    return {**kept, 'held': held}


def record(frozen: dict, answer: dict, entries: list, *, slot: str, run: str | None) -> None:
    """Put the answer's values in the decision record: each card's level against the source, each topic's, the links."""
    topic_of = {t['id']: topic_key(t['domain'], t['pillar'], t['name']) for t in frozen['new_topics']}
    number = int(slot.rsplit('.r', 1)[1]) if '.r' in slot else 0
    if number:  # run again after a dispute: this round replaces all of the one before
        decisions.supersede_round(entries, role=ROLE, slot=slot, earlier_slot=round_slot(number - 1), reason='dispute')
    for row in answer['cards']:
        data = {'overlap': row['overlap'], 'with': list(row.get('with') or []) if row['overlap'] != 'distinct' else [],
                'reason': row.get('reason')}
        if row['overlap'] == 'extends':
            data['carry'] = row.get('carry') or {}
        if row.get('alone'):
            data['alone'] = row['alone']
        decisions.record(entries, 'overlap.source', row['slug'], data, role=ROLE, slot=slot, run=run)
        for link in row.get('links') or []:
            relation = {k: v for k, v in (('type', link['type']), ('target', link['target']),
                                          ('note', link.get('note'))) if v}
            decisions.step(entries, 'relations', row['slug'], 'add', role=ROLE, slot=slot, run=run,
                           data={'relation': relation})
    for row in answer['new_topics']:
        decisions.record(entries, 'topic.same', topic_of[row['id']],
                         {'overlap': row['overlap'], 'with': topic_of.get(row.get('with')), 'reason': row.get('reason')},
                         role=ROLE, slot=slot, run=run)
    for row in answer.get('patches') or []:
        decisions.record(entries, 'patch', row['target'],
                         {**{k: row.get(k) or [] for k in ('propositions', 'flashcards', 'replace')},
                          **{k: row[k] for k in ('already', 'differs') if row.get(k)}},
                         role=ROLE, slot=slot, run=run)
    for row in answer.get('disputes') or []:
        decisions.record(entries, 'dispute', row['slug'], {'reason': row['reason']}, role=ROLE, slot=slot, run=run)
    for row in answer.get('held') or []:  # D-20: the last answer, kept patch by patch
        decisions.record(entries, 'patch.held', row['target'], {'cards': row['cards'], 'problems': row['problems']},
                         role=ROLE, slot=slot, run=run)


def apply(settled: list[dict], frozen: dict, entries: list) -> tuple[list[dict], dict]:
    """``(settled, report)`` with the merge check's final values applied; nothing in ``settled`` is changed in place.

    The values come only from the decision record (``entries``); the
    relations and taxonomy places the merge changes are recorded there as
    program steps (``move``, ``redirect``, ``filter``, ``topic_merge_refile``), and
    the write step reads them back with :func:`secondbrain.decisions.fold`.

    - ``extends``: the card is not written. Its carried propositions and
      flashcards, and its source quotes, are added to the card in ``with``;
      ``replace`` swaps one of that card's propositions for this card's fuller one.
    - ``same``: the card is not written and nothing is carried.
    - Either way, relations to it point to the card it went to, and its own
      relations move there, except where that card already links the target
      (in either direction) or would link itself.
    - a topic ``same`` as another: its cards are filed under that topic (domain,
      pillar and topic), and it is not added to the taxonomy.
    """
    judged = {(c['chunk'], c['slug']) for c in frozen['cards']}
    now = {(o['chunk'], s) for o in settled for s in o['accepted']}
    patch_set = {(c['chunk'], c['slug']) for g in frozen.get('patch_set') or [] for c in g['from']}
    if judged != now or patch_set != {(o['chunk'], s) for o in settled for s in o.get('repeats') or {}}:
        raise ValueError('the merge check judged a different set of cards than the chunks now accept')
    levels = decisions.values(entries, 'overlap.source')
    lacking = sorted(s for _, s in judged if s not in levels)
    if lacking:
        raise ValueError(f'the decision record has no level against the source for {", ".join(lacking)}')
    rows = {c['slug']: levels[c['slug']]['data'] for c in frozen['cards']}  # in the frozen order: one result
    merged = {s: r['with'][0] for s, r in rows.items() if r['overlap'] != 'distinct'}
    topic_rows = decisions.values(entries, 'topic.same')
    place_of = {topic_key(t['domain'], t['pillar'], t['name']): (t['domain'], t['pillar'], t['name'])
                for t in frozen['new_topics']}
    refile = {place_of[k]: place_of[e['data']['with']] for k, e in topic_rows.items()
              if k in place_of and e['data']['overlap'] == 'same'}
    original = {s: c for o in settled for s, c in o['accepted'].items()}
    repeats = {s: c for o in settled for s, c in (o.get('repeats') or {}).items()}  # they go into existing cards
    kept = {s: dict(c) for o in settled for s, c in o['accepted'].items() if s not in merged}
    for slug in kept:  # the cards of a topic merged into another are filed under that one
        key = tuple(decisions.fold(entries, 'classification', slug).get(k) for k in ('domain', 'pillar', 'topic'))
        if key in refile:
            target = dict(zip(('domain', 'pillar', 'topic'), refile[key]))
            decisions.step(entries, 'classification', slug, 'topic_merge_refile', role='program', slot=SLOT,
                           data={**target, 'from': list(key)})
    carried, skipped_by, replacements, too_large = {}, {}, {}, {}
    for slug, into in merged.items():  # what an extends card adds goes to the card it extends
        carry = rows[slug].get('carry') or {}
        if into not in kept:  # a card of the patch set: what goes into its existing card is that card's patch
            carried[slug], replacements[slug] = (0, 0), 0
            continue
        keep = kept[into]
        props, flashcards = list(keep.get('propositions') or []), list(keep.get('flashcards') or [])
        if len(props) + len(carry.get('propositions') or []) > MAX_PROPOSITIONS or \
                len(flashcards) + len(carry.get('flashcards') or []) > MAX_FLASHCARDS:
            # it does not fit: the card it extends stays as it is and this one goes to a split slot (D-15)
            too_large[slug] = {'into': into, 'carry': carry}
            carried[slug], replacements[slug] = (0, 0), 0
            continue
        new_p, new_f, skipped = [], [], 0
        for swap in carry.get('replace') or []:  # this card's fuller wording takes the place of the kept one
            number = int(swap['proposition'][1:])
            props[number - 1] = swap['by']
            new_p.append(number)
        # only a repeat (:func:`repeat`) is left out here; anything the comparison only finds the same went back to
        # the merge check at validation (decision D-21)
        for text in carry.get('propositions') or []:
            if any(repeat(text, x) for x in props):  # the kept card says it already, word for word
                skipped += 1
                continue
            props.append(text)
            new_p.append(len(props))
        for f in original[slug].get('flashcards') or []:
            if {'q': f.get('q'), 'a': f.get('a')} not in (carry.get('flashcards') or []):
                continue
            if any(repeat(f, x) for x in flashcards):
                skipped += 1
                continue
            flashcards.append(f)
            new_f.append(len(flashcards))
        missing = lost(props, flashcards, carry)
        if missing:  # before the card is written: nothing a carry holds may go missing on the way (decision D-25)
            raise ValueError(f'{slug} carries into {into} what {into} would not hold once merged: '
                             f'{"; ".join(missing)}; nothing was written')
        keep['propositions'], keep['flashcards'] = props, flashcards
        own = (original[slug].get('source_quotes') or [None])[0]
        keep['source_quotes'] = list(dict.fromkeys(list(keep.get('source_quotes') or [])
                                                   + list(original[slug].get('source_quotes') or [])))
        if (new_p or new_f) and own and own != (keep['source_quotes'] or [None])[0]:
            # where the carried items stand in the source, so they stay traceable (the write step adds it
            # to the card's `sources`)
            keep['_carried'] = list(keep.get('_carried') or []) + [
                {'locator': own, 'propositions': new_p, 'flashcards': new_f}]
        carried[slug], skipped_by[slug] = (len(new_p), len(new_f)), skipped
        replacements[slug] = len(carry.get('replace') or [])
    moved = _merge_relations(entries, kept, merged, repeats)
    added = [{'from': e['object'], 'type': e['data']['relation']['type'], 'target': e['data']['relation']['target']}
             for e in entries if e['kind'] == 'step' and e['value'] == 'relations' and e['role'] == ROLE
             and e['n'] not in decisions.superseded(entries) and e['object'] in kept
             and any(r['target'] == e['data']['relation']['target'] and r['type'] == e['data']['relation']['type']
                     for r in decisions.fold(entries, 'relations', e['object']))]
    # [[links]] in the text of the cards that stay go to the card a merged card went to (J4: a contrast line
    # still named a merged card); a link that would point at the card itself becomes plain text
    retitle = {original[s]['title']: (kept.get(into) or repeats[into])['title'] for s, into in merged.items()}
    rewritten = sum(_retitle_card(card, retitle) for card in kept.values()) if retitle else 0
    for slug in kept if retitle else ():  # and in the notes of their relations
        for r in decisions.fold(entries, 'relations', slug):
            note, count = _retitle(r.get('note'), retitle, kept[slug].get('title'))
            if count:
                rewritten += count
                decisions.step(entries, 'relations', slug, 'renote', role='program', slot=SLOT,
                               data={'target': r['target'], 'type': r['type'], 'note': note})
    out = []
    for o in settled:
        accepted = {s: kept[s] for s in o['accepted'] if s in kept}
        topics = {k: v for k, v in o['topics'].items() if k not in refile}
        split_merges = {s: {**original[s], '_with': too_large[s]['into'], '_carry': too_large[s]['carry']}
                        for s in o['accepted'] if s in too_large}
        out.append({**o, 'accepted': accepted, 'topics': topics, 'split_merges': split_merges})
    by_key = {(t['domain'], t['pillar'], t['name']): t for t in frozen['new_topics']}
    report = {
        'status': 'checked', 'cards': len(frozen['cards']),
        'merged': [{'slug': s, 'title': original[s]['title'], 'into': into, 'reason': rows[s]['reason'],
                    'carried': {'propositions': carried[s][0], 'flashcards': carried[s][1]},
                    **({'replaced': replacements[s]} if replacements[s] else {}),
                    **({'repeats_skipped': skipped_by[s]} if skipped_by.get(s) else {}),
                    **({'already': list((rows[s].get('carry') or {}).get('already') or [])}
                       if (rows[s].get('carry') or {}).get('already') else {})}
                   for s, into in merged.items() if s not in too_large],
        'too_large': {s: v['into'] for s, v in too_large.items()},
        'rewritten_links': rewritten,
        'added_links': added,
        'alone': [{'slug': s, 'reason': rows[s]['alone']} for s in kept if rows[s].get('alone')],
        'refiled_topics': [{'from': dict(zip(('domain', 'pillar', 'name'), k)),
                            'into': dict(zip(('domain', 'pillar', 'name'), v)),
                            'cards': sum(1 for c in original.values() if (c['domain'], c['pillar'], c['topic']) == k),
                            'reason': topic_rows[topic_key(*k)]['data'].get('reason')}
                           for k, v in refile.items() if k in by_key],
        'moved_relations': moved,
        'merged_into': dict(merged),
        'patches': {t: e['data'] for t, e in decisions.values(entries, 'patch').items()},
        'disputes': {s: e['data']['reason'] for s, e in decisions.values(entries, 'dispute').items()},
        'held': {t: e['data'] for t, e in decisions.values(entries, 'patch.held').items()}}
    return out, report


def _spaced(text) -> str:
    return ' '.join(str(text).split())


def _merge_relations(entries: list, kept: dict, merged: dict, repeats: dict | None = None) -> list:
    """Record the program steps that send the relations of merged cards, and the relations to them, to the card they
    went to; returns the moved ones (``from``, ``type``, ``target``, ``was_on``)."""
    def step(slug, op, data):
        decisions.step(entries, 'relations', slug, op, role='program', slot=SLOT, data=data)
    kept = {**kept, **{s: c for s, c in (repeats or {}).items() if s in set(merged.values())}}
    rels = {s: decisions.fold(entries, 'relations', s) for s in list(kept) + list(merged)}
    for slug in kept:  # links to a merged card go to the card it went to
        own = {r['target'] for r in rels[slug]}
        for r in rels[slug]:
            target = merged.get(r['target'])
            if target is None:
                continue
            if target == slug or target in own:
                step(slug, 'filter', {'target': r['target'], 'type': r['type'], 'why': 'its target was merged'})
                continue
            own.add(target)
            step(slug, 'redirect', {'target': r['target'], 'to': target, 'why': 'its target was merged'})
    moved = []
    for slug, into in merged.items():  # a merged card's own links move to the card it went to
        now = decisions.fold(entries, 'relations', into)
        for r in rels[slug]:
            target = merged.get(r['target'], r['target'])
            if target == into or any(x['target'] == target for x in now):
                continue
            if target in kept and any(x['target'] == into for x in decisions.fold(entries, 'relations', target)):
                continue  # a moved link must not make a pair point both ways
            relation = {**r, 'target': target}
            step(into, 'move', {'relation': relation, 'was_on': slug})
            now.append(relation)
            moved.append({'from': into, 'type': r['type'], 'target': target, 'was_on': slug})
    return moved


def _retitle(text, mapping: dict, own: str):
    """``text`` with its ``[[links]]`` to a key of ``mapping`` pointing at the mapped title; returns (text, count)."""
    if not isinstance(text, str):
        return text, 0
    count = 0

    def sub(m):
        nonlocal count
        title = mapping.get(m.group(1).strip())
        if title is None:
            return m.group(0)
        count += 1
        if title == own:
            return m.group(3)[1:] if m.group(3) else title
        return f'[[{title}{m.group(2)}{m.group(3)}]]'
    return _WIKILINK.sub(sub, text), count


def _retitle_card(card: dict, mapping: dict) -> int:
    """Rewrite the links in every text field of ``card`` in place; returns how many were changed."""
    own, total = card.get('title'), 0

    def fix(text):
        nonlocal total
        new, n = _retitle(text, mapping, own)
        total += n
        return new
    for key in ('essence', 'intuition'):
        if key in card:
            card[key] = fix(card[key])
    for key in ('propositions', 'confusions'):
        if isinstance(card.get(key), list):
            card[key] = [fix(x) for x in card[key]]
    if isinstance(card.get('flashcards'), list):
        card['flashcards'] = [{**f, 'q': fix(f.get('q')), 'a': fix(f.get('a'))} if isinstance(f, dict) else f
                              for f in card['flashcards']]
    return total

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

A retry writes the cards its parent left pending, and those missed the parent's
source check: a chunk reviewer leaves a repeat of another chunk's card to this
check, and by the retry that card is in the vault (the real-vault ingest: a
card whose points three written cards of its source already held was written
as confirmed). So in a retry each card also lists the closest cards earlier
jobs of the source wrote (``near_written``, with their propositions), and
``same`` may name one of them. A written card is not changed: the new card is
written next to it with ``status: needs-review`` and must link it.

The step runs when accepted cards come from two or more chunks, and in a retry
whenever a card is accepted and earlier jobs of the source wrote cards. Its
input is frozen as the slot's candidate (made by the program, not a producer),
so the answer is bound to exactly the cards it judged.
"""
from __future__ import annotations

import re
from typing import Any, Callable

SLOT = 'xc01'
STEP = 'source_check'
DECISIONS = ('keep', 'same')
TOPIC_DECISIONS = ('keep', 'same')
#: Limits a card keeps after cards are merged into it: the producer's 12 propositions, and twice its 4
#: flashcards, so a point carried over keeps its question (J4 on the drill 3 replay: a cap of 4 left 7 points
#: without any question in the vault).
MAX_PROPOSITIONS, MAX_FLASHCARDS = 12, 8
#: ``[[Title]]``, ``[[Title#heading]]`` and ``[[Title|alias]]`` in card text.
_WIKILINK = re.compile(r'\[\[([^\[\]|#]+)((?:#[^\[\]|]*)?)((?:\|[^\[\]]*)?)\]\]')
#: Existing cards shown per card as possible link targets (drill 3: the Euclid card was second).
NEAR_EXISTING = 3
#: Cards earlier jobs of the source wrote, shown per card of a retry by closeness (plus the ones it links).
NEAR_WRITTEN = 5

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
                    'flashcards': {'type': 'array', 'items': _FLASHCARD},
                    'replace': {'type': 'array', 'items': {
                        'type': 'object', 'required': ['proposition', 'by'],
                        'properties': {'proposition': _STR, 'by': _STR}}}}},
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


def needed(settled: list[dict], written: bool = False) -> bool:
    """True when accepted cards come from two or more chunks, or from one when ``written``.

    ``written``: the job is a retry and earlier jobs of the source wrote cards.
    """
    chunks = sum(1 for o in settled if o['accepted'])
    return chunks >= 2 or (written and chunks >= 1)


def frozen_input(settled: list[dict], source: dict, *, relation_views: dict,
                 near_existing: Callable[[dict], list[dict]] = lambda card: [],
                 near_written: Callable[[dict], list[dict]] | None = None) -> dict:
    """What the reviewer judges: every accepted card and new topic, sorted by where they are filed.

    ``near_existing(card)`` gives the existing cards (id, title, domain) a card
    may link; ``near_written(card)``, in a retry, the cards earlier jobs of the
    source wrote that are closest to it (id, title, domain, essence,
    propositions); ``relation_views`` the relation types and their meanings.
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
                      'near_existing': near_existing(c)[:NEAR_EXISTING],
                      **({'near_written': near_written(c)} if near_written else {})})
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
    written = {s: {e['id'] for e in c.get('near_written') or []} for s, c in cards.items()}
    # a retry card may link a written card by its slug: such a relation counts as a link to its id
    alias = {e['slug']: e['id'] for c in cards.values() for e in c.get('near_written') or [] if e.get('slug')}
    rows = _rows(payload['cards'], 'cards', 'slug', set(cards), DECISIONS, errors, also=written)
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
    merged = {s: r['with'] for s, r in rows.items() if r['decision'] == 'same' and r['with'] in cards}
    for slug, into in merged.items():
        if into in merged:
            errors.append(f'cards: {slug} names {into}, which is itself "same" as {merged[into]}; '
                          f'name {merged[into]} instead')
    # a card that repeats a card an earlier job of the source wrote: written next to it as needs-review
    written_repeats = {s: r['with'] for s, r in rows.items() if r['decision'] == 'same' and r['with'] not in cards}
    for slug, into in written_repeats.items():
        if any((rows[slug].get('carry') or {}).get(k) for k in ('propositions', 'flashcards', 'replace')):
            errors.append(f'cards: {slug} is "same" as the written card {into}, which is not changed; give no "carry"')
        linked_into = any(alias.get(r['target'], r['target']) == into for r in cards[slug]['relations']) or any(
            isinstance(x, dict) and x.get('target') == into for x in rows[slug].get('links') or [])
        if not linked_into:
            errors.append(f'cards: {slug} is "same" as the written card {into}; link it in "links" so the owner '
                          'sees the pair')
    # what each kept card ends with: its own propositions and flashcards plus what merged cards carry to it
    totals = {s: [len(c['propositions']), len(c['flashcards'])] for s, c in cards.items()}
    replaced = set()
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
        for i, swap in enumerate(carry.get('replace') or []):
            at = f'cards: {slug}.carry.replace[{i}]'
            ref, by = (swap.get('proposition'), swap.get('by')) if isinstance(swap, dict) else (None, None)
            number = int(ref[1:]) if isinstance(ref, str) and re.fullmatch(r'P[1-9]\d*', ref) else 0
            if into in cards and not 1 <= number <= len(cards[into]['propositions']):
                errors.append(f'{at}.proposition must name one of the propositions of {into} (P1–P'
                              f'{len(cards[into]["propositions"])})')
            elif (into, number) in replaced:
                errors.append(f'{at}: {into} {ref} is replaced twice')
            if by not in cards[slug]['propositions'] or by in props:
                errors.append(f'{at}.by must be one of its propositions, copied exactly, and not also in '
                              'carry.propositions')
            replaced.add((into, number))
        if into in totals:
            totals[into][0] += len(props)
            totals[into][1] += len(flashcards)
    for slug, (props, flashcards) in totals.items():
        if props > MAX_PROPOSITIONS or flashcards > MAX_FLASHCARDS:
            errors.append(f'cards: {slug} would have {props} propositions and {flashcards} flashcards with what is '
                          f'carried to it; at most {MAX_PROPOSITIONS} and {MAX_FLASHCARDS}. Carry fewer')
    domain = _final_domains(frozen, topics)
    linked = {(s, alias.get(r['target'], r['target'])) for s, c in cards.items() for r in c['relations']}
    added = set()
    for slug, row in rows.items():
        links = row.get('links') or []
        if links and slug in merged:
            errors.append(f'cards: {slug} is "same"; put its links on {merged[slug]}')
            continue
        existing = {e['id']: e.get('domain')
                    for e in list(cards[slug]['near_existing']) + list(cards[slug].get('near_written') or [])}
        for i, link in enumerate(links):
            at = f'cards: {slug}.links[{i}]'
            target, kind = link.get('target') if isinstance(link, dict) else None, (link or {}).get('type')
            if target in merged:
                errors.append(f'{at}: {target} is "same" as {merged[target]}; link {merged[target]}')
                continue
            if target == slug or (target not in cards and target not in existing):
                errors.append(f'{at}.target must be another card of the packet or one of its near_existing'
                              + (' or near_written' if written[slug] else '') + ' ids')
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


def _rows(items, where, key, known, decisions, errors, also=None) -> dict:
    """Rows by ``key``; ``also`` maps a name to the further values its ``with`` may take (written cards)."""
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
            extra = (also or {}).get(name) or set()
            if (other not in known and other not in extra) or other == name:
                errors.append(f'{at}.with must name another {key} of the packet'
                              + (' or one of its near_written ids' if extra else ''))
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
    - ``same`` as a card an earlier job of the source wrote (a retry): that
      card is not changed. This card is written with ``status: needs-review``
      (``_overlap_with``, as for a chunk reviewer's ``needs_review``) and keeps
      its links.
    - ``links``: added to the card, after its own relations.
    - a topic ``same`` as another: its cards are filed under that topic (domain,
      pillar and topic), and it is not added to the taxonomy.
    """
    judged = {(c['chunk'], c['slug']) for c in frozen['cards']}
    now = {(o['chunk'], s) for o in settled for s in o['accepted']}
    if judged != now:
        raise ValueError('the source check judged a different set of cards than the chunks now accept')
    rows = {r['slug']: r for r in answer['cards']}
    slugs = {c['slug'] for c in frozen['cards']}
    merged = {s: r['with'] for s, r in rows.items() if r['decision'] == 'same' and r['with'] in slugs}
    written_repeats = {s: r['with'] for s, r in rows.items() if r['decision'] == 'same' and r['with'] not in slugs}
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
            if slug in written_repeats:
                card.update(_overlap_with=written_repeats[slug], _overlap_reason=rows[slug]['reason'])
            kept[slug] = card
    carried, repeats, replacements = {}, {}, {}
    for slug, into in merged.items():  # what the merged card adds goes to the card it repeats
        carry = rows[slug].get('carry') or {}
        keep = kept[into]
        props, flashcards = list(keep.get('propositions') or []), list(keep.get('flashcards') or [])
        new_p, new_f, skipped = [], [], 0
        for swap in carry.get('replace') or []:  # this card's fuller wording takes the place of the kept one
            number = int(swap['proposition'][1:])
            props[number - 1] = swap['by']
            new_p.append(number)
        have_p, have_q = {_plain(x) for x in props}, {_plain(f.get('q')) for f in flashcards}
        for text in carry.get('propositions') or []:
            if _plain(text) in have_p:  # the kept card says it already, word for word
                skipped += 1
                continue
            have_p.add(_plain(text))
            props.append(text)
            new_p.append(len(props))
        for f in original[slug].get('flashcards') or []:
            if {'q': f.get('q'), 'a': f.get('a')} not in (carry.get('flashcards') or []):
                continue
            if _plain(f.get('q')) in have_q:
                skipped += 1
                continue
            have_q.add(_plain(f.get('q')))
            flashcards.append(f)
            new_f.append(len(flashcards))
        keep['propositions'], keep['flashcards'] = props, flashcards
        own = (original[slug].get('source_quotes') or [None])[0]
        keep['source_quotes'] = list(dict.fromkeys(list(keep.get('source_quotes') or [])
                                                   + list(original[slug].get('source_quotes') or [])))
        if (new_p or new_f) and own and own != (keep['source_quotes'] or [None])[0]:
            # where the carried items stand in the source, so they stay traceable (the write step adds it
            # to the card's `sources`)
            keep.setdefault('_carried', []).append({'locator': own, 'propositions': new_p, 'flashcards': new_f})
        carried[slug], repeats[slug] = (len(new_p), len(new_f)), skipped
        replacements[slug] = len(carry.get('replace') or [])
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
    # [[links]] in the text of the cards that stay go to the card a merged card went to (J4: a contrast line
    # still named a merged card); a link that would point at the card itself becomes plain text
    retitle = {original[s]['title']: kept[into]['title'] for s, into in merged.items()}
    rewritten = sum(_retitle_card(card, retitle) for card in kept.values()) if retitle else 0
    out = []
    for o in settled:
        accepted = {s: kept[s] for s in o['accepted'] if s in kept}
        topics = {k: v for k, v in o['topics'].items() if k not in refile}
        out.append({**o, 'accepted': accepted, 'topics': topics})
    report = {
        'status': 'checked', 'cards': len(frozen['cards']),
        'merged': [{'slug': s, 'title': original[s]['title'], 'into': into, 'reason': rows[s]['reason'],
                    'carried': {'propositions': carried[s][0], 'flashcards': carried[s][1]},
                    **({'replaced': replacements[s]} if replacements[s] else {}),
                    **({'repeats_skipped': repeats[s]} if repeats[s] else {})}
                   for s, into in merged.items()],
        'rewritten_links': rewritten,
        'same_as_written': [{'slug': s, 'title': original[s]['title'], 'with': into, 'reason': rows[s]['reason']}
                            for s, into in written_repeats.items()],
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


def _plain(text) -> str:
    """Text without spaces and punctuation, case folded: what counts as saying the same words."""
    return re.sub(r'[\W_]+', '', str(text or '')).casefold()


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
    for r in card.get('relations') or []:
        if r.get('note'):
            r['note'] = fix(r['note'])
    return total

"""`sb advise`: a model's advice on whether one large pillar should split, and how; the owner decides.

Storage keeps three levels (domain, pillar, card; :mod:`secondbrain.levels`) and grows sideways: a pillar that grew
past the owner's reference line (:data:`secondbrain.topics.PILLAR_REVIEW_AT`) can become several pillars of its
domain, or part of it can leave for a new domain; or it stays whole. Which of the three is right depends on what
the cards say, which the program cannot judge, so a model judges it from frozen evidence and another model reviews
the judgement. Nothing is split here: the advice is kept with the job and shown by `sb status`; a split, when the
owner wants one, goes through the commands that write (`sb curate`, `sb domcreate`).

The evidence (:func:`prepare`) is the program's: the domain's question and its pillars, the pillar's ``why``, its
cards grouped by their relations (the groups of the split proposals, :func:`secondbrain.topics.partition`), and for
each group how many of its links stay in the group, go to the rest of the pillar, to other pillars and to other
domains. The job id is made from that evidence, so `sb status` finds the advice again until the pillar's cards or
links change.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import config, jobs
from .cards import essence_of
from .schemas import AnswerError
from .store import digest
from .vault import read_snapshot

KIND = 'advice'
METHOD = 'advice/3'
DECISIONS = ('split_pillars', 'new_domain', 'keep')
#: Characters: (argument min, argument max, name max, why max), per card language.
LIMITS = {'zh': (20, 500, 16, 80), 'en': (60, 1400, 48, 240)}
#: Characters of a card's essence shown in the evidence.
ESSENCE = 90

SCHEMA = {
    'type': 'object', 'required': ['decision', 'argument'], 'additionalProperties': False,
    'properties': {
        'decision': {'enum': list(DECISIONS)},
        'argument': {'type': 'string'},
        'pillars': {'type': 'array', 'items': {
            'type': 'object', 'required': ['name', 'why', 'groups'], 'additionalProperties': False,
            'properties': {'name': {'type': 'string'}, 'why': {'type': 'string'},
                           'groups': {'type': 'array', 'items': {'type': 'string'}}}}},
        'new_domain': {'type': 'object', 'required': ['groups', 'name', 'question'], 'additionalProperties': False,
                       'properties': {'groups': {'type': 'array', 'items': {'type': 'string'}},
                                      'name': {'type': 'string'}, 'question': {'type': 'string'}}},
    }}
REVIEW_SCHEMA = {
    'type': 'object', 'required': ['items'], 'additionalProperties': False,
    'properties': {'items': {'type': 'array', 'items': {
        'type': 'object', 'required': ['id', 'decision', 'reason'], 'additionalProperties': False,
        'properties': {'id': {'type': 'string'}, 'decision': {'enum': ['accept', 'revise']},
                       'reason': {'type': 'string'}}}}}}
#: The one item a review judges.
ITEM = 'advice'


def _clip(text: str, n: int) -> str:
    text = ' '.join((text or '').split())
    return text if len(text) <= n else text[:n - 1] + '…'


def evidence(snapshot, domain: str, pillar: str) -> dict:
    """The frozen evidence of one pillar (see the module text); raises ``ValueError`` for an unknown pillar, or
    without networkx (the hint names the extra that installs it)."""
    from .topics import PILLAR_REVIEW_AT, partition
    domains = (snapshot.catalog or {}).get('domains') or {}
    if domain not in domains:
        raise ValueError(f'unknown domain: {domain}')
    meta = domains[domain] if isinstance(domains[domain], dict) else {}
    tax = snapshot.taxonomies.get(meta.get('moc')) or {}
    pillars = [p for p in tax.get('pillars') or [] if isinstance(p, dict) and isinstance(p.get('name'), str)]
    counts = Counter(c.fm.get('pillar') for c in snapshot.cards.values() if c.domain == domain)
    ids = {cid for cid, c in snapshot.cards.items() if c.domain == domain and c.fm.get('pillar') == pillar}
    if not ids:
        raise ValueError(f'no cards under pillar {pillar!r} of domain {domain!r}')
    try:
        groups, scattered = partition(snapshot, ids)
    except ImportError:
        from .labels import install_hint
        raise ValueError(f'sb advise groups the cards by their relations: {install_hint("curate", "en")}') from None
    where = {}
    for i, g in enumerate(groups, 1):
        for cid in g:
            where[cid] = f'g{i}'
    links = {f'g{i}': {'inside': 0, 'other_groups': 0, 'other_pillars': Counter(), 'other_domains': Counter()}
             for i in range(1, len(groups) + 1)}
    seen = set()
    for cid in sorted(ids):
        for rel in snapshot.cards[cid].fm.get('relations') or []:
            target = rel.get('target') if isinstance(rel, dict) else None
            other = snapshot.cards.get(target) if isinstance(target, str) else None
            if other is None or target == cid or cid not in where:
                continue
            row = links[where[cid]]
            if target in ids:
                pair = tuple(sorted((cid, target)))
                if pair in seen:  # a relation and its inverse count once
                    continue
                seen.add(pair)
                if where.get(target) == where[cid]:
                    row['inside'] += 1
                else:
                    row['other_groups'] += 1
            elif other.domain == domain:
                row['other_pillars'][other.fm.get('pillar') or '?'] += 1
            else:
                row['other_domains'][other.domain] += 1

    def card(cid):
        c = snapshot.cards[cid]
        return {'id': cid, 'title': c.title, 'essence': _clip(essence_of(c.body) or '', ESSENCE)}
    why = next((p.get('why', '') for p in pillars if p['name'] == pillar), '')
    return {
        'domain': {'id': domain, 'title': meta.get('moc') or domain, 'question': meta.get('question', ''),
                   'cards': sum(counts.values()),
                   'pillars': [{'name': p['name'], 'why': p.get('why', ''), 'cards': counts.get(p['name'], 0)}
                               for p in pillars]},
        'other_domains': sorted(d for d in domains if d != domain),
        'pillar': {'name': pillar, 'why': why, 'cards': len(ids), 'review_at': PILLAR_REVIEW_AT},
        'groups': [{'id': f'g{i}', 'size': len(g), 'cards': [card(c) for c in g],
                    'links': {'inside': links[f'g{i}']['inside'], 'other_groups': links[f'g{i}']['other_groups'],
                              'other_pillars': dict(sorted(links[f'g{i}']['other_pillars'].items())),
                              'other_domains': dict(sorted(links[f'g{i}']['other_domains'].items()))}}
                   for i, g in enumerate(groups, 1)],
        'scattered': [card(c) for c in scattered],
    }


def packet_of(snapshot, domain: str, pillar: str) -> dict:
    language = config.current().card_language
    a, b, n, w = LIMITS[language]
    return {'kind': KIND, 'method_version': METHOD, 'language': language,
            'limits': {'argument_min': a, 'argument_max': b, 'name_max': n, 'why_max': w},
            **evidence(snapshot, domain, pillar)}


def job_id_of(packet: dict) -> str:
    return 'advice-' + digest(jobs.encoded(packet))[:16]


def prepare(root: Path, *, domain: str | None = None, pillar: str) -> dict:
    """Freeze the evidence of one pillar as a job; the same evidence gives the same job."""
    snapshot = read_snapshot(root)
    if domain is None:
        found = sorted({c.domain for c in snapshot.cards.values() if c.fm.get('pillar') == pillar})
        if len(found) != 1:
            raise ValueError(f'pillar {pillar!r} is in {len(found)} domains; give --domain' if found
                             else f'no cards under pillar {pillar!r}')
        domain = found[0]
    packet = packet_of(snapshot, domain, pillar)
    return jobs.create(root, job_id_of(packet), KIND, packet)


def validate(packet: dict, candidate: dict) -> dict:
    """The program's check of an advice (raises :class:`AnswerError` with every problem found)."""
    if not isinstance(candidate, dict):
        raise AnswerError('the answer must be {"decision": ..., "argument": ...}')
    lim = packet['limits']
    problems = []
    decision = candidate.get('decision')
    groups = {g['id'] for g in packet['groups']}
    if decision not in DECISIONS:
        problems.append(f'decision must be one of {list(DECISIONS)}')
    argument = candidate.get('argument')
    if not isinstance(argument, str) or not lim['argument_min'] <= len(argument.strip()) <= lim['argument_max']:
        problems.append(f'argument must be {lim["argument_min"]}-{lim["argument_max"]} characters')
    if decision != 'keep' and not groups:
        problems.append('no group of related cards reaches the size of a pillar of its own: only keep fits')
    named = candidate.get('pillars') or []
    if decision == 'split_pillars':
        placed = [gid for p in named if isinstance(p, dict) for gid in p.get('groups') or []]
        if sorted(placed) != sorted(groups):
            problems.append(f'split_pillars puts every group in exactly one new pillar: {sorted(groups)}')
        if len(named) < 2:
            problems.append('split_pillars makes at least two pillars')
        others = {p['name'] for p in packet['domain']['pillars']} - {packet['pillar']['name']}
        names = [(p.get('name') or '').strip() for p in named if isinstance(p, dict)]
        for i, p in enumerate(named):
            if not isinstance(p, dict):
                continue
            name, why = (p.get('name') or '').strip(), (p.get('why') or '').strip()
            if not 2 <= len(name) <= lim['name_max']:
                problems.append(f'pillars[{i}]: a pillar name of 2-{lim["name_max"]} characters')
            elif name in others:
                problems.append(f'pillars[{i}]: {name!r} is already another pillar of this domain')
            if not 4 <= len(why) <= lim['why_max']:
                problems.append(f'pillars[{i}]: why (what the pillar answers) of 4-{lim["why_max"]} characters')
        if len(set(names)) != len(names):
            problems.append('each new pillar has its own name')
        if candidate.get('new_domain'):
            problems.append('split_pillars takes no new_domain')
    elif decision == 'new_domain':
        nd = candidate.get('new_domain')
        if not isinstance(nd, dict):
            problems.append('new_domain needs {"groups", "name", "question"}')
        else:
            moving = nd.get('groups') or []
            if not moving or set(moving) - groups or len(set(moving)) != len(moving):
                problems.append(f'new_domain.groups: one or more of {sorted(groups)}, each once')
            name = (nd.get('name') or '').strip()
            if not 2 <= len(name) <= lim['name_max'] * 2:
                problems.append(f'new_domain.name of 2-{lim["name_max"] * 2} characters')
            elif name in {packet['domain']['id'], packet['domain']['title'], *packet['other_domains']}:
                problems.append(f'new_domain.name {name!r} is a domain already')
            if not 4 <= len((nd.get('question') or '').strip()) <= lim['why_max']:
                problems.append(f'new_domain.question of 4-{lim["why_max"]} characters')
        if named:
            problems.append('new_domain takes no pillars list (name the moving groups in new_domain.groups)')
    elif decision == 'keep' and (named or candidate.get('new_domain')):
        problems.append('keep takes no pillars and no new_domain')
    if problems:
        raise AnswerError('The advice was refused:\n- ' + '\n- '.join(problems))
    return candidate


def check_review(packet: dict, candidate: dict, review: dict) -> dict:
    items = review.get('items') if isinstance(review, dict) else None
    if not isinstance(items, list) or [x.get('id') for x in items if isinstance(x, dict)] != [ITEM]:
        raise AnswerError(f'the review judges the one item {ITEM!r}')
    x = items[0]
    if x.get('decision') not in {'accept', 'revise'} or not str(x.get('reason', '')).strip():
        raise AnswerError(f'{ITEM}: decision accept|revise with a reason')
    return review


def summary(packet: dict, candidate: dict, review: dict | None) -> dict:
    """The advice as `sb status` shows it."""
    out = {'decision': candidate['decision'], 'argument': candidate['argument']}
    if candidate.get('pillars'):
        sizes = {g['id']: g['size'] for g in packet['groups']}
        out['pillars'] = [{'name': p['name'], 'why': p['why'], 'groups': p['groups'],
                           'cards': sum(sizes.get(g, 0) for g in p['groups'])} for p in candidate['pillars']]
    if candidate.get('new_domain'):
        sizes = {g['id']: g['size'] for g in packet['groups']}
        out['new_domain'] = {**candidate['new_domain'],
                             'cards': sum(sizes.get(g, 0) for g in candidate['new_domain']['groups'])}
    item = ((review or {}).get('items') or [{}])[0]
    out['review'] = {'decision': item.get('decision'), 'reason': item.get('reason')}
    return out


def apply(root: Path, job_id: str) -> dict:
    """Keep the reviewed advice with the job (nothing in the vault changes); a review that still revised it after the
    repair rounds is kept with it, as the owner's to weigh."""
    state = jobs.load(root, job_id)
    packet = jobs.read_input(root, job_id)
    candidate = validate(packet, jobs.artifact(root, state, 'candidate'))
    review = jobs.artifact(root, state, 'review')
    return jobs.update(root, job_id, status='applied', advice=summary(packet, candidate, review), applied_paths=[])


def lookup(root: Path, snapshot, domain: str, pillar: str) -> dict:
    """What `sb status` says about the advice of one pillar: the kept advice when the pillar's evidence is the
    same as when it was given, else the command that asks for it."""
    try:
        job_id = job_id_of(packet_of(snapshot, domain, pillar))
    except ValueError:
        return {}
    try:
        state = jobs.load(root, job_id)
    except ValueError:
        state = None
    if state and state.get('status') == 'applied' and state.get('advice'):
        return {'advice': {**state['advice'], 'job': job_id}}
    return {'advise': f'sb advise --domain {domain} --pillar {pillar} --executor api'}

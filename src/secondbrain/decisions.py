"""The decision record of a job: every value that decides what is written has one final judge.

Each value an ingest job writes from (a card's level against the vault, its
level against the source, a patch for an existing card, ...) has exactly one
role that gives its last value: :data:`FINAL`. Values made of several steps
(a card's relations, its place in the taxonomy) record each step, and the
steps must come in the order of :data:`STEPS`, each by a role that may take
it. Everything else is a hint (a field named ``*_hint``), never read for an
outcome. The write step reads only this record (``current`` and ``fold``).

The record is a list of entries in the job state (``state["decisions"]``),
only ever appended to. Two kinds of later entry are allowed; any other
second value is an error, raised, never a silent overwrite:

1. The final role judges the value again in a later round. The new entry
   *supersedes* the earlier one, and only for a reason that happened, which
   the caller's facts must confirm: ``repair_round`` (the card is in a later
   round's candidate), ``refile`` (it was filed under a new topic its review
   refused and is filed again), ``dispute`` (the merge check disputed it).
   A merge check run again after a dispute supersedes the whole earlier
   round (:func:`supersede_round`): every value and step that round gave,
   also the ones the new round does not give again.
2. A step of a composite value. When a card is produced again in a later
   round, or judged again after a dispute, its chain of steps starts anew
   (for the same reasons); steps of the roles before the restarting one
   are carried into the new chain.

The section of the design that lists who judges what is generated from these
two tables (:func:`table`); the tables are the original.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

REASONS = ('repair_round', 'refile', 'dispute')
ROLES = ('owner', 'producer', 'reviewer', 'merge_check', 'split_reviewer', 'program')


@dataclass(frozen=True)
class Final:
    role: str
    reasons: tuple
    about: str


@dataclass(frozen=True)
class Step:
    role: str | tuple
    ops: tuple
    about: str

    def takes(self, role: str) -> bool:
        return role in self.role if isinstance(self.role, tuple) else role == self.role

    @property
    def roles(self) -> str:
        return ' or '.join(self.role) if isinstance(self.role, tuple) else self.role


#: Single values: value -> its final role, the reasons it may be judged again for, what it is.
FINAL = {
    'verdict': Final('reviewer', ('repair_round', 'refile', 'dispute'),
                     'a proposed card: accept (its content is right) or revise'),
    'overlap.existing': Final('reviewer', ('repair_round', 'refile', 'dispute'),
                              'an accepted card against the vault cards (in a retry also the written cards): '
                              'level and with'),
    'coverage': Final('reviewer', ('repair_round',), 'a chunk: are its passages handled'),
    'topic.verdict': Final('reviewer', ('repair_round',), 'a proposed new topic: accept or revise'),
    'overlap.source': Final('merge_check', ('dispute',),
                            'a card the reviewer found distinct against the other cards of its source'),
    'topic.same': Final('merge_check', ('dispute',), 'a new topic: distinct, or the same subject as another'),
    'dispute': Final('merge_check', ('dispute',),
                     'a card of the patch set the merge check cannot patch from: its reason'),
    'patch': Final('merge_check', ('dispute',), 'the one patch for an existing card: what is carried, replaced'),
    'overlap.split': Final('split_reviewer', ('repair_round',), 'a split card against its existing card'),
    'split.verdict': Final('split_reviewer', ('repair_round',), 'a split card: accept or revise'),
}

#: Composite values: value -> the steps in order, each with the role that takes it and the operations it may do.
STEPS = {
    'relations': (
        Step('producer', ('create',), 'the producer writes the card with its relations'),
        Step('reviewer', ('add_relation', 'retype_relation', 'remove_relation'),
             'the reviewer amends them (mechanical fixes)'),
        Step('merge_check', ('add',), 'the merge check adds links, never removes one'),
        Step('program', ('filter', 'redirect', 'move', 'renote'),
             'the program drops illegal links and links to unwritten cards, and points links to a repeated '
             'card at the card it repeats'),
    ),
    'classification': (
        Step('owner', ('constrain',), "the owner's placement (--domain, --pillar, --topic) constrains it"),
        Step('producer', ('propose',), 'the producer files the card'),
        Step(('reviewer', 'split_reviewer'), ('accept', 'refile'),
             'the reviewer (of a split card: the split reviewer) accepts the place or refiles the card under an '
             'existing topic (where a card is filed is its decision)'),
        Step('program', ('topic_merge_refile', 'overflow'),
             'the program files the cards of a new topic the merge check found the same as another under that '
             'one (the placement\'s topic is kept), and turns cards of new topics past the limit pending'),
    ),
}


class DecisionError(ValueError):
    """A second value without an allowed reason, a value from the wrong role, or a step out of order."""


class Facts(Protocol):
    """What the job shows happened; the record asks before it accepts a reason to supersede."""

    def reappears(self, obj: str, earlier_slot: str, slot: str) -> bool: ...

    def refiled(self, obj: str, earlier_slot: str, slot: str) -> bool: ...


class NoFacts:
    """Facts of nothing: no reason can be shown (a record built without the job behind it)."""

    def reappears(self, obj, earlier_slot, slot):
        return False

    def refiled(self, obj, earlier_slot, slot):
        return False


# --------------------------------------------------------------------------
# reading the record
# --------------------------------------------------------------------------
def _dead_slots(entries: list) -> dict:
    """``{(role, slot)}`` of rounds superseded as a whole."""
    return {(e['role'], e['supersedes_slot']) for e in entries if e['kind'] == 'round'}


def superseded(entries: list) -> set:
    """Numbers of the entries a later entry superseded (one by one, or with their whole round)."""
    gone = {e['supersedes'] for e in entries if e.get('supersedes') is not None}
    rounds = _dead_slots(entries)
    gone |= {e['n'] for e in entries if e['kind'] in ('value', 'step') and (e['role'], e['slot']) in rounds}
    chains: dict = {}
    for e in entries:
        if e['kind'] == 'step':
            key = (e['value'], e['object'])
            chains[key] = max(chains.get(key, 0), e['chain'])
    gone |= {e['n'] for e in entries if e['kind'] == 'step' and e['chain'] < chains[(e['value'], e['object'])]}
    return gone


def current(entries: list, value: str, obj: str) -> dict | None:
    """The one entry of ``value`` for ``obj`` that nothing superseded, or None."""
    gone = superseded(entries)
    live = [e for e in entries if e['kind'] == 'value' and e['value'] == value and e['object'] == obj
            and e['n'] not in gone]
    if len(live) > 1:  # only reachable by editing the record by hand
        raise DecisionError(f'{value} of {obj} has {len(live)} entries that nothing superseded')
    return live[0] if live else None


def values(entries: list, value: str) -> dict:
    """``{object: entry}`` of every current entry of ``value``."""
    gone = superseded(entries)
    out = {}
    for e in entries:
        if e['kind'] == 'value' and e['value'] == value and e['n'] not in gone:
            if e['object'] in out:
                raise DecisionError(f'{value} of {e["object"]} has more than one entry that nothing superseded')
            out[e['object']] = e
    return out


def chain(entries: list, value: str, obj: str) -> list:
    """The current chain of steps of ``value`` for ``obj``, in order."""
    gone = superseded(entries)
    return [e for e in entries if e['kind'] == 'step' and e['value'] == value and e['object'] == obj
            and e['n'] not in gone]


# --------------------------------------------------------------------------
# writing the record
# --------------------------------------------------------------------------
def _append(entries: list, entry: dict) -> dict:
    entry = {'n': (entries[-1]['n'] + 1) if entries else 1, **entry}
    entries.append(entry)
    return entry


def _proved(entries: list, reason: str, obj: str, earlier: dict, slot: str, facts: Facts) -> bool:
    if reason == 'repair_round':
        return facts.reappears(obj, earlier['slot'], slot)
    if reason == 'refile':
        return facts.refiled(obj, earlier['slot'], slot)
    if reason == 'dispute':  # the merge check disputed this card after the entry it supersedes
        return any(e['kind'] == 'value' and e['value'] == 'dispute' and e['object'] == obj and e['n'] > earlier['n']
                   for e in entries)
    return False


def record(entries: list, value: str, obj: str, data: dict, *, role: str, slot: str, run: str | None = None,
           reason: str | None = None, facts: Facts | None = None) -> dict:
    """Append the final ``value`` of ``obj``; raise :class:`DecisionError` when the record forbids it."""
    rule = FINAL.get(value)
    if rule is None:
        raise DecisionError(f'{value!r} is not a value of the decision record (decisions.FINAL)')
    if role != rule.role:
        raise DecisionError(f'{value} of {obj} is judged by the {rule.role}, not the {role}')
    earlier = current(entries, value, obj)
    entry = {'kind': 'value', 'value': value, 'object': obj, 'data': data, 'role': role, 'slot': slot, 'run': run}
    if earlier is None:
        if reason is not None:
            raise DecisionError(f'{value} of {obj}: nothing to supersede for {reason}')
        return _append(entries, entry)
    if reason is None:
        raise DecisionError(f'{value} of {obj} was given already (entry {earlier["n"]}, {earlier["slot"]}); a second '
                            'value needs a reason to supersede it')
    if reason not in rule.reasons:
        raise DecisionError(f'{value} of {obj} may be judged again only for {", ".join(rule.reasons)}, not {reason}')
    if not _proved(entries, reason, obj, earlier, slot, facts or NoFacts()):
        raise DecisionError(f'{value} of {obj}: the job shows no {reason} that would let {slot} supersede '
                            f'{earlier["slot"]}')
    return _append(entries, {**entry, 'supersedes': earlier['n'], 'reason': reason})


def supersede_round(entries: list, *, role: str, slot: str, earlier_slot: str, reason: str) -> dict:
    """Record that round ``slot`` of ``role`` replaces all of round ``earlier_slot`` (the merge check after a dispute).

    The earlier round must hold at least one ``dispute``: that is the only
    reason a merge check runs again.
    """
    if reason != 'dispute':
        raise DecisionError(f'a whole round is superseded only after a dispute, not {reason}')
    gone = superseded(entries)
    if not any(e['kind'] == 'value' and e['value'] == 'dispute' and e['slot'] == earlier_slot and e['role'] == role
               and e['n'] not in gone for e in entries):
        raise DecisionError(f'{slot} supersedes {earlier_slot} for a dispute, but {earlier_slot} disputed nothing')
    return _append(entries, {'kind': 'round', 'role': role, 'slot': slot, 'supersedes_slot': earlier_slot,
                             'reason': reason})


def step(entries: list, value: str, obj: str, op: str, *, role: str, slot: str, run: str | None = None,
         data: dict | None = None, reason: str | None = None, facts: Facts | None = None) -> dict:
    """Append one step of the composite ``value`` of ``obj``.

    The step must be one the role may take (:data:`STEPS`) and come no earlier
    in the order than the chain's last step. With ``reason``, the chain starts
    anew from this role (a later round, or a judgement again after a dispute);
    the steps of the roles before it are carried into the new chain.
    """
    steps = STEPS.get(value)
    if steps is None:
        raise DecisionError(f'{value!r} is not a composite value of the decision record (decisions.STEPS)')
    index = next((i for i, s in enumerate(steps) if s.takes(role)), None)
    if index is None or op not in steps[index].ops:
        allowed = '; '.join(f'{s.roles}: {", ".join(s.ops)}' for s in steps)
        raise DecisionError(f'{value} of {obj}: the {role} may not {op} (allowed: {allowed})')
    live = chain(entries, value, obj)
    number = max((e['chain'] for e in entries if e['kind'] == 'step' and e['value'] == value and e['object'] == obj),
                 default=0)
    entry = {'kind': 'step', 'value': value, 'object': obj, 'op': op, 'role': role, 'slot': slot, 'run': run,
             'data': data or {}, 'index': index}
    if reason is not None:
        if reason not in REASONS:
            raise DecisionError(f'a chain starts anew only for {", ".join(REASONS)}, not {reason}')
        earlier = next((e for e in reversed(live) if e['index'] >= index), None) or (live[-1] if live else None)
        if earlier is None:
            raise DecisionError(f'{value} of {obj}: no chain to start anew for {reason}')
        if not _proved(entries, reason, obj, earlier, slot, facts or NoFacts()):
            raise DecisionError(f'{value} of {obj}: the job shows no {reason} that would start its chain anew')
        number += 1
        for old in live:
            if old['index'] < index:
                _append(entries, {**{k: v for k, v in old.items() if k != 'n'}, 'chain': number, 'carried': old['n']})
        return _append(entries, {**entry, 'chain': number, 'reason': reason})
    if live and live[-1]['index'] > index:
        raise DecisionError(f'{value} of {obj}: a {role} step ({op}) cannot come after a {live[-1]["role"]} step '
                            f'({live[-1]["op"]}) in one round; order: '
                            + ' → '.join(s.roles for s in steps))
    return _append(entries, {**entry, 'chain': max(number, 1)})


# --------------------------------------------------------------------------
# folding composite values
# --------------------------------------------------------------------------
def _relations(steps: list) -> list:
    out: list = []
    for e in steps:
        d = e['data']
        if e['op'] == 'create':
            out = [dict(r) for r in d.get('relations') or []]
        elif e['op'] in ('add', 'add_relation'):
            if not any(r['target'] == d['relation']['target'] for r in out):
                out.append(dict(d['relation']))
        elif e['op'] == 'retype_relation':
            out = [{**r, 'type': d['type'], **({'note': d['note']} if d.get('note') else {})}
                   if r['target'] == d['target'] else r for r in out]
        elif e['op'] in ('remove_relation', 'filter'):
            out = [r for r in out if not (r['target'] == d['target'] and r['type'] == d.get('type', r['type']))]
        elif e['op'] == 'redirect':
            out = [{**r, 'target': d['to']} if r['target'] == d['target'] else r for r in out]
        elif e['op'] == 'move':
            if not any(r['target'] == d['relation']['target'] for r in out):
                out.append(dict(d['relation']))
        elif e['op'] == 'renote':
            out = [{**r, 'note': d['note']} if r['target'] == d['target'] and r['type'] == d['type'] else r
                   for r in out]
    return out


def _classification(steps: list) -> dict:
    place: dict = {}
    for e in steps:
        d = e['data']
        if e['op'] in ('propose', 'refile', 'topic_merge_refile'):
            place = {k: d[k] for k in ('domain', 'pillar', 'topic')}
        elif e['op'] == 'overflow':
            place = {**place, 'pending': d.get('reason') or 'new topic past the limit'}
    return place


FOLDS: dict[str, Callable[[list], object]] = {'relations': _relations, 'classification': _classification}


def fold(entries: list, value: str, obj: str):
    """The final ``value`` of ``obj``: its current chain of steps applied in order."""
    return FOLDS[value](chain(entries, value, obj))


# --------------------------------------------------------------------------
# the table of the design, generated from the two tables above
# --------------------------------------------------------------------------
def table() -> str:
    """Markdown: every value with its final judge and when it may be judged again; every composite with its steps."""
    rows = ['| Value | Final judge | Judged again only for | What it is |', '|---|---|---|---|']
    rows += [f'| `{v}` | {f.role} | {", ".join(f.reasons) or "—"} | {f.about} |' for v, f in FINAL.items()]
    rows += ['', '| Composite value | Step | Role | Operations | What it is |', '|---|---|---|---|---|']
    for value, steps in STEPS.items():
        rows += [f'| `{value}` | {i} | {s.roles} | {", ".join(s.ops)} | {s.about} |' for i, s in enumerate(steps, 1)]
    return '\n'.join(rows) + '\n'

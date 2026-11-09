"""How much a new card repeats other cards: one scale, one definition, one table of outcomes.

Every step of the ingest chain that judges a repeat uses :data:`LEVELS`, and
each judges one kind of comparison, so no value is given twice:

- the chunk reviewer judges a new card against the vault's cards (in a retry,
  also the cards earlier jobs of the source wrote): value ``overlap.existing``;
- the merge check judges a card the reviewer found ``distinct`` against the
  other cards of the source: value ``overlap.source``;
- the split slot's reviewer judges a split card against its existing card:
  value ``overlap.split``.

The producer only proposes (``overlap_hint`` on a card, ``same`` coverage
rows); the program never reads a hint for an outcome. :func:`outcome` reads the
final values from the job's decision record (:mod:`secondbrain.decisions`).
The definition the three prompts share is generated here (:func:`definition`).
"""
from __future__ import annotations

from . import decisions

LEVELS = ('distinct', 'extends', 'same')
#: Two new topics of one source: one subject or two (the merge check).
TOPIC_LEVELS = ('distinct', 'same')
#: Coverage rows: a card, knowledge vault cards hold already (``same``), not worth keeping, no place yet.
COVERAGE = ('card', 'same', 'out_of_scope', 'needs_taxonomy')
#: Names of earlier versions; a prompt that still uses one fails the prompt test.
RETIRED = ('keep', 'duplicate', 'needs_review', 'duplicate_of', 'overlap_with')

_MEANING = {
    'distinct': ('the card answers another question than the cards in `with`, also when it is about the same '
                 'things (which tools there are, and how to choose among them, are two questions): merged into the '
                 'card in `with`, that card would no longer answer one question (it would stop being one unit). '
                 '`with` may be empty or name related cards.'),
    'extends': ('the card answers the same question as the one card in `with` (the question its title names), or one '
                'of its sub-questions, and has details, examples, cases or the answer to a sub-question that card '
                'lacks; merged into it, that card still answers one question, and every proposition it adds answers '
                'that question. A card that only answers a narrower sub-question of that card is `extends` too. '
                '`with` names exactly one card.'),
    'same': ('the card holds nothing that the cards in `with` do not hold; its knowledge may be spread over several '
             'of them. `with` names at least one card.'),
}
#: The line every judge of a repeat reads after the definition (design D-09): what is a unit of its own.
UNIT_RULE = ('More examples, cases or sub-questions on the same question are not a unit of their own (they are '
             '`extends`); only a card that answers another question, which would leave the other card no longer '
             'answering one question if merged into it, is a unit of its own (`distinct`), even when it is about the '
             'same object.')


def definition() -> str:
    """The shared definition of the scale, as the prompts show it (one paragraph per level, in :data:`LEVELS` order)."""
    lines = ['How much a card repeats other cards is one of three levels, the same wherever it is judged:']
    lines += [f'- `{level}`: {_MEANING[level]}' for level in LEVELS]
    return '\n'.join(lines + ['', UNIT_RULE]) + '\n'


#: Where the prompts take the definition: this line is replaced by :func:`definition`.
PLACEHOLDER = '<!-- overlap.definition -->'


def with_problem(level: str, ids, known=None) -> str | None:
    """Why ``with`` does not fit ``level`` (``extends``: one card; ``same``: at least one), or None.

    ``known``, when given, is the set of ids ``with`` may name.
    """
    if level not in LEVELS:
        return f'overlap must be one of {", ".join(LEVELS)}'
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
        return '`with` must be a list of card ids'
    if level == 'extends' and len(ids) != 1:
        return '`extends` names exactly one card in `with`'
    if level == 'same' and not ids:
        return '`same` names at least one card in `with`'
    if len(set(ids)) != len(ids):
        return '`with` names a card twice'
    if known is not None:
        stray = [i for i in ids if i not in known]
        if stray:
            return f'`with` may name only {known.description if hasattr(known, "description") else "known"} ' \
                   f'cards; not {", ".join(stray)}'
    return None


class KnownIds(frozenset):
    """A set of ids with a description for error messages."""

    def __new__(cls, ids, description: str):
        self = super().__new__(cls, ids)
        self.description = description
        return self


#: What becomes of an accepted card, by the comparison and its level. Against the vault: ``decide`` sends the
#: card to the merge check's decision set, ``patch`` to the patch set of its existing card, ``drop`` removes
#: it (not written; listed under ``duplicates``). Against the source: ``write``, ``merge_carry`` (its carried
#: propositions and review cards go to the card in ``with``), ``merge`` (nothing carried). The program records the
#: fate of a card that goes into another (``outcome.program``), which may be ``write``, ``drop`` or ``pending`` (it
#: waits for the owner: a dispute not settled, a judgement again that kept failing the checks, a split card not done).
OUTCOMES = {
    ('existing', 'distinct'): 'decide', ('existing', 'extends'): 'patch', ('existing', 'same'): 'drop',
    ('source', 'distinct'): 'write', ('source', 'extends'): 'merge_carry', ('source', 'same'): 'merge',
}


def route(entries: list, slug: str) -> dict:
    """``{"outcome", "with", "reason"}`` of an accepted card by its level against the vault (before the merge check)."""
    existing = decisions.current(entries, 'overlap.existing', slug)
    if existing is None:
        raise OutcomeError(f'{slug}: no final level against the vault cards (the chunk reviewer gives it)')
    data = existing['data']
    return {'outcome': OUTCOMES[('existing', data['overlap'])], 'with': list(data['with']), 'reason': data.get('reason')}


class OutcomeError(ValueError):
    """The decision record lacks a final value an outcome needs, or holds one it must not hold."""


def outcome(entries: list, slug: str, *, checked: bool) -> dict:
    """``{"outcome", "with", "by"}`` of the accepted card ``slug``, from the decision record only.

    The card's level against the vault (``overlap.existing``, the chunk
    reviewer's) comes first; only a ``distinct`` card has a level against the
    source (``overlap.source``, the merge check's), and it must have one when
    the merge check ran (``checked``). A hint is never read.
    """
    existing = decisions.current(entries, 'overlap.existing', slug)
    source = decisions.current(entries, 'overlap.source', slug)
    program = decisions.current(entries, 'outcome.program', slug)
    if program is not None:  # the program sent it elsewhere than its levels would (a card of the patch set written)
        return {'outcome': program['data']['outcome'], 'with': [], 'by': 'program'}
    if existing is None:
        raise OutcomeError(f'{slug}: no final level against the vault cards (the chunk reviewer gives it)')
    level = existing['data']['overlap']
    first = OUTCOMES[('existing', level)]
    if first != 'decide':
        if source is not None:
            raise OutcomeError(f'{slug}: the chunk reviewer judged it {level} against the vault cards, so it is not '
                               'in the merge check\'s decision set, yet the record holds a level against the source')
        return {'outcome': first, 'with': list(existing['data']['with']), 'by': 'reviewer'}
    if source is None:
        if checked:
            raise OutcomeError(f'{slug}: the merge check ran but gave no level against the source')
        return {'outcome': 'write', 'with': [], 'by': 'reviewer'}
    return {'outcome': OUTCOMES[('source', source['data']['overlap'])], 'with': list(source['data']['with']),
            'by': 'merge_check'}

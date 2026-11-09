"""A reviewer's notes: each of one of five kinds, and the program's rule on them (decision D-18 3).

Reviewers wrote a real problem down in a note and still accepted the card, and the wrong card went into the vault
(H8: 3 of the 6 real problems; H4: 36 of the injected errors). So every note now names its kind. Four kinds are the
blocking problems of design section 2.5 (``fact``, ``unreadable``, ``quote``, ``not_a_unit``); the fifth,
``minor``, is wording, style, or something that could be better but is not wrong. When a reviewer accepts a card
(a split card, a paragraph of an export) and notes a problem of one of the first four kinds, the program sends it
back as ``revise``, that note its reason: it counts toward the content repair rounds like any revise, and once
they are used up the card is listed for the owner. Only ``minor`` notes leave the decision as it is; they are
recorded for the owner.

Every reviewer prompt that asks for notes takes the same definition (:func:`definition`, put in for
:data:`PLACEHOLDER` by :func:`secondbrain.runner.prompt`).

A reviewer of a card reads more (:func:`card_part`, for :data:`CARD_PLACEHOLDER`): a sixth kind, ``repeats`` (the
card says one thing twice), which sends an accepted card back as the first four do; the card's one question
(:mod:`secondbrain.unit`), written in the review row's ``question`` before each proposition and review card is
checked against it; and that an example gives on the card the premises it uses. A reviewer of an article's
paragraphs reads none of it: its notes keep the five kinds.
"""
from __future__ import annotations

from . import unit

CATEGORIES = ('fact', 'unreadable', 'quote', 'not_a_unit', 'minor')
#: The kinds that send an accepted item back; the same four a focused repair round may revise a card for.
BLOCKING = CATEGORIES[:4]
#: The kinds only a reviewer of a card writes; each sends an accepted card back.
CARD_ONLY = ('repeats',)
CARD_CATEGORIES = BLOCKING + CARD_ONLY + ('minor',)
CARD_BLOCKING = BLOCKING + CARD_ONLY


def _notes(categories) -> dict:
    return {'type': ['array', 'null'], 'items': {
        'type': 'object', 'required': ['category', 'text'],
        'properties': {'category': {'type': 'string', 'enum': list(categories)}, 'text': {'type': 'string'}}}}


NOTES = _notes(CATEGORIES)
NOTE = NOTES['items']
#: The notes of a reviewer of a card (a chunk's cards, a split card).
CARD_NOTES = _notes(CARD_CATEGORIES)

PLACEHOLDER = '<!-- notes.definition -->'
#: Where a card reviewer's prompt takes :func:`card_part`, right after the definition.
CARD_PLACEHOLDER = '<!-- notes.cards -->'

#: The rule of D-18 4: in a replay of H4 reviewers noted a condition a proposition had dropped and called it minor,
#: as the essence or a review card still had it; 8 of the 10 should have been sent back.
ON_ITS_OWN = ('A proposition that, read on its own, says more than the text it comes from (it leaves out a condition, a '
              'limit or a case the text gives), says it wrongly, or cannot be understood is `fact` (`unreadable` when '
              'it cannot be understood) and needs a revise. That the card\'s essence or a review card still gives the '
              'condition is no reason to call it `minor`: each proposition must be right by itself.')

_DEFINITION = """\
`notes`: what you noticed beyond your decision, each as `{"category": "<kind>", "text": "<what and where, short>"}`.
The kind says how much it matters:
- `fact`: a statement that is wrong: the text it comes from (the chunk; for an article, the cited cards) contradicts it or does not say it (an added fact, number, example or name; a relation or comparison the wrong way round). A fact in a confusion or an intuition that neither the chunk nor a card it names by `[[title]]` gives is `fact` too, however true it is.
- `unreadable`: it cannot be understood without the source: it points at the source ("the example above", "this handout") or leaves out what it relies on.
- `quote`: a quotation or citation that does not hold: a source quote that is not in the chunk word for word, or a card cited for what it does not say.
- `not_a_unit`: it is not one recallable unit: two separate ideas in one card, or a fragment that does not stand on its own.
- `minor`: wording, style, or something that could be better but is not wrong.
{on_its_own}
A note of the first four kinds is a reason to revise: when you accept with such a note, the program sends the item back, with the note as the reason. Write `minor` only for what is not wrong; when something is wrong, revise and give the reason.""".replace("{on_its_own}", ON_ITS_OWN)


def definition() -> str:
    return _DEFINITION


#: A card that says one thing twice. What an essence, an intuition or a confusion says again is left out: judges did
#: not agree on it.
REPEATS = ('- `repeats`: the card says one thing twice: two of its propositions, or two of its review cards, make the '
           'same point in other words. An essence, an intuition or a confusion that says again what a proposition '
           'says is not `repeats`.')
#: The question a reviewer writes first, and checks every item against.
QUESTION = ('Write that question in `question` first, in one sentence in the card\'s language, as a reader would ask '
            'it. Then check each proposition (P1 is the first) and each review card (Q1 is the first) against it. '
            'When some of them answer another question, revise, with a `not_a_unit` note that names them and the '
            'question they answer: they are to be split off into a card of their own, or moved.')
#: An example needs its premises on the card.
PREMISES = ('Readable without the source: an example or a worked calculation gives, on the card, the premises it uses '
            '(the rule, the coordinates, the code book, the input values); one that relies on premises only the source '
            'gives is `unreadable`.')


def card_part() -> str:
    """What a reviewer of a card reads after :func:`definition`: the kind only a card has, its one question, and the
    premises of its examples."""
    return '\n'.join([
        'On a card, one more kind of note:', REPEATS,
        'A `repeats` note is a reason to revise as well: when you accept with one, the card is sent back.', '',
        unit.definition(), QUESTION, '', PREMISES])


def problems(notes, where: str, categories=CATEGORIES) -> list[str]:
    """What is wrong with the notes of one item (``where`` names it): each needs a known kind (of ``categories``: a
    card's reviewer has :data:`CARD_CATEGORIES`) and a text."""
    out = []
    for i, note in enumerate(notes or []):
        if not isinstance(note, dict):
            continue  # the shape check names it
        if note.get('category') not in categories:
            out.append(f'{where}.notes[{i}].category must be one of {", ".join(categories)}')
        if not isinstance(note.get('text'), str) or not note['text'].strip():
            out.append(f'{where}.notes[{i}].text must say what is wrong and where')
    return out


def blocking_note(notes) -> dict | None:
    """The first note of a blocking kind, if any (a card's ``repeats`` too: notes of other items cannot have it)."""
    return next((n for n in notes or [] if isinstance(n, dict) and n.get('category') in CARD_BLOCKING), None)


def turned(row: dict) -> dict:
    """``row`` (a reviewer's verdict on one item) sent back when it accepts with a blocking note; else ``row``.

    The turned row is a ``revise`` whose reason is the note; ``turned`` keeps what the reviewer answered (its
    decision, reason and the note), and ``blocking`` names the kind. A mechanical fix comes only with an accept, so
    a turned row has none, and the level it gave (``overlap``) is not read on a revise.
    """
    note = blocking_note(row.get('notes')) if row.get('decision') == 'accept' else None
    if note is None:
        return row
    kept = {k: v for k, v in row.items() if k not in ('amend', 'decision', 'reason', 'blocking')}
    return {**kept, 'decision': 'revise', 'blocking': note['category'],
            'reason': f"the review noted a {note['category']} problem: {note['text']}",
            'turned': {'from': 'accept', 'reason': row.get('reason'), 'note': note}}


def text(note) -> str:
    """A note as one line, its kind first (for the owner's list and the merge check's packet)."""
    return f"[{note.get('category')}] {note.get('text')}" if isinstance(note, dict) else str(note)

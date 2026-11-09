"""What one card is: the answer to one question.

"One recallable unit" was a word every model read its own way: blind judges found cards that answered two questions
a reader could ask apart (which tools there are, and how to choose among them), most often after a card had new
points added to it. The rule here makes the unit a thing to check item by item: the card's question first, then each
proposition and review card against it.

The producers (a chunk's cards, a split card), every reviewer of a card (through
:func:`secondbrain.review_notes.card_part`) and the merge check composing a patch read these same words; the levels of
a repeat (:mod:`secondbrain.overlap`) are defined by the same question.
"""
from __future__ import annotations

ONE_QUESTION = (
    'One question: a card answers one question, the one its title names. Each proposition and each review card '
    'answers that question or gives what its answer needs (a condition, a case, an example, a step of it, the '
    'answer to one of its sub-questions). A proposition or a review card that answers another question, one a '
    'reader could ask and have answered by a card of its own, does not belong on the card, even when it is about '
    'the same things: which tools there are is one question; how they differ, or which to choose when, is another. '
    'It goes on a card of its own, linked to this one.')

#: Where a prompt takes the rule: this line is replaced by :func:`definition`.
PLACEHOLDER = '<!-- unit.definition -->'


def definition() -> str:
    return ONE_QUESTION

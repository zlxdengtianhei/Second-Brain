"""Where a card's essence, intuition and confusions may take what they say from: the chunk, or a card named by title.

A card's propositions were checked against the chunk, but its essence, intuition and confusions were not on the
reviewer's list, and the producer was told to write intuitions and confusions "whenever the chunk or the candidate
cards support them" without saying a comparison must come from somewhere. Producers filled them from general
knowledge. The three prompts now say the same thing: each sentence comes from the chunk or from a card it names by
``[[title]]``; the reviewer checks each sentence; a fact with no such source is a ``fact`` note. The program's check
that a card with a ``contrasts_with`` relation writes a confusion stays as it was.
"""
from __future__ import annotations

import re

from secondbrain import review_notes
from secondbrain.runner import prompt


def one_line(text: str, start: str) -> str:
    return next(line for line in text.splitlines() if line.lstrip().startswith(start))


def test_the_reviewer_checks_every_sentence_of_the_essence_intuition_and_confusions():
    faithful = one_line(prompt('review_cards.md'), '- Faithful:')
    assert 'every sentence of the essence, the intuition and each confusion' in faithful
    assert "supported by the chunk's text, or say only what a library card it names by `[[title]]` has written" in \
        faithful
    assert 'general knowledge' in faithful and 'also in the intuition and the confusions' in faithful


def test_the_producer_writes_intuitions_and_confusions_from_the_chunk_or_a_card_named_by_title():
    rule = one_line(prompt('produce_cards.md'), '- `intuition`')
    assert 'They say only what the chunk says.' in rule
    assert re.search(r'name it by `\[\[title\]\]`.*use only what the chunk or that card has written', rule)
    assert 'never a fact, an example or a difference from general knowledge' in rule
    assert 'A card with a `contrasts_with` relation states that contrast in `confusions`, from these two sources ' \
           'only.' in rule


def test_an_unsourced_fact_in_a_confusion_or_an_intuition_is_a_fact_note():
    fact = one_line(review_notes.definition(), '- `fact`:')
    assert 'A fact in a confusion or an intuition that neither the chunk nor a card it names by `[[title]]` gives ' \
           'is `fact` too' in fact
    assert fact in prompt('review_cards.md')  # the reviewer's prompt has the one definition

"""The functions that put a patch or a carry into a card check their own result: no caller can leave the check out.

The fifth verdict found the check before writing (every item of a patch is in the card, ``source_check.lost``) called
by each of the two write paths, the patch into an existing card and the carry into a card of the source, on its own;
a third path added later would have to remember it. Now the one function that writes an existing card's knowledge
sections (``cards.patch``) takes the patch it writes (``taken``, a required argument) and checks its result, and the
one function that merges a carry into a card of the source (``source_check.take``) does the same. Called directly,
around the pipelines, with an item left out, each refuses.
"""
from __future__ import annotations

import pytest

from secondbrain import cards as cardfmt
from secondbrain.pipelines import source_check
from secondbrain.vault import parse_note

RAW = ('---\nid: demo_card\ntitle: Card\nsources: []\nrelations: []\n---\n'
       '## Essence\nA card.\n\n## Propositions\n- P1. One.\n- P2. Two.\n\n## Review cards\nQ1?::A1.\n').encode()


def test_cards_patch_refuses_to_write_a_card_that_lost_an_item_of_its_patch():
    taken = {'propositions': ['Three.', 'Four.'], 'flashcards': [{'q': 'Q2?', 'a': 'A2.'}]}
    out = cardfmt.patch(RAW, taken=taken, add_props=['Three.', 'Four.'], add_flashcards=[{'q': 'Q2?', 'a': 'A2.'}])
    assert cardfmt.propositions_of(parse_note(out)[1])[-2:] == ['Three.', 'Four.']
    with pytest.raises(cardfmt.PatchLost, match=r"proposition 'Four\.'") as lost:  # one left out on the way
        cardfmt.patch(RAW, taken=taken, add_props=['Three.'], add_flashcards=[{'q': 'Q2?', 'a': 'A2.'}])
    assert lost.value.missing == ["proposition 'Four.'"]
    with pytest.raises(cardfmt.PatchLost, match=r"review card 'Q2\?'"):
        cardfmt.patch(RAW, taken=taken, add_props=['Three.', 'Four.'])
    swap = {'flashcard_replace': [{'flashcard': 'Q1', 'q': 'Q1, fuller?', 'a': 'A1, and more.'}]}
    with pytest.raises(cardfmt.PatchLost, match=r'which replaces Q1'):
        cardfmt.patch(RAW, taken=swap)  # the swap is not made
    assert b'Q1, fuller?::A1, and more.' in cardfmt.patch(RAW, taken=swap,
                                                         replace_flashcards={1: {'q': 'Q1, fuller?',
                                                                                 'a': 'A1, and more.'}})


def test_the_patch_it_takes_must_be_named():
    with pytest.raises(TypeError, match='taken'):
        cardfmt.patch(RAW, add_props=['Three.'])  # noqa - what a new write path that forgot the check would do


def test_the_carry_merge_refuses_a_result_that_lost_an_item(monkeypatch):
    carry = {'propositions': ['Three.'], 'flashcards': [{'q': 'Q2?', 'a': 'A2.'}]}
    took = source_check.take(['One.', 'Two.'], [{'q': 'Q1?', 'a': 'A1.'}], carry, [{'q': 'Q2?', 'a': 'A2.'}])
    assert took['propositions'][-1] == 'Three.' and took['flashcards'][-1] == {'q': 'Q2?', 'a': 'A2.'}
    monkeypatch.setattr(source_check, 'repeat', lambda a, b: True)  # every item taken for one the card holds
    with pytest.raises(source_check.Lost, match=r"proposition 'Three\.'"):
        source_check.take(['One.', 'Two.'], [{'q': 'Q1?', 'a': 'A1.'}], carry, [{'q': 'Q2?', 'a': 'A2.'}])

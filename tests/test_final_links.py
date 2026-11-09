"""A ``[[link]]`` in the text written goes to the title the named card is in the vault under, or is no link at all.

A card may name another card of its source by ``[[title]]`` (an example's premise from another chunk, a contrast).
That card is often not written under that title: it goes into an existing card (``extends``), is judged the same as
one (``same``), is written under a title made unique, or waits for the owner. The link was written as it was, to a
title no card has. Now, once titles and merges are final, each link is pointed at the title its card is in the vault
under (recorded in ``links_moved``); a link to a card that is not written is left as plain text (``links_unlinked``)
and its card is written needs-review with the reason ``dangling_link``, on the owner's list. Only the link changes,
never the words around it. The cards are invented.
"""
from __future__ import annotations

import pytest

from secondbrain.vault import read_snapshot
from tests.test_patch_set import job, producer, reviewer, run

LINE = 'A lantern queue keeps arrival order, as [[{}]] states.'


def written_line(root):
    snap = read_snapshot(root)
    lines = [line for c in snap.cards.values() for line in (root / c.path).read_text(encoding='utf-8').splitlines()
             if 'arrival order, as' in line]
    return snap, lines


@pytest.mark.parametrize('title, how', [('Lantern queue', 'extends'), ('Arrival order in lantern queues', 'same')])
def test_a_link_to_a_card_that_went_into_an_existing_card_goes_to_that_card(tmp_path, title, how):
    """The first chunk's card is merged into a card of the vault (or judged the same as one); the second chunk's card
    names it: the link goes to the card of the vault."""
    root, job_id, _ = job(tmp_path)
    base = producer()

    def produce(task):
        answer = base(task)
        for c in answer.get('cards') or []:
            if isinstance(c, dict) and c.get('slug') == 'lit-marker-cost':
                c['propositions'] = list(c.get('propositions') or []) + [LINE.format(title)]
        return answer
    done = run(root, job_id, reviewer(), produce)
    assert done['status'] == 'applied', done.get('pending')
    snap, [line] = written_line(root)
    [target] = [m['to'] for m in done['links_moved'] if m['from'] == title]
    assert f'[[{target}]]' in line and target in {c.title for c in snap.cards.values()} and target != title
    assert done['links_unlinked'] == []


def test_a_link_to_a_card_that_is_nowhere_is_plain_text_and_its_card_waits_for_the_owner(tmp_path):
    from secondbrain.service import Service
    root, job_id, _ = job(tmp_path)
    base = producer()

    def produce(task):
        answer = base(task)
        for c in answer.get('cards') or []:
            if isinstance(c, dict) and c.get('slug') == 'lit-marker-cost':
                c['propositions'] = list(c.get('propositions') or []) + [LINE.format('A card nobody wrote')]
        return answer
    done = run(root, job_id, reviewer(), produce)
    snap, [line] = written_line(root)
    assert line.endswith('as A card nobody wrote states.') and '[[' not in line.split('as', 1)[1]
    assert [u['link'] for u in done['links_unlinked']] == ['A card nobody wrote']
    [item] = [i for i in Service(root).owner_items() if i['owner_reason'] == 'dangling_link']
    assert 'arrival order, as' in (root / item['path']).read_text(encoding='utf-8')

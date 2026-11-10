"""A ``[[link]]`` in the text written goes to the title the named card is in the vault under, or is no link at all.

A card may name another card of its source by ``[[title]]`` (an example's premise from another chunk, a contrast).
That card is often not written under that title: it goes into an existing card (``extends``), is judged the same as
one (``same``), is written under a title made unique, or waits for the owner. The link was written as it was, to a
title no card has. Now, once titles and merges are final, each link is pointed at the title its card is in the vault
under (recorded in ``links_moved``); a link to a card that is not written is left as plain text (``links_unlinked``)
and its card is written needs-review with the reason ``dangling_link``, on the owner's list. Only the link changes,
never the words around it.

The same holds in a patch an existing card takes: it is relinked once, and the write checks and writes the same
words (before, the check read the patch as given and the write the relinked words, and the write stopped every
time). A link that names, under another title, the existing card it goes into is plain text. What a patch adds is
read from the relinked patch, so two items alike once relinked go in once. The cards are invented.
"""
from __future__ import annotations

import pytest

from secondbrain import cards as cardfmt
from secondbrain.vault import read_snapshot
from tests.helpers import EXISTING
from tests.ingest_helpers import keep_all
from tests.test_patch_set import LANTERN, job, producer, reviewer, run

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


PATCH_LINE = 'A lantern queue lights its markers in turn, as [[{}]] says.'


@pytest.mark.parametrize('title', ['A queue card no one wrote', 'Arrival order in lantern queues'])
def test_a_link_in_a_patch_is_relinked_once_and_the_existing_card_takes_the_patch(tmp_path, title):
    """lantern-queue extends the vault's card, which takes its propositions as a patch; one of them names a card no
    one wrote, or the card judged the same as that very vault card."""
    root, job_id, _ = job(tmp_path)
    base = producer()

    def produce(task):
        answer = base(task)
        for c in answer.get('cards') or []:
            if isinstance(c, dict) and c.get('slug') == 'lantern-queue':
                c['propositions'] = list(c.get('propositions') or []) + [PATCH_LINE.format(title)]
        return answer
    done = run(root, job_id, reviewer(), produce)
    assert done['status'] == 'applied', done.get('pending')
    assert done['patch_set'] == {'lantern-queue': 'patched'}
    old = read_snapshot(root).cards[EXISTING]
    text = (root / old.path).read_text(encoding='utf-8')
    assert f'as {title} says.' in text and f'[[{title}' not in text  # the words stay, the link is plain text
    moved = [m for m in done['links_moved'] if m.get('into') == EXISTING]
    unlinked = [u for u in done['links_unlinked'] if u.get('into') == EXISTING]
    if title == 'A queue card no one wrote':
        assert (moved, unlinked) == ([], [{'into': EXISTING, 'cards': ['lantern-queue'], 'link': title}])
    else:  # judged the same as the vault card: it names the card it stands in
        assert (moved, unlinked) == ([{'into': EXISTING, 'cards': ['lantern-queue'], 'from': title, 'to': old.title}],
                                     [])


NOBODY = '[[A card no one filed]]'


def patching(tmp_path, propositions=(), flashcards=(), check=None, old_line=None, first=None):
    """Run the two-chunk job; lantern-queue (which extends the vault's card) gets ``propositions`` and ``flashcards``
    more (and ``first`` for its first proposition), the merge check answers with ``check``, and ``old_line`` (old,
    new) rewrites a line of the vault's card."""
    root, job_id, _ = job(tmp_path)
    if old_line:
        path = next((root / '10_Knowledge').rglob(f'{EXISTING}.md'))
        path.write_text(path.read_text(encoding='utf-8').replace(*old_line), encoding='utf-8')
    base = producer()

    def produce(task):
        answer = base(task)
        for c in answer.get('cards') or []:
            if isinstance(c, dict) and c.get('slug') == 'lantern-queue':
                c['propositions'] = ([first] if first else []) + list(c.get('propositions') or [])[1 if first else 0:] \
                    + list(propositions)
                c['flashcards'] = list(c.get('flashcards') or []) + list(flashcards)
        return answer
    done = run(root, job_id, reviewer(check=check) if check else reviewer(), produce)
    assert done['status'] == 'applied', done.get('pending')
    old = read_snapshot(root).cards[EXISTING]
    return done, cardfmt.propositions_of(old.body), cardfmt.flashcards_of(old.body)


@pytest.mark.parametrize('shape', ['two items', 'an item and the card'])
def test_items_alike_once_relinked_go_into_the_existing_card_once(tmp_path, shape):
    plain = 'A lantern queue dims its markers past A card no one filed here.'
    linked = plain.replace('A card no one filed', NOBODY)
    if shape == 'two items':
        done, props, _ = patching(tmp_path, propositions=[linked, plain])
    else:  # the vault's card says it already, in plain words
        done, props, _ = patching(tmp_path, propositions=[linked],
                                  old_line=('- P1. Old proposition 1 about routing readers.', f'- P1. {plain}'))
    assert props.count(plain) == 1 and not any(NOBODY in p for p in props)


def test_a_link_in_a_patchs_review_card_is_relinked(tmp_path):
    card = {'q': f'Which card keeps the order rule? {NOBODY}', 'a': 'The card on arrival order.'}
    done, _, flashcards = patching(tmp_path, flashcards=[card])
    assert {'q': 'Which card keeps the order rule? A card no one filed', 'a': card['a']} in flashcards
    assert [u['link'] for u in done['links_unlinked'] if u.get('into') == EXISTING] == ['A card no one filed']


def test_a_link_in_a_review_card_swap_is_relinked(tmp_path):
    swap = {'flashcard': 'Q1', 'q': 'How does a reader pass old items?', 'a': f'By a jump ahead, as {NOBODY} says.'}
    check = lambda p: keep_all(p, _patches={EXISTING: {'propositions': list(LANTERN['propositions']),  # noqa: E731
                                                        'flashcards': list(LANTERN['flashcards']),
                                                        'flashcard_replace': [swap]}})
    done, _, flashcards = patching(tmp_path, check=check)
    assert flashcards[0] == {'q': swap['q'], 'a': 'By a jump ahead, as A card no one filed says.'}
    assert [u['link'] for u in done['links_unlinked'] if u.get('into') == EXISTING] == ['A card no one filed']


def test_a_link_in_a_replaced_proposition_is_relinked(tmp_path):
    by = LANTERN['propositions'][0].rstrip('.') + f', as {NOBODY} says.'
    check = lambda p: keep_all(p, _patches={EXISTING: {'propositions': list(LANTERN['propositions'][1:]),  # noqa: E731
                                                        'flashcards': list(LANTERN['flashcards']),
                                                        'replace': [{'proposition': 'P2', 'by': by}]}})
    done, props, _ = patching(tmp_path, check=check, first=by)  # the merge check replaces with the card's words
    assert props[1] == by.replace(NOBODY, 'A card no one filed')
    assert [u['link'] for u in done['links_unlinked'] if u.get('into') == EXISTING] == ['A card no one filed']

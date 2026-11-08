"""A card of the patch set whose propositions the existing card all says opens no split slot (decision D-24).

When the patch for an existing card is too large, each card that extends it gets a split slot with its own part of
the patch. A card whose propositions the merge check found the existing card says (``already``) and which carries
only review cards has nothing to rewrite: a slot for it made the split producer write a card from review cards alone
(SB5-gd sp01 in the h7v3 replay). Now its review cards go into the existing card as a patch of their own; when they
would take that card past 8 review cards, they are held (D-20): the card is written next to it, needs review, and is
listed with the reason. Synthetic cards only.
"""
from __future__ import annotations

import copy

from secondbrain import cards as cardfmt
from tests.helpers import EXISTING
from tests.ingest_helpers import CARDS, keep_all
from tests.test_invariants import LEVELS, two_chunk_producer
from tests.test_patch_set import card_of, job, producer, reviewer, run, sb_existing, unchanged

LANTERN, ORDER = CARDS['lantern-queue'], CARDS['queue-order']
THIRD = {'q': 'What lights a marker?', 'a': 'A writer, every eight items.'}
BOTH_EXTEND = {**LEVELS, 'queue-order': {'overlap': 'extends', 'with': [EXISTING]}}


def three_review_cards(task):
    out = copy.deepcopy(two_chunk_producer(task))
    for c in out.get('cards') or []:
        if c['slug'] == 'lantern-queue':
            c['flashcards'] = list(c['flashcards']) + [THIRD]
        if c['slug'] == 'queue-order':
            c['relations'] = [{'type': 'similar_to', 'target': EXISTING, 'note': 'n'}]
    return out


def produce():
    inner = producer()

    def respond(task):
        return inner(task) if task.chunk.startswith('sp') or task.chunk.startswith('dg') else three_review_cards(task)
    return respond


def check(p):
    """lantern-queue: both propositions already in the existing card (P1, P2), three review cards carried;
    queue-order: its proposition and review cards carried. 12 + 1 propositions: too large, a split is needed."""
    [group] = p['patch_set']
    lantern = next(c for c in group['from'] if c['slug'] == 'lantern-queue')
    order = next(c for c in group['from'] if c['slug'] == 'queue-order')
    return keep_all(p, _patches={EXISTING: {
        'propositions': list(order['propositions']),
        'flashcards': list(lantern['flashcards']) + list(order['flashcards']),
        'already': [{'text': t, 'as': f'P{i}'} for i, t in enumerate(lantern['propositions'], 1)]}})


def test_a_card_left_with_review_cards_only_opens_no_split_slot_and_its_review_cards_go_into_the_card(tmp_path):
    root, job_id, before = job(tmp_path, n_props=12)
    seen = []
    done = run(root, job_id, reviewer(check=check, levels=BOTH_EXTEND), _counting(produce(), seen))
    assert done['status'] == 'applied', done.get('pending')
    assert seen == ['sp01'] and done['patch_set'] == {'lantern-queue': 'patched', 'queue-order': 'split'}
    old = card_of(root, EXISTING)
    assert len(cardfmt.propositions_of(old.body)) == 12  # its propositions as they were
    assert cardfmt.flashcards_of(old.body)[2:] == list(LANTERN['flashcards']) + [THIRD]
    [merged] = done['merged_into_existing']
    assert merged['from'] == ['lantern-queue'] and merged['propositions'] == [] and merged['flashcards'] == 3
    assert [a['as'] for a in merged['already']] == ['P1', 'P2']


def test_review_cards_that_would_take_the_card_past_eight_are_held_and_the_card_is_written_next_to_it(tmp_path):
    root, job_id, _ = job(tmp_path, n_props=12)
    before = sb_existing(root, 12, n_flashcards=6)  # 6 + 3 review cards: past 8
    seen = []
    done = run(root, job_id, reviewer(check=check, levels=BOTH_EXTEND), _counting(produce(), seen))
    assert done['status'] == 'applied', done.get('pending')
    assert seen == ['sp01'] and done['patch_set'] == {'lantern-queue': 'written', 'queue-order': 'split'}
    assert unchanged(root, before)  # nothing went in (back links of new cards aside)
    [row] = [n for n in done['needs_review'] if n['title'] == LANTERN['title']]
    assert row['with'] == EXISTING and 'past 8 review cards' in row['not_merged']
    [held] = [h for h in done['patches_held'] if h.get('review_cards_only')]
    assert held['target'] == EXISTING and held['cards'] == ['lantern-queue']
    lantern = next(p['id'] for p in done['produced'] if p['slug'] == 'lantern-queue')
    assert THIRD in cardfmt.flashcards_of(card_of(root, lantern).body)  # not lost


def _counting(respond, seen):
    def wrapped(task):
        if task.chunk.startswith('sp'):
            seen.append(task.chunk)
        return respond(task)
    return wrapped

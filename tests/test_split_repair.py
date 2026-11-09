"""A split card is repaired in the same place as a chunk's cards, by the role table.

The repairs ran only for the chunk producer, so the same mechanical slip (a quote that lost the source's bold marks)
was repaired in a chunk's answer and refused in a split card, which then cost a round. Now every role that writes cards
(``AnswerRole.cards``) has its cards repaired in one place before they are checked: the split producer by the text
repairs (title, card id, quote); its relations are left to its own check, which asks for a typed link to the card it
narrows. The source is the invented two-chunk source of tests/test_invariants.py with its first sentence in bold.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

from secondbrain import jobs
from secondbrain.pipelines import ingest, split
from tests.ingest_helpers import SOURCE_NAME, build_ingest_vault, with_diagrams
from tests.test_ingest import settings
from tests.test_invariants import SOURCE, two_chunk_producer
from tests.test_patch_set import LANTERN, SPLIT_CARD, reviewer, run, sb_existing

PLAIN = LANTERN['source_quotes'][0]
BOLD = PLAIN.replace('A lantern queue keeps', 'A **lantern queue** keeps')


def test_every_role_that_writes_cards_says_where_they_are_and_which_repairs_it_gets():
    makers = {r.name: (r.cards, r.repair_rules) for r in ingest.ANSWER_ROLES if r.cards}
    assert makers == {'chunk producer': ('cards', None), 'split producer': ('card', ('title', 'card_id', 'quote'))}


def test_the_same_quote_without_its_bold_marks_is_repaired_in_a_chunk_and_in_a_split_card(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    settings(chunk_chars=1000)
    (root / '00_Inbox' / SOURCE_NAME).write_text(SOURCE.replace(PLAIN, BOLD), encoding='utf-8')
    job_id = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)['job_id']
    sb_existing(root, 11)  # lantern-queue's patch would pass 12 propositions: it is rewritten in a split slot
    asked = []

    def produce(task):
        if split.is_slot(task.chunk.split('.')[0]):
            asked.append(task.chunk)
            return {'card': copy.deepcopy(SPLIT_CARD)}  # its quote lost the bold marks too
        plain = {**task.packet, 'chunk': {**task.packet['chunk'], 'text': task.packet['chunk']['text'].replace(
            BOLD, PLAIN)}}
        return two_chunk_producer(SimpleNamespace(packet=plain))  # quotes as if the source had no bold marks
    done = run(root, job_id, reviewer(), with_diagrams(produce))
    assert done['status'] == 'applied', done.get('pending')
    assert asked == ['sp01']  # the split card was taken on its first answer
    state = jobs.load(root, job_id)
    chunk = jobs.artifact(root, state, 'candidate', 'c01')
    queue = next(c for c in chunk['cards'] if c['slug'] == 'lantern-queue')
    assert queue['source_quotes'] == [BOLD]
    card = jobs.artifact(root, state, 'candidate', 'sp01')
    assert card['card']['source_quotes'] == [BOLD]
    assert [(r['rule'], r['before'], r['after']) for r in card['program_repairs']] == [('quote', PLAIN, BOLD)]
    assert {('sp01', 'quote')} <= {(r['slot'], r['rule']) for r in done['program_repairs']}

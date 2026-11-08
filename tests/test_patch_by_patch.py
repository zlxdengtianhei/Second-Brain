"""The merge check's last answer is kept patch by patch (decision D-20).

In the H7 rerun one review card of one patch was close to a review card of its existing card; the merge check's
answer was sent back three times, the check was skipped, and every card that extends an existing card of that
source was written next to its card. Now, when the last answer the merge check may give still fails the program
checks in some patches only, the patches that pass go into their cards; the cards of the others are written next
to their existing cards, needs-review, and the owner's list says what the program found. Each refusal and the
final split are in the job result.
"""
from __future__ import annotations

import copy
import json

from secondbrain import cards as cardfmt
from secondbrain import jobs
from secondbrain import layout as layouts
from secondbrain.pipelines import source_check
from secondbrain.service import Service
from tests.helpers import EXISTING
from tests.ingest_helpers import CARDS, keep_all
from tests.test_invariants import FILLER_NOTE, LEVELS, two_chunk_producer
from tests.test_patch_set import card_of, job, producer, reviewer, run, unchanged

LIT = CARDS['lit-marker-cost']


def another_existing(root, cid, title, flashcards):
    """A second existing card in sb format, filed like card_exist."""
    path = next((root / '10_Knowledge').rglob(f'{EXISTING}.md'))
    head = path.read_bytes().decode('utf-8').split('\n---\n', 1)[0].replace(f'id: {EXISTING}', f'id: {cid}') \
        .replace('title: 已有卡', f'title: {title}') + '\n---\n'
    body = ('## 本质\n另一张已有卡。\n\n## 命题\n- P1. An old point that nothing repeats.\n\n## 相关\n暂无。\n\n'
            '## 复习卡\n' + '\n'.join(f'{q}::{a}' for q, a in flashcards) + '\n')
    new = path.with_name(f'{cid}.md')
    new.write_bytes((head + body).encode('utf-8'))
    registry_path = root / layouts.DEFAULT.registry
    registry = json.loads(registry_path.read_text(encoding='utf-8'))
    registry['notes'].append({'id': cid, 'path': str(new.relative_to(root)), 'title': title,
                              'domain': registry['notes'][0]['domain']})
    registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding='utf-8')
    return new.read_bytes()


def three_patches(tmp_path):
    """lantern-queue extends card_exist, filler-note card_b, lit-marker-cost card_c, whose review card asks what
    lit-marker-cost's first one asks: that patch collides in every answer."""
    root, job_id, before = job(tmp_path)
    b = another_existing(root, 'card_b', 'Filler cards', [('What is an old filler question?', 'Old.')])
    c = another_existing(root, 'card_c', 'Marker costs', [(LIT['flashcards'][0]['q'], 'Every eighth item.')])

    def produce(task):
        out = copy.deepcopy(two_chunk_producer(task))  # the fixture cards are shared
        for card in out.get('cards') or []:
            extra = {'filler-note': 'card_b', 'lit-marker-cost': 'card_c'}.get(card['slug'])
            if extra:
                card['relations'] = [*card.get('relations', []), {'type': 'similar_to', 'target': extra, 'note': 'n'}]
        return out
    levels = {**LEVELS, 'filler-note': {'overlap': 'extends', 'with': ['card_b']},
              'lit-marker-cost': {'overlap': 'extends', 'with': ['card_c']}}
    return root, job_id, {EXISTING: before, 'card_b': b, 'card_c': c}, produce, levels


def test_three_patches_one_always_collides_two_go_in_and_one_waits_for_the_owner(tmp_path):
    root, job_id, before, produce, levels = three_patches(tmp_path)
    checks = []
    done = run(root, job_id, reviewer(checks, levels=levels), producer_with(produce))
    assert done['status'] == 'applied', done.get('pending')
    assert sorted(g['target'] for g in checks[0].packet['patch_set']) == sorted([EXISTING, 'card_b', 'card_c'])
    assert len(checks) == 3                                   # max_invalid_retries = 2: the third answer is the last
    assert len(done['source_check_refusals']) == 2            # each refusal is in the result
    assert all("asks what card_c Q1 asks" in r['message'] for r in done['source_check_refusals'])
    assert done['patch_set'] == {'lantern-queue': 'patched', 'filler-note': 'patched', 'lit-marker-cost': 'written'}
    [held] = done['patches_held']                              # the final split
    assert held['target'] == 'card_c' and held['cards'] == ['lit-marker-cost']
    assert any('asks what card_c Q1 asks' in p for p in held['problems'])
    assert done['source_check']['status'] == 'checked'
    # two cards took their patches
    assert LANTERN_PROPS <= set(cardfmt.propositions_of(card_of(root, EXISTING).body))
    assert set(FILLER_NOTE['propositions']) <= set(cardfmt.propositions_of(card_of(root, 'card_b').body))
    # the third card is unchanged; its extending card is written next to it, needs-review, with what collided
    c = card_of(root, 'card_c')
    assert cardfmt.propositions_of(c.body) == ['An old point that nothing repeats.']
    written = next(x for x in read_cards(root) if x.title == LIT['title'])
    assert written.fm['status'] == 'needs-review' and 'possible_repeat' in written.fm['review_reason']
    assert done['outcomes']['lit-marker-cost'] == 'write'
    [row] = [r for r in done['needs_review'] if r['id'] == written.id]
    assert row['with'] == 'card_c' and 'asks what card_c Q1 asks' in row['not_merged']
    items = [i for i in Service(root).owner_items() if i.get('id') == written.id]
    assert [i['owner_reason'] for i in items] == ['possible_repeat']
    assert 'asks what card_c Q1 asks' in items[0]['detail']


def test_salvage_removes_disputes_of_a_held_patch_so_the_other_two_still_go_in(tmp_path):
    root, job_id, before, produce, levels = three_patches(tmp_path)
    checks = []

    def check(packet):
        answer = keep_all(packet)
        answer['disputes'] = [{'slug': 'lit-marker-cost', 'reason': 'its patch collides with the existing card'}]
        return answer

    done = run(root, job_id, reviewer(checks, check=check, levels=levels), producer_with(produce))
    assert done['status'] == 'applied', done.get('pending')
    assert len(checks) == 3
    assert done['patch_set'] == {'lantern-queue': 'patched', 'filler-note': 'patched', 'lit-marker-cost': 'written'}
    [held] = done['patches_held']
    assert held['target'] == 'card_c' and held['cards'] == ['lit-marker-cost']
    assert LANTERN_PROPS <= set(cardfmt.propositions_of(card_of(root, EXISTING).body))
    assert set(FILLER_NOTE['propositions']) <= set(cardfmt.propositions_of(card_of(root, 'card_b').body))
    assert cardfmt.propositions_of(card_of(root, 'card_c').body) == ['An old point that nothing repeats.']
    written = next(x for x in read_cards(root) if x.title == LIT['title'])
    assert written.fm['status'] == 'needs-review' and 'possible_repeat' in written.fm['review_reason']


def test_a_problem_outside_the_patches_still_refuses_the_whole_answer():
    group = {'target': 'kc_old', 'card': {'propositions': ['An old point.'], 'flashcards': []},
             'from': [{'slug': 'new-card', 'propositions': ['A new point.'], 'flashcards': []}]}
    frozen = {'cards': [], 'new_topics': [], 'patch_set': [group]}
    good = {'target': 'kc_old', 'propositions': ['A new point.']}
    bad = {'target': 'kc_old', 'propositions': ['A new point.'], 'flashcards': [{'q': 'Not carried?', 'a': 'x'}]}
    assert source_check.salvage(frozen, {'cards': [], 'new_topics': [], 'patches': [good]})['patches'] == [good]
    saved = source_check.salvage(frozen, {'cards': [], 'new_topics': [], 'patches': [bad]})
    assert saved['patches'] == [] and saved['held'][0]['target'] == 'kc_old'
    assert saved['held'][0]['cards'] == ['new-card'] and 'patches[0].flashcards' in saved['held'][0]['problems'][0]
    outside = {'cards': [], 'new_topics': [{'id': 'nt9', 'overlap': 'distinct'}], 'patches': [bad]}
    assert source_check.salvage(frozen, outside) is None                         # a problem outside the patches
    assert source_check.salvage(frozen, {'cards': 'x', 'new_topics': []}) is None  # the shape


def test_before_the_last_answer_a_colliding_patch_is_still_sent_back(tmp_path):
    root, job_id, before, produce, levels = three_patches(tmp_path)
    checks = []

    def check(packet):  # the second answer leaves the colliding review card out
        if len(checks) == 1:
            return keep_all(packet)
        return keep_all(packet, _patches={'card_c': {'propositions': list(LIT['propositions']),
                                                     'flashcards': LIT['flashcards'][1:]}})
    done = run(root, job_id, reviewer(checks, check=check, levels=levels), producer_with(produce))
    assert len(checks) == 2 and done['patches_held'] == [] and len(done['source_check_refusals']) == 1
    assert done['patch_set']['lit-marker-cost'] == 'patched'


LANTERN_PROPS = set(CARDS['lantern-queue']['propositions'])


def producer_with(produce):
    from tests.ingest_helpers import with_diagrams
    from secondbrain.pipelines import split
    from tests.test_patch_set import SPLIT_CARD

    def respond(task):
        if split.is_slot(task.chunk.split('.')[0]):
            return {'card': SPLIT_CARD}
        return produce(task)
    return with_diagrams(respond)


def read_cards(root):
    from secondbrain.vault import read_snapshot
    return list(read_snapshot(root).cards.values())

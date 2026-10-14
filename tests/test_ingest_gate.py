"""The second drill's judged defects (J4): domain-only relation types, near-duplicates, spaces, conflicts."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from secondbrain import cli, config, jobs, labels
from secondbrain import layout as layouts
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest
from secondbrain.service import Service
from secondbrain.vault import read_snapshot
from tests.helpers import DOMAIN, EXISTING
from tests.ingest_helpers import (CARDS, FIXED_DIMMING, OTHER_CARD, OTHER_DOMAIN, OTHER_PILLAR, OTHER_TOPIC,
                                  SOURCE_NAME, build_ingest_vault, first_candidate, first_review, repair_candidate,
                                  repair_review, scripted)

def with_domain_words(root):
    """tool_for / used_by open only to the pebbles domain, as `domain_relations` opens words to one domain."""
    path = root / layouts.DEFAULT.catalog
    catalog = json.loads(path.read_text(encoding='utf-8'))
    catalog['domain_relations'] = {OTHER_DOMAIN: ['tool_for', 'used_by']}
    catalog['inverse_relations'].update({'tool_for': 'used_by', 'used_by': 'tool_for'})
    path.write_text(json.dumps(catalog), encoding='utf-8')
    return root


def check(root, candidate):
    """The producer gate of a fresh job on ``root``, as `sb job submit` runs it."""
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    state, packet = jobs.load(root, job['job_id']), jobs.read_input(root, job['job_id'])
    return ingest.validate_submission(packet, candidate, role='producer', root=root, chunk='c01', state=state)


def pebble_card(**fields):
    return {**CARDS['queue-order'], 'slug': 'pebble-order', 'title': 'Pebbles keep their order',
            'domain': OTHER_DOMAIN, 'pillar': OTHER_PILLAR, 'topic': OTHER_TOPIC, **fields}


def candidate_with(*cards):
    rows = [{'source_quote': c['source_quotes'][0], 'disposition': 'card', 'cards': [c['slug']], 'reason': 'r'}
            for c in cards]
    return {'cards': list(cards), 'new_topics': [], 'coverage': rows}


# --- J-1: a type in domain_relations is open to its domain only, both ways -----------------------
def test_a_domain_only_type_is_refused_on_a_card_of_another_domain(tmp_path):
    root = with_domain_words(build_ingest_vault(tmp_path / 'vault'))
    card = {**CARDS['queue-order'], 'relations': [{'type': 'tool_for', 'target': EXISTING}]}
    with pytest.raises(ingest.IngestError, match=f"type must be one of .* \\(the types open to {DOMAIN}; got 'tool_for'"):
        check(root, candidate_with(card))


def test_a_domain_only_type_is_refused_when_its_back_link_lands_in_another_domain(tmp_path):
    root = with_domain_words(build_ingest_vault(tmp_path / 'vault'))
    across = pebble_card(relations=[{'type': 'tool_for', 'target': EXISTING}])
    with pytest.raises(ingest.IngestError, match=f"back link 'used_by' is not open to the target's domain {DOMAIN}"):
        check(root, candidate_with(across))
    within = pebble_card(relations=[{'type': 'tool_for', 'target': OTHER_CARD}])
    assert check(root, candidate_with(within))  # both cards are pebbles


def test_packets_show_a_domain_only_type_only_for_its_domain(tmp_path):
    root = with_domain_words(build_ingest_vault(tmp_path / 'vault'))
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    state, packet = jobs.load(root, job['job_id']), jobs.read_input(root, job['job_id'])
    produce = ingest.producer_packet(root, state, packet, 'c01')
    assert 'tool_for' not in produce['relation_types'] and 'similar_to' in produce['relation_types']
    assert OTHER_DOMAIN in packet['chunks'][0]['candidate_domains']  # three domains, all candidates
    assert set(produce['domain_relation_types']) == {OTHER_DOMAIN}
    assert set(produce['domain_relation_types'][OTHER_DOMAIN]) == {'tool_for', 'used_by'}
    assert ingest._closed_relation(packet['catalog'], 'tool_for', OTHER_DOMAIN, OTHER_DOMAIN) is None
    assert 'not open to domain' in ingest._closed_relation(packet['catalog'], 'tool_for', DOMAIN, OTHER_DOMAIN)


# --- one relation between two cards ------------------------------------------------------------
def test_two_relations_between_the_same_two_cards_are_refused(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    base = first_candidate()
    cost = base['cards'][1]  # lit-marker-cost: depends_on lantern-queue
    both = {**cost, 'relations': cost['relations'] + [{'type': 'part_of', 'target': 'lantern-queue'}]}
    with pytest.raises(ingest.IngestError, match="2 relations between 'lantern-queue' and 'lit-marker-cost'"):
        check(root, {**base, 'cards': [base['cards'][0], both] + base['cards'][2:]})
    # the same pair through the back link: lantern-queue part_of lit-marker-cost meets its depends_on
    queue = {**base['cards'][0], 'relations': [{'type': 'part_of', 'target': 'lit-marker-cost'}]}
    with pytest.raises(ingest.IngestError, match='counting the back links'):
        check(root, {**base, 'cards': [queue] + base['cards'][1:]})


# --- J-2: an accepted card that links existing cards needs an explicit overlap decision ------------
def test_an_accepted_card_linking_an_existing_card_needs_an_overlap_decision():
    candidate = first_candidate()
    review = first_review()
    del review['cards'][0]['overlap']
    with pytest.raises(ingest.IngestError, match=r'lantern-queue: the card links existing cards \(card_exist\)'):
        ingest.validate_review(candidate, review, {EXISTING})
    review['cards'][0].update(overlap='needs_review', overlap_with=OTHER_CARD)
    with pytest.raises(ingest.IngestError, match='overlap_with must name the existing card it repeats'):
        ingest.validate_review(candidate, review, {EXISTING, OTHER_CARD})
    review['cards'][0]['overlap_with'] = EXISTING
    assert ingest.validate_review(candidate, review, {EXISTING, OTHER_CARD})
    review['cards'][3]['overlap'] = 'maybe'
    with pytest.raises(ingest.IngestError, match='overlap must be distinct or needs_review'):
        ingest.validate_review(candidate, review, {EXISTING})


def run(root, review):
    produce, respond, _ = scripted([first_candidate(), repair_candidate()], [review, repair_review()])
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    return ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=respond))


def test_a_card_the_reviewer_says_mostly_repeats_one_is_written_as_needs_review(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    review = first_review()
    review['cards'][0].update(overlap='needs_review', overlap_with=EXISTING, reason='repeats the existing card')
    result = run(root, review)
    assert result['status'] == 'applied'
    queue = next(p for p in result['produced'] if p['slug'] == 'lantern-queue')
    assert result['needs_review'] == [{'id': queue['id'], 'title': 'Lantern queue', 'overlap_with': EXISTING,
                                       'reason': 'repeats the existing card'}]
    cards = {c.title: c for c in read_snapshot(root).cards.values()}
    assert cards['Lantern queue'].fm['status'] == 'needs-review'
    assert cards['Lit marker memory cost'].fm['status'] == 'confirmed'  # judged distinct
    assert ('similar_to', EXISTING) in {(r['type'], r['target']) for r in cards['Lantern queue'].fm['relations']}


# --- J-4: a disagreement with an existing card is recorded, shown and never merged ---------------
def test_a_conflict_with_an_existing_card_is_recorded_and_listed_until_that_card_changes(tmp_path, capsys):
    root = build_ingest_vault(tmp_path / 'vault')
    existing = read_snapshot(root).cards[EXISTING]
    before = (root / existing.path).read_bytes()
    review = first_review()
    review['cards'][1]['conflicts'] = [{'with': EXISTING, 'detail': 'it says 0.51/0.49; the text gives 0.88/0.12'}]
    result = run(root, review)
    cost = next(p for p in result['produced'] if p['slug'] == 'lit-marker-cost')
    assert result['conflicts'] == [{'card': cost['id'], 'slug': 'lit-marker-cost', 'title': 'Lit marker memory cost',
                                    'with': EXISTING, 'with_title': existing.title,
                                    'detail': 'it says 0.51/0.49; the text gives 0.88/0.12', 'by': 'reviewer',
                                    'chunk': 'c01'}]
    rows = [json.loads(line) for line in (root / layouts.DEFAULT.conflicts).read_text(encoding='utf-8').splitlines()]
    assert [(r['with'], r['job_id']) for r in rows] == [(EXISTING, result['job_id'])]
    # the existing card got back links only; its text is as it was
    assert ingest.content_digest(read_snapshot(root).cards[EXISTING]) == ingest.content_digest(existing)
    # the HOME this write leaves counts the conflict it records (the record is not on disk yet when HOME is made)
    assert labels.pages()['conflicts'].format(i=4, n=1) in (root / 'HOME.md').read_text(encoding='utf-8')
    assert (root / existing.path).read_bytes() != before
    assert cli.main(['--vault', str(root), 'status']) == 0
    status = json.loads(capsys.readouterr().out)
    assert [(c['with'], c['card']) for c in status['conflicts']] == [(EXISTING, cost['id'])]
    assert 'until the existing card' in status['conflicts_hint']
    # a later back link (related section) does not settle it; editing the card's text does
    path = root / existing.path
    path.write_text(path.read_text(encoding='utf-8') + '\n## 相关\n- similar_to:: [[Lantern queue]]\n',
                    encoding='utf-8')
    assert len(Service(root).status()['conflicts']) == 1
    path.write_text(path.read_text(encoding='utf-8').replace('已有卡正文', '已有卡正文（已核对）'), encoding='utf-8')
    status = Service(root).status()
    assert status['conflicts'] == [] and 'conflicts_hint' not in status


def test_a_conflict_the_producer_flagged_is_recorded_even_if_the_reviewer_does_not_repeat_it(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    candidate = first_candidate()
    candidate['cards'][0] = {**candidate['cards'][0],
                             'conflicts': [{'with': EXISTING, 'detail': 'it says queues reorder items'}]}
    produce, respond, _ = scripted([candidate, repair_candidate()], [first_review(), repair_review()])
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                            reviewer=FakeExecutor('claude-test', responder=respond))
    assert [(c['slug'], c['with'], c['by']) for c in result['conflicts']] == [('lantern-queue', EXISTING, 'producer')]
    queue = next(c for c in read_snapshot(root).cards.values() if c.title == 'Lantern queue')
    assert 'reorder' not in queue.body  # a conflict is never written into the card


def test_a_producer_conflict_must_name_an_existing_card(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    card = {**CARDS['queue-order'], 'conflicts': [{'with': 'card_missing', 'detail': 'says otherwise'}]}
    with pytest.raises(ingest.IngestError, match=r'conflicts\[0\] needs "with" \(an existing card id\)'):
        check(root, candidate_with(card))
    card['conflicts'] = [{'with': EXISTING, 'detail': 'says otherwise'}]
    assert check(root, candidate_with(card))


# --- J-3: runs of spaces reach the card file ------------------------------------------------------
def test_runs_of_spaces_in_a_card_reach_the_written_file(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    candidate = first_candidate()
    candidate['cards'][0] = {**candidate['cards'][0], 'propositions': [
        'Items stay in arrival order.', 'split() reads `3  10 -2` as three numbers.']}
    produce, respond, _ = scripted([candidate, {'cards': [FIXED_DIMMING], 'new_topics': [],
                                                'coverage': repair_candidate()['coverage']}],
                                   [first_review(), repair_review()])
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                   reviewer=FakeExecutor('claude-test', responder=respond))
    queue = next(c for c in read_snapshot(root).cards.values() if c.title == 'Lantern queue')
    assert '- P2. split() reads `3  10 -2` as three numbers.' in queue.body


# --- drill-log P4: a slug that would take an existing card's id ----------------------------------
def test_a_slug_whose_id_an_existing_card_has_is_refused(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    config.activate(replace(config.current(), ingest=replace(config.current().ingest, id_prefix='card_')))
    card = {**CARDS['queue-order'], 'slug': 'exist'}  # card_ + exist = the existing card_exist
    with pytest.raises(ingest.IngestError, match="slug 'exist' would make the id card_exist, which an existing card has"):
        check(root, candidate_with(card))
    assert check(root, candidate_with({**card, 'slug': 'arrival-order'}))


# --- drill-log P6: links to cards that were not written ---------------------------------------
def test_a_link_to_a_card_judged_a_duplicate_goes_to_the_card_it_repeats(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    candidate = first_candidate()
    candidate['cards'][0] = {**candidate['cards'][0], 'relations': candidate['cards'][0]['relations'] + [
        {'type': 'depends_on', 'target': 'queue-order', 'note': 'order comes first'}]}
    review = first_review()
    review['cards'][3]['duplicate_of'] = OTHER_CARD  # queue-order repeats the pebble card (for the test)
    produce, respond, _ = scripted([candidate, repair_candidate()], [review, repair_review()])
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                            reviewer=FakeExecutor('claude-test', responder=respond))
    assert result['status'] == 'applied' and result['skipped_relations'] == []
    cards = {c.id: c for c in read_snapshot(root).cards.values()}
    queue = next(c for c in cards.values() if c.title == 'Lantern queue')
    assert {'type': 'depends_on', 'target': OTHER_CARD, 'note': 'order comes first'} in queue.fm['relations']
    assert {'type': 'depends_on', 'target': queue.id} in cards[OTHER_CARD].fm['relations']  # back link


def pending_link(root, placement=None):
    """A partial parent whose lit-marker-cost links marker-dimming, which stays pending; returns (job, cost id, repair)."""
    dimming = {**CARDS['marker-dimming'], 'relations': []}
    cost = {**CARDS['lit-marker-cost'], 'relations': CARDS['lit-marker-cost']['relations'] + [
        {'type': 'similar_to', 'target': 'marker-dimming', 'note': 'both about markers'}]}
    first = first_candidate()
    first['cards'] = [first['cards'][0], cost, dimming, first['cards'][3]]
    fixed = {**FIXED_DIMMING, 'relations': []}
    repair = {'cards': [fixed], 'new_topics': [], 'coverage': repair_candidate()['coverage']}
    produce, respond, _ = scripted([first, repair], [first_review(), repair_review('revise')])
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME, placement=placement)
    parent = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                            reviewer=FakeExecutor('claude-test', responder=respond))
    assert parent['status'] == 'partial'
    cost_id = next(p['id'] for p in parent['produced'] if p['slug'] == 'lit-marker-cost')
    assert [(r['from'], r['target_slug']) for r in parent['skipped_relations']] == [(cost_id, 'marker-dimming')]
    return job['job_id'], cost_id, repair


def retried(root, job_id, candidates, reviews):
    child = ingest.retry(root, job_id)
    produce, respond, _ = scripted(candidates, reviews)
    return ingest.advance(root, child['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=respond))


def verdict(decision, **fields):
    return {'cards': [{'slug': 'marker-dimming', 'decision': decision, 'reason': 'r', **fields}],
            'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}


def test_a_link_to_a_pending_card_is_restored_when_a_retry_writes_it(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    job_id, cost_id, repair = pending_link(root)
    child = ingest.retry(root, job_id)
    assert jobs.read_input(root, child['job_id'])['retry']['skipped_relations'] == [
        {'from': cost_id, 'type': 'similar_to', 'target_slug': 'marker-dimming'}]
    produce, respond, _ = scripted([repair], [verdict('accept')])
    done = ingest.advance(root, child['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=respond))
    assert done['status'] == 'applied'
    new_id = next(p['id'] for p in done['produced'] if p['slug'] == 'marker-dimming')
    assert done['restored_relations'] == [{'from': cost_id, 'type': 'similar_to', 'target_slug': 'marker-dimming',
                                           'target': new_id}]
    cards = read_snapshot(root).cards
    assert {'type': 'similar_to', 'target': cost_id} in cards[new_id].fm['relations']
    assert {'type': 'similar_to', 'target': new_id} in cards[cost_id].fm['relations']
    assert '[[Marker dimming]]' in cards[cost_id].body  # and in its related section


# drill 3: a retry child got 7 carried links and reported none of them, restored or not
def test_a_carried_link_the_new_card_already_makes_is_reported_as_linked(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    job_id, cost_id, repair = pending_link(root)
    linking = {**repair, 'cards': [{**repair['cards'][0], 'relations': [
        {'type': 'part_of', 'target': cost_id, 'note': 'dimming acts on the markers it costs'}]}]}
    done = retried(root, job_id, [linking], [verdict('accept', overlap='distinct')])
    new_id = next(p['id'] for p in done['produced'] if p['slug'] == 'marker-dimming')
    assert done['restored_relations'] == [{'from': cost_id, 'type': 'similar_to', 'target_slug': 'marker-dimming',
                                           'target': new_id,
                                           'linked_as': {'from': new_id, 'type': 'part_of', 'target': cost_id}}]
    assert done['skipped_relations'] == []
    assert [r for r in read_snapshot(root).cards[cost_id].fm['relations'] if r['target'] == new_id] == [
        {'type': 'part_of', 'target': new_id}]  # one relation per pair


def test_a_carried_link_to_a_card_judged_a_duplicate_goes_to_the_card_it_repeats(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    job_id, cost_id, repair = pending_link(root)
    done = retried(root, job_id, [repair], [verdict('duplicate', duplicate_of=OTHER_CARD)])
    assert done['status'] == 'applied' and done['produced'] == []
    assert done['restored_relations'] == [{'from': cost_id, 'type': 'similar_to', 'target_slug': 'marker-dimming',
                                           'target': OTHER_CARD, 'redirected_from': 'marker-dimming'}]
    cards = read_snapshot(root).cards
    assert {'type': 'similar_to', 'target': OTHER_CARD} in cards[cost_id].fm['relations']
    assert {'type': 'similar_to', 'target': cost_id} in cards[OTHER_CARD].fm['relations']


def test_a_carried_link_whose_target_is_dropped_is_listed_and_not_carried_again(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    job_id, cost_id, _ = pending_link(root)
    dropped = {'cards': [], 'new_topics': [], 'coverage': [
        {'source_quote': FIXED_DIMMING['source_quotes'][0], 'disposition': 'out_of_scope', 'reason': 'not needed'}]}
    done = retried(root, job_id, [dropped], [{'cards': [], 'new_topics': [],
                                             'coverage': {'decision': 'accept', 'reason': 'ok'}}])
    assert done['status'] == 'applied' and done['restored_relations'] == []
    assert done['skipped_relations'] == [{'from': cost_id, 'type': 'similar_to', 'target_slug': 'marker-dimming',
                                          'reason': 'target card was dropped in review; add the link by hand '
                                                    'if it still matters'}]


def test_a_carried_link_whose_target_is_still_pending_goes_to_the_next_retry(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    job_id, cost_id, repair = pending_link(root)
    done = retried(root, job_id, [repair, repair], [verdict('revise'), verdict('revise')])
    assert done['status'] == 'partial'
    row = {'from': cost_id, 'type': 'similar_to', 'target_slug': 'marker-dimming'}
    assert done['skipped_relations'] == [{**row, 'reason': 'target card was not written'}]
    grandchild = ingest.retry(root, done['job_id'])
    assert jobs.read_input(root, grandchild['job_id'])['retry']['skipped_relations'] == [row]


def test_a_carried_link_from_a_card_deleted_since_is_listed(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    job_id, cost_id, repair = pending_link(root)
    gone = read_snapshot(root).cards[cost_id].path  # the owner deleted the card the link starts from
    (root / gone).unlink()
    registry = json.loads((root / layouts.DEFAULT.registry).read_text(encoding='utf-8'))
    registry['notes'] = [n for n in registry['notes'] if n.get('path') != gone]
    (root / layouts.DEFAULT.registry).write_text(json.dumps(registry), encoding='utf-8')
    done = retried(root, job_id, [repair], [verdict('accept')])
    assert done['restored_relations'] == []
    assert done['skipped_relations'] == [{'from': cost_id, 'type': 'similar_to', 'target_slug': 'marker-dimming',
                                          'reason': 'the card the link starts from no longer exists'}]


def test_one_conflict_recorded_by_a_parent_and_its_retry_counts_once(tmp_path):
    # V1 C7 (drill 2 copy: 5 shown, 4 real): the parent recorded it for a card it could not write, without an id
    root = build_ingest_vault(tmp_path / 'vault')
    digest = ingest.content_digest(read_snapshot(root).cards[EXISTING])
    row = {'slug': 'lit-marker-cost', 'title': 'Lit marker memory cost', 'with': EXISTING, 'detail': 'd',
           'by': 'reviewer', 'source_sha256': 'abc', 'with_digest': digest}
    path = root / layouts.DEFAULT.conflicts
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**row, 'card': None, 'job_id': 'parent'}) + '\n'
                    + json.dumps({**row, 'card': 'kc_lit-marker-cost', 'job_id': 'parent-retry1'}) + '\n'
                    + json.dumps({**row, 'slug': 'other-card', 'card': None}) + '\n', encoding='utf-8')
    conflicts = Service(root).status()['conflicts']
    assert [(c['card'], c['slug'], c['job_id']) for c in conflicts] == [
        ('kc_lit-marker-cost', 'lit-marker-cost', 'parent-retry1'), (None, 'other-card', None)]


# --- placement: the owner says where a source's cards go (J4 on drill 3: one subject in 2 domains, 8 topics) ----
PEBBLES = {'domain': OTHER_DOMAIN, 'pillar': OTHER_PILLAR, 'topic': OTHER_TOPIC}


def test_a_placement_is_frozen_with_the_job_and_shown_to_producers_and_reviewers(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME, placement=PEBBLES)
    packet = jobs.read_input(root, job['job_id'])
    assert packet['placement'] == PEBBLES
    assert all(c['candidate_domains'][0] == OTHER_DOMAIN for c in packet['chunks'])
    again = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME, placement={'domain': DOMAIN})
    assert again['resumed'] and 'keeps that placement' in again['placement_note']
    produce, respond, seen = scripted([first_candidate(), repair_candidate()], [first_review(), repair_review()])
    ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                   reviewer=FakeExecutor('claude-test', responder=respond))
    assert seen['producer'][0].packet['placement'] == PEBBLES and seen['reviewer'][0].packet['placement'] == PEBBLES
    assert OTHER_DOMAIN in seen['producer'][0].packet['candidate_taxonomies']


@pytest.mark.parametrize('place, message', [
    ({'domain': 'nowhere'}, 'not a catalog domain'),
    ({'domain': OTHER_DOMAIN, 'pillar': 'Nope'}, 'not a pillar of pebbles'),
    ({'domain': OTHER_DOMAIN, 'pillar': OTHER_PILLAR, 'topic': 'Colours'}, 'add it to the pillar'),
    ({'domain': OTHER_DOMAIN, 'topic': OTHER_TOPIC}, 'then optionally a pillar'),
])
def test_a_placement_must_name_what_the_taxonomy_has(tmp_path, place, message):
    root = build_ingest_vault(tmp_path / 'vault')
    with pytest.raises(ingest.IngestError, match=message):
        ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME, placement=place)


def test_the_cli_takes_a_placement_and_a_retry_keeps_it(tmp_path, capsys):
    root = build_ingest_vault(tmp_path / 'vault')
    assert cli.main(['--vault', str(root), 'ingest', str(root / '00_Inbox' / SOURCE_NAME), '--prepare-only',
                     '--domain', OTHER_DOMAIN, '--pillar', OTHER_PILLAR]) == 0
    [job] = json.loads(capsys.readouterr().out)
    assert jobs.read_input(root, job['job_id'])['placement'] == {'domain': OTHER_DOMAIN, 'pillar': OTHER_PILLAR}
    # V1 C5: going on with another placement keeps the frozen one, and the result says so next to the hand-offs
    assert cli.main(['--vault', str(root), 'ingest', str(root / '00_Inbox' / SOURCE_NAME), '--executor', 'host',
                     '--domain', DOMAIN]) == 0
    [going] = json.loads(capsys.readouterr().out)
    assert going['handoffs'] and 'keeps that placement' in going['placement_note']
    other = build_ingest_vault(tmp_path / 'other')
    job_id, _, _ = pending_link(other, placement=PEBBLES)
    child = ingest.retry(other, job_id)
    packet = jobs.read_input(other, child['job_id'])
    assert packet['placement'] == PEBBLES and packet['chunks'][0]['candidate_domains'][0] == OTHER_DOMAIN
    assert cli.main(['--vault', str(other), 'ingest', '--retry', job_id, '--domain', DOMAIN]) != 0
    assert 'keeps the placement' in capsys.readouterr().err


# --- prompts --------------------------------------------------------------------------------------
def test_the_reviewer_is_told_about_standalone_cards_overlap_and_conflicts():
    review = (ingest.Path(ingest.__file__).parents[1] / 'prompts' / 'review_cards.md').read_text(encoding='utf-8')
    assert 'Readable without the source' in review and '本讲义' in review
    assert '`needs_review`: the card mostly repeats one existing card' in review
    assert 'the program records it for the owner' in review and 'Do not merge or correct the existing card' in review
    produce = (ingest.Path(ingest.__file__).parents[1] / 'prompts' / 'produce_cards.md').read_text(encoding='utf-8')
    assert 'Never put such a note into the card\'s propositions' in produce and 'One relation per target' in produce

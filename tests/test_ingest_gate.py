"""The second drill's judged defects (J4): domain-only relation types, near-duplicates, spaces, conflicts."""
from __future__ import annotations

import json

import pytest

from secondbrain import cli, jobs
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


# --- prompts --------------------------------------------------------------------------------------
def test_the_reviewer_is_told_about_standalone_cards_overlap_and_conflicts():
    review = (ingest.Path(ingest.__file__).parents[1] / 'prompts' / 'review_cards.md').read_text(encoding='utf-8')
    assert 'Readable without the source' in review and '本讲义' in review
    assert '`needs_review`: the card mostly repeats one existing card' in review
    assert 'the program records it for the owner' in review and 'Do not merge or correct the existing card' in review
    produce = (ingest.Path(ingest.__file__).parents[1] / 'prompts' / 'produce_cards.md').read_text(encoding='utf-8')
    assert 'Never put such a note into the card\'s propositions' in produce and 'One relation per target' in produce

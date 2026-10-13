"""S3 routing: per-passage votes pick the domain the source is about, not the biggest domain."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from secondbrain import config, index, jobs
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest
from secondbrain.pipelines.routing import passages, route
from secondbrain.vault import read_snapshot
from tests.ingest_helpers import (SOURCE_NAME, build_ingest_vault, first_candidate, first_review, repair_candidate,
                                  repair_review, scripted)
from tests.routing_fixture import SOURCE, build


def test_passages_join_headings_to_their_text():
    parts = passages('# Title\n\nFirst paragraph.\n\n## Part\n\nSecond paragraph.\nstill second.\n\n## Empty\n')
    assert parts == ['Title\nFirst paragraph.', 'Part\nSecond paragraph.\nstill second.', 'Empty']


def test_the_source_domain_wins_over_a_large_domain_with_the_same_generic_words(tmp_path):
    root = build(tmp_path / 'vault')
    snap = read_snapshot(root)
    # the trap: as one long query, the large domain fills the top hits
    votes = {}
    for rank, hit in enumerate(index.search(root, snap, SOURCE, 20)):
        votes[hit['domain']] = votes.get(hit['domain'], 0) + 20 - rank
    assert max(votes, key=votes.get) == 'agent-harness'
    routed = route(root, snap, SOURCE, domains=2)
    assert routed['domains'][0] == 'algorithms' and routed['passages'] == 6
    assert [n['domain'] for n in routed['neighbors'][:4]] == ['algorithms'] * 4
    assert routed['votes']['algorithms'] > 0.5


def test_prepare_freezes_the_routed_domains(tmp_path):
    root = build(tmp_path / 'vault')
    prepared = ingest.prepare(root, root / '00_Inbox' / 'practice.md')
    [chunk] = jobs.read_input(root, prepared['job_id'])['chunks']
    assert chunk['candidate_domains'][0] == 'algorithms'
    assert chunk['routing']['passages'] == 6 and chunk['routing']['votes']['algorithms'] > 0.5


def test_validation_errors_list_the_legal_choices(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    packet = jobs.read_input(root, prepared['job_id'])

    def check(mutate):
        candidate = json.loads(json.dumps(first_candidate()))
        mutate(candidate)
        with pytest.raises(ingest.IngestError) as err:
            ingest.validate_candidate(packet, candidate, slot='c01', other_cards=[], kept_slugs=set(),
                                      existing_ids=set(read_snapshot(root).cards), existing_titles=set())
        return str(err.value)
    message = check(lambda c: c['cards'][0].update(domain='nowhere'))
    assert 'legal domains: attention, pebbles, transformer' in message
    message = check(lambda c: c['cards'][0].update(domain='pebbles', pillar='Token routing'))
    assert 'legal topics of pebbles: Pebble basics: Sizes' in message


def test_a_repair_round_gets_the_taxonomy_of_the_domain_the_reviewer_names(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    config.activate(replace(config.current(), ingest=replace(config.current().ingest, candidate_domains=1)))
    review = first_review()
    review['cards'][2].update(suggested_domain='transformer', reason='this belongs with the transformer cards')
    produce, respond, seen = scripted([first_candidate(), repair_candidate()], [review, repair_review()])
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    [chunk] = jobs.read_input(root, prepared['job_id'])['chunks']
    routed = chunk['candidate_domains']
    assert len(routed) == 1 and routed != ['transformer']
    ingest.advance(root, prepared['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                   reviewer=FakeExecutor('claude-test', responder=respond))
    first, repair = seen['producer'][:2]
    assert set(first.packet['candidate_taxonomies']) == set(routed)
    # routed domain + the domain of the rejected card + the domain the reviewer asked for
    assert set(repair.packet['candidate_taxonomies']) == set(routed) | {'attention', 'transformer'}
    assert {d['slug'] for d in seen['reviewer'][0].packet['domains']} == {'attention', 'pebbles', 'transformer'}


def test_domain_names_in_review_reasons_count_as_requests():
    packet = {'catalog': {'domains': {'pebbles': {}, 'attention': {}, 'agent-harness': {}, 'harness': {}}}}
    findings = {'cards': [{'reason': 'move it to pebbles; not about attention-like things'}],
                'new_topics': [], 'coverage': {'reason': 'the agent-harness part is missing'}}
    assert ingest._domains_named(packet, findings) == ['pebbles', 'agent-harness']


def test_a_suggested_domain_must_exist():
    candidate = first_candidate()
    review = first_review()
    review['cards'][2]['suggested_domain'] = 'nowhere'
    with pytest.raises(ingest.IngestError, match='suggested_domain must be one of'):
        ingest.validate_review(candidate, review, {'card_exist'}, {'attention': {}, 'pebbles': {}})

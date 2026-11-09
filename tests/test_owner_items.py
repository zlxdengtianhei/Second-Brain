"""What waits for the owner, one list; topic sizes and split proposals; names that say the same words."""
from __future__ import annotations

import copy
import json
import sys
from dataclasses import replace

import pytest

from secondbrain import cli, config, curation, topics
from secondbrain import layout as layouts
from secondbrain.executors import FakeExecutor
from secondbrain.moc import MAX_TOPIC
from secondbrain.pipelines import ingest
from secondbrain.service import OWNER_REASONS, Service
from secondbrain.vault import read_snapshot
from tests.helpers import DOMAIN, EXISTING, MOC, PILLAR, TAXONOMY, TOPIC, build_vault
from tests.ingest_helpers import CARDS, NEW_TOPIC, SOURCE_NAME, build_ingest_vault, first_candidate, scripted


# --- one list of owner items ----------------------------------------------------------------------------------------
def a_job_that_leaves_one_of_each(tmp_path):
    """One chunk, no repair round: every kind of owner item comes out of it."""
    root = build_ingest_vault(tmp_path / 'vault')
    current = config.current()
    config.activate(replace(current, review=replace(current.review, max_repair_rounds=0)))
    candidate = copy.deepcopy(first_candidate())
    cards = {c['slug']: c for c in candidate['cards']}
    cards['lantern-queue']['status'] = 'needs-review'  # the producer finds the source unclear
    # a link to a card of the source that ends pending: plain text when written, and listed (dangling_link)
    cards['lantern-queue']['propositions'][0] += f" (see [[{cards['marker-dimming']['title']}]])"
    cards['queue-order']['relations'] = [{'type': 'similar_to', 'target': EXISTING, 'note': 'n'}]
    candidate['coverage'].append({'source_quote': 'When the queue is compacted', 'disposition': 'needs_taxonomy',
                                  'suggested_pillar': 'Maintenance', 'reason': 'no pillar for upkeep'})
    review = {'cards': [{'slug': 'lantern-queue', 'decision': 'accept', 'overlap': 'distinct', 'reason': 'ok',
                         'conflicts': [{'with': EXISTING, 'detail': 'the existing card says markers are kept'}]},
                        {'slug': 'lit-marker-cost', 'decision': 'accept', 'overlap': 'distinct', 'reason': 'ok'},
                        {'slug': 'marker-dimming', 'decision': 'revise', 'reason': 'dimmed, not deleted'},
                        {'slug': 'queue-order', 'decision': 'accept', 'overlap': 'extends', 'with': [EXISTING],
                         'reason': 'adds the order to the existing card'}],
              'new_topics': [{'domain': DOMAIN, 'pillar': PILLAR, 'name': NEW_TOPIC, 'decision': 'revise',
                              'reason': 'too narrow for a topic'}],
              'coverage': {'decision': 'accept', 'reason': 'covered'}}
    produce, respond, _ = scripted([candidate], [review])
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    done = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=respond))
    return root, done


def test_every_kind_of_item_that_waits_for_the_owner_is_on_one_list(tmp_path, capsys):
    root, done = a_job_that_leaves_one_of_each(tmp_path)
    assert done['status'] == 'partial'
    items = Service(root).owner_items()
    by = {}
    for item in items:
        by.setdefault(item['owner_reason'], []).append(item)
    assert set(by) == set(OWNER_REASONS)
    assert [i['title'] for i in by['source_unclear']] == [CARDS['lantern-queue']['title']]
    assert [i['title'] for i in by['possible_repeat']] == [CARDS['queue-order']['title']]
    assert [i['title'] for i in by['dangling_link']] == [CARDS['lantern-queue']['title']]
    assert done['links_unlinked'] == [{'card': 'lantern-queue', 'link': CARDS['marker-dimming']['title']}]
    assert by['conflict'][0]['with'] == EXISTING
    assert {i['slug'] for i in by['pending']} == {'marker-dimming', 'lit-marker-cost'}
    taxonomy = {i['kind']: i for i in by['needs_taxonomy']}
    assert taxonomy['passage']['suggested_pillar'] == 'Maintenance'
    assert taxonomy['refused_topic']['name'] == NEW_TOPIC and taxonomy['refused_topic']['cards'] == 1
    assert done['refused_topics'][0]['reason'] == 'too narrow for a topic'
    status = Service(root).status()
    assert status['owner_items'] == {r: len(by[r]) for r in OWNER_REASONS}
    assert 'sb status --owner-items' in status['owner_items_hint']
    assert cli.main(['--vault', str(root), 'status', '--owner-items']) == 0
    assert {i['owner_reason'] for i in json.loads(capsys.readouterr().out)} == set(OWNER_REASONS)


def test_a_card_marked_needs_review_before_reasons_were_recorded_counts_as_an_unclear_source(tmp_path):
    root = build_vault(tmp_path / 'vault')
    path = next((root / '10_Knowledge').rglob(f'{EXISTING}.md'))
    path.write_text(path.read_text(encoding='utf-8').replace('topic:', 'status: needs-review\ntopic:'), encoding='utf-8')
    [item] = Service(root).owner_items()
    assert item['owner_reason'] == 'source_unclear' and 'no reason recorded' in item['detail']


# --- topic sizes ----------------------------------------------------------------------------------------------------
def card_file(root, cid, title, topic=TOPIC, links=(), pillar=PILLAR):
    rel = ''.join(f'- type: similar_to\n  target: {t}\n' for t in links)
    (root / '10_Knowledge' / DOMAIN / f'{cid}.md').write_text(
        f'---\nid: {cid}\ntitle: {title}\ndomain: {DOMAIN}\npillar: {pillar}\ntopic: {topic}\n'
        + (f'relations:\n{rel}' if rel else '') + '---\n## Essence\nx\n', encoding='utf-8')
    lay = layouts.DEFAULT
    registry = json.loads((root / lay.registry).read_text(encoding='utf-8'))
    registry['notes'].append({'id': cid, 'path': lay.card_path(DOMAIN, f'{cid}.md'), 'title': title, 'domain': DOMAIN})
    (root / lay.registry).write_text(json.dumps(registry, ensure_ascii=False), encoding='utf-8')


def taxonomy_with(root, topics_):
    tax = {**TAXONOMY, 'pillars': [{**TAXONOMY['pillars'][0], 'topics': topics_}]}
    (root / '20_MOCs' / '_pillars' / f'{MOC}.yaml').write_text(json.dumps(tax, ensure_ascii=False), encoding='utf-8')


def test_status_lists_topics_past_one_map_page_empty_topics_and_cards_outside_the_taxonomy(tmp_path):
    root = build_vault(tmp_path / 'vault')
    taxonomy_with(root, [TOPIC, 'Unused'])
    for i in range(MAX_TOPIC):  # with the existing card: 26 in the topic
        card_file(root, f'demo_c{i:02d}', f'Card {i:02d}')
    card_file(root, 'demo_lost', 'Lost card', topic='Not curated')
    hints = topics.hints(read_snapshot(root))
    assert hints['oversize'] == [{'domain': DOMAIN, 'pillar': PILLAR, 'topic': TOPIC, 'cards': MAX_TOPIC + 1,
                                  'limit': MAX_TOPIC, 'command': f'sb curate --domain {DOMAIN}'}]
    assert {'domain': DOMAIN, 'pillar': PILLAR, 'topic': 'Unused'} in hints['empty']
    assert {'domain': DOMAIN, 'pillar': PILLAR, 'topic': TOPIC} not in hints['empty']
    assert hints['outside_taxonomy'] == 1
    status = Service(root).status()
    assert status['topics']['oversize'][0]['cards'] == MAX_TOPIC + 1 and 'sb curate' in status['topics_hint']


def test_a_topic_of_exactly_one_page_is_not_past_it(tmp_path):
    root = build_vault(tmp_path / 'vault')
    for i in range(MAX_TOPIC - 1):
        card_file(root, f'demo_c{i:02d}', f'Card {i:02d}')
    assert topics.hints(read_snapshot(root))['oversize'] == []


# --- split proposals (D-08) -----------------------------------------------------------------------------------------
def clique(prefix, n, extra=()):
    ids = [f'demo_{prefix}{i:02d}' for i in range(n)]
    return [(cid, [o for o in ids if o != cid] + list(extra if k == 0 else ())) for k, cid in enumerate(ids)]


def topic_of(root, groups):
    for members in groups:
        for cid, links in members:
            card_file(root, cid, cid.replace('demo_', 'Card '), links=links)


def test_a_large_topic_splits_by_its_relations_small_groups_join_their_nearest_or_are_scattered(tmp_path):
    root = build_vault(tmp_path / 'vault')
    a = clique('a', 12)
    b = clique('b', 11)
    c = clique('c', 3, extra=['demo_a00'])   # three cards, one link into group a
    d = clique('d', 2)                        # two cards linked only to each other
    topic_of(root, [a, b, c, d])              # with the existing card (no links): 29 cards
    proposal = topics.split_proposal(read_snapshot(root), DOMAIN, PILLAR, TOPIC)
    sizes = [g['size'] for g in proposal['groups']]
    assert proposal['cards'] == 29 and sizes == [15, 11]
    first = {x['id'] for x in proposal['groups'][0]['cards']}
    assert {'demo_a00', 'demo_c00', 'demo_c02'} <= first
    assert {x['id'] for x in proposal['scattered']} == {EXISTING, 'demo_d00', 'demo_d01'}
    assert not any(g['needs_second_round'] for g in proposal['groups'])
    assert topics.split_proposal(read_snapshot(root), DOMAIN, PILLAR, TOPIC) == proposal  # fixed seed


def test_a_group_still_past_one_page_says_it_needs_a_second_round(tmp_path):
    root = build_vault(tmp_path / 'vault')
    topic_of(root, [clique('a', 27), clique('b', 6)])
    proposal = topics.split_proposal(read_snapshot(root), DOMAIN, PILLAR, TOPIC)
    assert [(g['size'], g['needs_second_round']) for g in proposal['groups']] == [(27, True), (6, False)]


def test_curation_gets_the_proposals_of_its_large_topics_only(tmp_path):
    root = build_vault(tmp_path / 'vault')
    topic_of(root, [clique('a', 14), clique('b', 13)])
    job = curation.prepare(root, [DOMAIN], job_id='curate-test')
    from secondbrain import jobs
    packet = jobs.read_input(root, job['job_id'])
    [proposal] = packet['split_proposals']
    assert proposal['topic'] == TOPIC and [g['size'] for g in proposal['groups']] == [14, 13]
    small = build_vault(tmp_path / 'small')
    job = curation.prepare(small, [DOMAIN], job_id='curate-small')
    assert 'split_proposals' not in jobs.read_input(small, job['job_id'])


def test_without_networkx_the_proposal_says_how_to_get_it(tmp_path, monkeypatch):
    root = build_vault(tmp_path / 'vault')
    monkeypatch.setitem(sys.modules, 'networkx.algorithms.community', None)
    proposal = topics.split_proposal(read_snapshot(root), DOMAIN, PILLAR, TOPIC)
    assert 'networkx' in proposal['unavailable'] and '.[curate]' in proposal['unavailable']


# --- names that say the same words (same_text) -----------------------------------------------------------------------
def check(root, candidate, *, other=(), titles=frozenset()):
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    from secondbrain import jobs
    packet = jobs.read_input(root, job['job_id'])
    return ingest.validate_candidate(packet, candidate, slot='c01', other_cards=list(other), kept_slugs=set(),
                                     existing_ids=set(read_snapshot(root).cards), existing_titles=set(titles))


def test_a_title_with_the_same_words_as_another_clashes_whatever_its_case_and_punctuation(tmp_path):
    from secondbrain.textnorm import plain
    root = build_ingest_vault(tmp_path / 'vault')
    candidate = copy.deepcopy(first_candidate())
    candidate['cards'][0]['title'] = '“Lantern, Queue!”'
    with pytest.raises(ingest.IngestError, match='repeats another card of this source'):
        check(root, candidate, other=[{'slug': 'x', 'title': 'lantern queue', 'domain': DOMAIN}])
    with pytest.raises(ingest.IngestError, match="a card titled '“Lantern, Queue!”' already exists"):
        check(root, candidate, titles={plain('Lantern queue')})
    assert ingest._unique_title('Lantern, Queue', {plain('lantern queue')}) == 'Lantern, Queue (2)'
    assert ingest._unique_title('Lantern-Queue', {plain('lantern queue')}) == 'Lantern-Queue'  # a hyphen counts


def test_a_new_topic_with_the_same_words_as_a_curated_one_clashes(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    candidate = copy.deepcopy(first_candidate())
    candidate['new_topics'][0]['name'] = TOPIC.upper() + ' !'
    with pytest.raises(ingest.IngestError, match='duplicates or overlaps existing topic'):
        check(root, candidate)

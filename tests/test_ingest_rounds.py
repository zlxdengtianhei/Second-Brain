"""Repair rounds and review packets: split cards, replaced new topics, near-duplicate digests, status counts."""
from __future__ import annotations

import json
from dataclasses import replace

from secondbrain import cli, config, jobs
from secondbrain import layout as layouts
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest
from secondbrain.service import Service
from secondbrain.vault import read_snapshot
from tests.helpers import DOMAIN, EXISTING, MOC, PILLAR, TOPIC
from tests.ingest_helpers import (CARDS, FIXED_DIMMING, NEW_TOPIC, SOURCE_NAME, TOPIC_PROPOSAL, build_ingest_vault,
                                  first_candidate, first_review, scripted)

DIM_BEFORE_CUT = {**FIXED_DIMMING, 'slug': 'dimming-before-cut', 'title': 'Markers before a compaction cut',
                  'propositions': ['Markers before the cut are dimmed, not deleted.'],
                  'source_quotes': ['markers before the cut are dimmed rather than deleted']}
DIMMED_NOT_FOLLOWED = {**FIXED_DIMMING, 'slug': 'dimmed-not-followed', 'title': 'Dimmed markers are not followed',
                       'propositions': ['Dimmed markers stay readable but are never followed.'],
                       'relations': [{'type': 'depends_on', 'target': 'dimming-before-cut', 'note': 'what dimming is'}],
                       'source_quotes': ['dimmed markers stay readable but are never followed']}


def rows(*cards):
    return [{'source_quote': c['source_quotes'][0], 'disposition': 'card', 'cards': [c['slug']],
             'reason': 'PRODUCER-REASON-7 covered'} for c in cards]


def run(root, producer_outputs, reviewer_outputs):
    produce, respond, seen = scripted(producer_outputs, reviewer_outputs)
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, prepared['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                            reviewer=FakeExecutor('claude-test', responder=respond))
    return result, seen


def accept(*slugs, coverage_decision='accept', topics=()):
    return {'cards': [{'slug': s, 'decision': 'accept', 'reason': 'fine'} for s in slugs],
            'new_topics': [{**{k: t[k] for k in ('domain', 'pillar', 'name')}, 'decision': 'accept', 'reason': 'ok'}
                           for t in topics],
            'coverage': {'decision': coverage_decision, 'reason': 'checked against the chunk'}}


# --- a rejected card that the repair split or dropped -----------------------------------------
def test_a_rejected_card_the_repair_split_is_not_left_pending(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    split = {'cards': [DIM_BEFORE_CUT, DIMMED_NOT_FOLLOWED], 'new_topics': [],
             'coverage': rows(DIM_BEFORE_CUT, DIMMED_NOT_FOLLOWED)}
    result, _ = run(root, [first_candidate(), split],
                    [first_review(), accept('dimming-before-cut', 'dimmed-not-followed')])
    assert result['status'] == 'applied' and result['pending'] == []
    assert {p['slug'] for p in result['produced']} == {'lantern-queue', 'lit-marker-cost', 'dimming-before-cut',
                                                       'dimmed-not-followed'}


def test_after_a_split_only_the_gap_the_last_review_names_stays_pending(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    split = {'cards': [DIM_BEFORE_CUT], 'new_topics': [], 'coverage': rows(DIM_BEFORE_CUT)}
    result, _ = run(root, [first_candidate(), split],
                    [first_review(), accept('dimming-before-cut', coverage_decision='revise')])
    assert result['status'] == 'partial'
    assert [(p.get('slug'), p.get('scope')) for p in result['pending']] == [(None, 'coverage')]


def test_a_repair_round_sees_the_passages_earlier_rounds_settled_without_a_card(tmp_path):
    """Drill 2: the repair reviewer did not see round 0's duplicate row and called that passage missing."""
    root = build_ingest_vault(tmp_path / 'vault')
    first = first_candidate()
    skim = {'source_quote': 'A reader that holds a marker can jump straight to it', 'disposition': 'duplicate',
            'duplicate_of': [EXISTING], 'reason': 'the existing card says it'}
    first['coverage'].append(skim)
    _, seen = run(root, [first, {'cards': [FIXED_DIMMING], 'new_topics': [], 'coverage': rows(FIXED_DIMMING)}],
                  [first_review(), accept('marker-dimming')])
    expected = {'duplicates': [{k: skim[k] for k in ('source_quote', 'duplicate_of')},
                               {'source_quote': CARDS['queue-order']['source_quotes'][0],
                                'title': CARDS['queue-order']['title'], 'duplicate_of': [EXISTING],
                                'review': 'already covered by the existing card'}],
                'out_of_scope': [{'source_quote': 'Thanks for reading, see you next week!'}]}
    assert seen['producer'][1].packet['already_settled'] == expected
    assert seen['reviewer'][1].packet['already_settled'] == expected
    assert 'already_settled' in seen['reviewer'][1].prompt and 'already_settled' in seen['producer'][1].prompt
    assert 'already_settled' not in seen['reviewer'][0].packet  # round 0 has no earlier round


# --- a new topic the review did not accept, replaced in the repair round ----------------------
def topic_refused_review():
    review = first_review()
    review['cards'][2] = {'slug': 'marker-dimming', 'decision': 'accept', 'reason': 'fine'}
    review['new_topics'][0].update(decision='revise', reason='too broad; name what the cards share')
    return review


def refiled(topic, *slugs):
    return [{**(FIXED_DIMMING if s == 'marker-dimming' else CARDS[s]), 'topic': topic} for s in slugs]


def test_a_repair_round_renames_a_refused_topic_and_refiles_the_accepted_cards(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    config.activate(replace(config.current(), ingest=replace(config.current().ingest, new_topics_per_source=1)))
    renamed = {**TOPIC_PROPOSAL, 'name': 'Marker upkeep', 'boundary': 'What lit markers cost and how they retire.'}
    cards = refiled('Marker upkeep', 'lit-marker-cost', 'marker-dimming')
    repair = {'cards': cards, 'new_topics': [renamed], 'coverage': rows(*cards)}
    result, seen = run(root, [first_candidate(), repair],
                       [topic_refused_review(), accept('lit-marker-cost', 'marker-dimming', topics=[renamed])])
    # the repair producer was told which kept cards to file again, and why
    refile = seen['producer'][1].packet['refile_cards']
    assert {c['slug'] for c in refile} == {'lit-marker-cost', 'marker-dimming'}
    assert {c['topic_review'] for c in refile} == {'too broad; name what the cards share'}
    assert 'refile_cards' in seen['producer'][1].prompt
    # the reviewer judges the refiled cards as candidates, not as already accepted
    assert [c['slug'] for c in seen['reviewer'][1].packet['already_accepted_cards']] == ['lantern-queue']
    assert result['status'] == 'applied' and result['pending'] == []
    assert result['new_topics'] == [{'domain': DOMAIN, 'pillar': PILLAR, 'name': 'Marker upkeep'}]
    written = {c.title: c.fm['topic'] for c in read_snapshot(root).cards.values()}
    assert written['Lit marker memory cost'] == written['Marker dimming'] == 'Marker upkeep'
    assert read_snapshot(root).taxonomies[MOC]['pillars'][0]['topics'] == [TOPIC, 'Marker upkeep']


def test_a_repair_round_can_refile_the_cards_under_a_curated_topic(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    cards = refiled(TOPIC, 'lit-marker-cost', 'marker-dimming')
    repair = {'cards': cards, 'new_topics': [], 'coverage': rows(*cards)}
    result, _ = run(root, [first_candidate(), repair],
                    [topic_refused_review(), accept('lit-marker-cost', 'marker-dimming')])
    assert result['status'] == 'applied' and result['new_topics'] == []
    assert {c.fm['topic'] for c in read_snapshot(root).cards.values() if c.id != EXISTING
            and c.domain == DOMAIN} == {TOPIC}
    assert NEW_TOPIC not in read_snapshot(root).taxonomies[MOC]['pillars'][0]['topics']


def test_kept_cards_not_refiled_stay_pending_under_the_refused_topic(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    nothing = {'cards': [], 'new_topics': [], 'coverage': [
        {'source_quote': 'Markers cost one extra word', 'disposition': 'needs_taxonomy',
         'suggested_pillar': 'Marker economics', 'reason': 'no fitting pillar'}]}
    result, _ = run(root, [first_candidate(), nothing], [topic_refused_review(), accept()])
    assert result['status'] == 'partial'
    assert {p['slug'] for p in result['pending']} == {'lit-marker-cost', 'marker-dimming'}
    assert all('was not accepted' in p['reason'] for p in result['pending'])


# --- near-duplicate digests in the reviewer packet --------------------------------------------
def write_card(root, cid, title, essence, propositions):
    lay = layouts.DEFAULT
    rel = lay.card_path(DOMAIN, f'{title}.md')
    body = (f'## Essence\n{essence}\n\n## Propositions\n' + ''.join(f'- P{i}. {p}\n' for i, p in
                                                                   enumerate(propositions, 1))
            + '\n## Related\n- similar_to:: [[已有卡]]\n\n## Review cards\nWhat is it?::' + essence + '\n')
    (root / rel).write_text(f'---\nid: {cid}\ntitle: {title}\ndomain: {DOMAIN}\npillar: {PILLAR}\n'
                            f'topic: {TOPIC}\n---\n{body}', encoding='utf-8')
    registry = json.loads((root / lay.registry).read_text(encoding='utf-8'))
    registry['notes'].append({'id': cid, 'path': rel, 'title': title, 'domain': DOMAIN})
    (root / lay.registry).write_text(json.dumps(registry, ensure_ascii=False), encoding='utf-8')


def lantern_vault(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    write_card(root, 'card_lantern', 'Lantern queues in brief', 'A lantern queue keeps arrival order.',
               ['Items keep arrival order.', 'Readers skip ahead by lit markers.'])
    write_card(root, 'card_markers', 'Lit markers and their cost', 'Lit markers cost memory.',
               ['A writer lights a marker every eight items.', 'Each marker costs one word of memory.'])
    return root


def test_near_duplicates_show_essence_and_propositions_once_per_card(tmp_path):
    root = lantern_vault(tmp_path)
    snapshot = read_snapshot(root)
    near, table = ingest._near_duplicates(root, snapshot, list(CARDS.values()))
    assert set(near) == set(CARDS) and all(len(ids) <= ingest.NEAR_DUPLICATES_PER_CARD for ids in near.values())
    assert {i for ids in near.values() for i in ids} == set(table)  # each existing card once
    lantern = table['card_lantern']
    assert lantern == {'title': 'Lantern queues in brief', 'domain': DOMAIN,
                       'essence': 'A lantern queue keeps arrival order.',
                       'propositions': ['P1. Items keep arrival order.', 'P2. Readers skip ahead by lit markers.']}


def test_near_duplicate_propositions_stop_at_the_byte_budget_best_rank_first(tmp_path, monkeypatch):
    root = lantern_vault(tmp_path)
    snapshot = read_snapshot(root)
    near, _ = ingest._near_duplicates(root, snapshot, list(CARDS.values()))
    best = [ids[0] for ids in near.values() if ids]
    first = best[0]
    cost = len(json.dumps(ingest._card_digest(snapshot.cards[first])[1], ensure_ascii=False).encode('utf-8'))
    monkeypatch.setattr(ingest, 'NEAR_DUPLICATE_PROPOSITION_BYTES', cost)
    _, table = ingest._near_duplicates(root, snapshot, list(CARDS.values()))
    assert 'propositions' in table[first]
    assert [i for i, d in table.items() if 'propositions' in d] == [first]
    assert all(d['essence'] for d in table.values())


def test_the_review_packet_carries_digests_not_bodies(tmp_path):
    root = lantern_vault(tmp_path)
    _, seen = run(root, [first_candidate(), {'cards': [FIXED_DIMMING], 'new_topics': [],
                                             'coverage': rows(FIXED_DIMMING)}],
                  [first_review(), accept('marker-dimming')])
    packet = seen['reviewer'][0].packet
    assert set(packet['near_duplicates']) == set(CARDS)
    assert all(isinstance(i, str) for ids in packet['near_duplicates'].values() for i in ids)
    assert 'body' not in json.dumps(packet['near_duplicate_cards']) and 'Review cards' not in json.dumps(packet)
    assert 'near_duplicate_cards' in seen['reviewer'][0].prompt


# --- what sb status and sb doctor report -------------------------------------------------------
def test_status_counts_map_issues_and_names_the_command_that_lists_them(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    write_card(root, 'card_odd', 'Odd card', 'Filed under a topic that does not exist.', ['It is odd.'])
    path = root / layouts.DEFAULT.card_path(DOMAIN, 'Odd card.md')
    path.write_text(path.read_text(encoding='utf-8').replace(f'topic: {TOPIC}', 'topic: Nowhere'), encoding='utf-8')
    status = Service(root).status()
    assert status['issues'] == [] and status['map_issues'] == {'invalid_classification': 1}
    assert status['map_issues_hint'] == 'sb refresh lists each one'


def test_a_hand_edited_catalog_is_checked_by_status_and_doctor(tmp_path, capsys):
    root = build_ingest_vault(tmp_path / 'vault')
    assert cli.main(['--vault', str(root), 'doctor']) == 0
    assert json.loads(capsys.readouterr().out)['catalog'] == 'valid'
    catalog = root / layouts.DEFAULT.catalog
    data = json.loads(catalog.read_text(encoding='utf-8'))
    data['inverse_relations']['part_of'] = 'contains'  # not closed: contains has no way back
    catalog.write_text(json.dumps(data), encoding='utf-8')
    [issue] = [i for i in Service(root).status()['issues'] if i['code'] == 'catalog_invalid']
    assert issue['path'] == layouts.DEFAULT.catalog and issue['detail']
    assert cli.main(['--vault', str(root), 'doctor']) == 0
    assert json.loads(capsys.readouterr().out)['catalog'].startswith('invalid: ')


def test_status_counts_only_open_items_across_a_retry_chain(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    review = first_review()
    review['cards'][1].update(decision='revise', reason='numbers are off')
    again = {'cards': [{'slug': 'marker-dimming', 'decision': 'revise', 'reason': 'still wrong'},
                       {'slug': 'lit-marker-cost', 'decision': 'revise', 'reason': 'numbers are off'}],
             'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'covered'}}
    two = [FIXED_DIMMING, CARDS['lit-marker-cost']]
    result, _ = run(root, [first_candidate(), {'cards': two, 'new_topics': [], 'coverage': rows(*two)}],
                    [review, again])
    parent = result['job_id']
    assert len(result['pending']) == 2
    child = ingest.retry(root, parent)
    fixed = {'cards': [{'slug': 'marker-dimming', 'decision': 'accept', 'reason': 'matches now'},
                       {'slug': 'lit-marker-cost', 'decision': 'revise', 'reason': 'numbers still off'}],
             'new_topics': [{**{k: TOPIC_PROPOSAL[k] for k in ('domain', 'pillar', 'name')}, 'decision': 'accept',
                             'reason': 'needed'}], 'coverage': {'decision': 'accept', 'reason': 'answered'}}
    answer = {'cards': two, 'new_topics': [TOPIC_PROPOSAL], 'coverage': rows(*two)}
    produce, respond, _ = scripted([answer, answer], [fixed, fixed])
    done = ingest.advance(root, child['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=respond))
    assert done['status'] == 'partial' and len(done['pending']) == 1
    status = Service(root).status()
    assert status['pending_items'] == 1
    listed = {j['job_id']: j for j in status['jobs']}
    assert listed[parent]['pending'] == [] and listed[parent]['retried_by'] == child['job_id']
    assert [p['slug'] for p in listed[child['job_id']]['pending']] == ['lit-marker-cost']
    assert len(jobs.load(root, parent)['pending']) == 2  # the record itself is kept


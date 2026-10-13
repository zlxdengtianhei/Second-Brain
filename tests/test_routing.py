"""S3 routing: per-passage votes pick the domain the source is about, not the biggest domain."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from secondbrain import config, index, jobs
from secondbrain import layout as layouts
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


def test_the_domain_size_correction_keeps_a_large_domain_from_winning_on_generic_hits(tmp_path, monkeypatch):
    """Votes are divided by the square root of each domain's share of cards; without it the large domain wins."""
    from secondbrain import index as index_module
    root = build(tmp_path / 'vault')
    snap = read_snapshot(root)
    by_domain: dict = {}
    for card in sorted(snap.cards.values(), key=lambda c: c.id):
        by_domain.setdefault(card.domain, []).append({'id': card.id, 'domain': card.domain, 'title': card.title})
    assert len(by_domain['agent-harness']) > 6 * len(by_domain['algorithms'])
    # one passage of generic words hits only the large domain; one about sorting hits all sorting cards first
    generic = by_domain['agent-harness'][:10]
    sorting = by_domain['algorithms'][:4] + by_domain['agent-harness'][10:16]
    monkeypatch.setattr(index_module, 'search_many', lambda root, snapshot, parts, k: [generic, sorting])
    text = ' '.join(f'g{i}' for i in range(40)) + '\n\n' + ' '.join(f's{i}' for i in range(40))
    raw: dict = {}
    for hits in (generic, sorting):
        total = sum(1 / (r + 1) for r in range(len(hits)))
        for r, hit in enumerate(hits):
            raw[hit['domain']] = raw.get(hit['domain'], 0) + (1 / (r + 1)) / total
    assert max(raw, key=raw.get) == 'agent-harness'  # the same hits, uncorrected
    routed = route(root, snap, text, domains=1)
    assert routed['passages'] == 2 and routed['domains'] == ['algorithms']
    assert routed['votes']['algorithms'] > routed['votes']['agent-harness']


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
    # the field alone, with a reason that names no domain (the reason path is tested below)
    review['cards'][2].update(suggested_domain='transformer', reason='this belongs with the decoder cards')
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


def empty_vault(root, domains):
    """A vault with a curated taxonomy per domain and no cards at all."""
    (root / '10_Knowledge').mkdir(parents=True)
    (root / '20_MOCs/_pillars').mkdir(parents=True)
    (root / '_data/secondbrain').mkdir(parents=True)
    (root / '00_Inbox').mkdir()
    catalog = {'schema': 'secondbrain-catalog/1', 'tracks': [{'id': 't', 'title': 'T', 'domains': list(domains)}],
               'domains': {d: {'moc': f'{d.title()} Map', 'question': f'How does {d} work?'} for d in domains},
               'relation_types': ['depends_on'], 'inverse_relations': {}, 'domain_relations': {}}
    (root / layouts.DEFAULT.catalog).write_text(json.dumps(catalog), encoding='utf-8')
    (root / '_data/notes_registry.json').write_text(json.dumps({'notes': []}), encoding='utf-8')
    for d in domains:
        (root / '20_MOCs/_pillars' / f'{d.title()} Map.yaml').write_text(json.dumps(
            {'title': f'{d.title()} Map', 'narrative': 'n',
             'pillars': [{'name': f'{d.title()} basics', 'order': 1, 'why': 'w', 'topics': ['Overview']}]}),
            encoding='utf-8')
    return root.resolve()


def test_a_vault_without_cards_sends_every_domain_taxonomy(tmp_path):
    root = empty_vault(tmp_path / 'vault', ['sorting', 'networks', 'cooking'])
    (root / '00_Inbox' / 'notes.md').write_text(SOURCE, encoding='utf-8')
    prepared = ingest.prepare(root, root / '00_Inbox' / 'notes.md')
    [chunk] = jobs.read_input(root, prepared['job_id'])['chunks']
    assert chunk['candidate_domains'] == ['sorting', 'networks', 'cooking']
    assert chunk['routing']['filled'] == ['sorting', 'networks', 'cooking'] and chunk['neighbors'] == []
    packet = ingest.producer_packet(root, jobs.load(root, prepared['job_id']),
                                    jobs.read_input(root, prepared['job_id']), 'c01')
    assert set(packet['candidate_taxonomies']) == {'sorting', 'networks', 'cooking'}
    assert packet['candidate_taxonomies']['cooking']['pillars'][0]['name'] == 'Cooking basics'


def test_empty_domains_fill_the_candidates_before_other_unvoted_ones(tmp_path):
    root = build(tmp_path / 'vault')
    catalog_path = root / layouts.DEFAULT.catalog
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    for extra in ('zz-empty', 'aa-empty'):
        catalog['domains'][extra] = {'moc': f'{extra} Map', 'question': '?'}
        catalog['tracks'][0]['domains'].append(extra)
        (root / '20_MOCs/_pillars' / f'{extra} Map.yaml').write_text(json.dumps(
            {'title': f'{extra} Map', 'narrative': 'n',
             'pillars': [{'name': 'Basics', 'order': 1, 'why': 'w', 'topics': ['Overview']}]}), encoding='utf-8')
    catalog_path.write_text(json.dumps(catalog), encoding='utf-8')
    snap = read_snapshot(root)
    only_algorithms = 'Binary search keeps a sorted range lo to hi and halves it each step.'
    routed = route(root, snap, only_algorithms, domains=5)
    assert routed['domains'][0] == 'algorithms' and len(routed['domains']) == 5
    voted = [d for d in routed['domains'] if d not in routed['filled']]
    assert routed['domains'] == voted + routed['filled']
    # unvoted domains: the empty ones first, in catalog order (zz-empty was added before aa-empty)
    unvoted_with_cards = [d for d in ['algorithms', 'agent-harness', 'networking'] if d not in voted]
    assert routed['filled'] == (['zz-empty', 'aa-empty'] + unvoted_with_cards)[:5 - len(voted)]
    assert routed['filled'][:2] == ['zz-empty', 'aa-empty']


def test_the_pillar_error_names_what_exists_and_what_to_do(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    packet = jobs.read_input(root, prepared['job_id'])
    candidate = json.loads(json.dumps(first_candidate()))
    candidate['new_topics'][0]['pillar'] = 'Brand new pillar'
    with pytest.raises(ingest.IngestError) as err:
        ingest.validate_candidate(packet, candidate, slot='c01', other_cards=[], kept_slugs=set(),
                                  existing_ids=set(read_snapshot(root).cards), existing_titles=set())
    message = str(err.value)
    assert 'pillars: Token routing' in message and 'needs_taxonomy' in message and 'sb taxonomy' not in message


def test_the_first_ingest_into_an_empty_vault_writes_cards_and_maps(tmp_path):
    root = empty_vault(tmp_path / 'vault', ['sorting', 'networks'])
    (root / '00_Inbox' / 'notes.md').write_text(SOURCE, encoding='utf-8')
    quote = 'Binary search needs a sorted range.'
    card = {'slug': 'binary-search-range', 'title': 'Binary search needs a sorted range', 'kind': 'concept',
            'domain': 'sorting', 'pillar': 'Sorting basics', 'topic': 'Overview',
            'essence': 'Binary search only works on a sorted range.', 'propositions': ['The range must be sorted.'],
            'flashcards': [{'q': 'What does binary search need?', 'a': 'A sorted range.'},
                           {'q': 'What happens each step?', 'a': 'The range is halved.'}],
            'relations': [], 'source_quotes': [quote], 'reason': 'one fact'}
    candidate = {'cards': [card], 'new_topics': [],
                 'coverage': [{'source_quote': quote, 'disposition': 'card', 'cards': [card['slug']], 'reason': 'r'},
                              {'source_quote': 'Aside: the online judge harness', 'disposition': 'out_of_scope',
                               'reason': 'aside'}]}
    review = {'cards': [{'slug': card['slug'], 'decision': 'accept', 'reason': 'faithful'}], 'new_topics': [],
              'coverage': {'decision': 'accept', 'reason': 'the rest is practice text'}}
    produce, respond, _ = scripted([candidate], [review])
    prepared = ingest.prepare(root, root / '00_Inbox' / 'notes.md')
    result = ingest.advance(root, prepared['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                            reviewer=FakeExecutor('claude-test', responder=respond))
    assert result['status'] == 'applied' and result['new_card_count'] == 1
    assert (root / '10_Knowledge/sorting/Binary search needs a sorted range.md').is_file()
    assert (root / '20_MOCs/Sorting Map.md').is_file()


def test_an_empty_vault_offers_the_domains_whose_words_the_source_uses(tmp_path):
    names = ['calculus', 'linear-algebra', 'discrete-math', 'cooking', 'sorting', 'networks', 'gardening']
    root = empty_vault(tmp_path / 'vault', names)
    catalog_path = root / layouts.DEFAULT.catalog
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    catalog['domains']['networks']['question'] = 'How do packets find their way through routers?'
    catalog_path.write_text(json.dumps(catalog), encoding='utf-8')
    snap = read_snapshot(root)
    text = 'Sorting practice: merge sort keeps equal keys in order; quick sort partitions around a pivot.'
    routed = route(root, snap, text, domains=3)
    assert routed['domains'] == ['sorting', 'calculus', 'linear-algebra'] and routed['filled'] == routed['domains']
    text = 'Routers forward packets hop by hop.\n\nSorting the routing table by prefix length helps.'
    assert route(root, snap, text, domains=3)['domains'] == ['networks', 'sorting', 'calculus']
    # no shared words: empty domains in catalog order, as before
    assert route(root, snap, 'Lorem ipsum dolor.', domains=3)['domains'] == names[:3]

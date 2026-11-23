"""One concept in two domains: two narrow cards and a bridge, never one merged card.

SKOS reads a bridge in two strengths (W3C, section 10): `same_concept_as` says one concept and may be chained;
`similar_to` says close and never is. These tests judge the program's side only: no model decides the strength,
and the same scripted answers that merged two domains before now keep them apart.
"""
from __future__ import annotations

import json

import pytest

from secondbrain import bridges, cli, decisions, overlap, service
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest, source_check
from secondbrain.vault import read_snapshot
from tests.helpers import DOMAIN, EXISTING, PILLAR, TOPIC
from tests.ingest_helpers import (CARDS, OTHER_DOMAIN, OTHER_PILLAR, OTHER_TOPIC, SOURCE_NAME, build_ingest_vault,
                                  is_source_check_task, keep_all, with_diagrams)
from tests.test_ingest import settings

pytestmark = pytest.mark.usefixtures('old_review_rules')

ORDER_QUOTE = 'a lantern queue keeps every item in arrival order'
FILLER = ' '.join(f'Filler sentence {i} says nothing about queues.' for i in range(14))
SOURCE = ('# Lantern queues\n\n' + CARDS['lantern-queue']['source_quotes'][0] + '\n\n## Notes\n\n' + FILLER + '\n\n'
          + FILLER + '\n\n## Lit markers\n\nA writer lights a marker every eight items. Markers cost one extra '
          'word of memory each, so a queue of 800 items carries 100 markers. Note again that '
          + ORDER_QUOTE + '.\n')
#: The same point said in the second domain, as its own narrow card.
ARRIVAL = {**CARDS['queue-order'], 'slug': 'arrival-order', 'title': 'Lantern queues keep arrival order',
           'domain': OTHER_DOMAIN, 'pillar': OTHER_PILLAR, 'topic': OTHER_TOPIC,
           'source_quotes': [ORDER_QUOTE],
           'propositions': CARDS['queue-order']['propositions'] + ['The order holds while markers are lit.'],
           'relations': [{'type': 'part_of', 'target': 'lantern-queue', 'note': 'a property of the queue'}]}


def add_strong_bridge_type(root):
    """Declare the strong bridge type in the vault's catalog, as a vault that wants one does."""
    path = root / '_data' / 'secondbrain' / 'catalog.json'
    catalog = json.loads(path.read_text(encoding='utf-8'))
    catalog['relation_types'].append('same_concept_as')
    catalog['inverse_relations']['same_concept_as'] = 'same_concept_as'
    catalog.setdefault('relation_gloss', {})['same_concept_as'] = 'the same concept, named in another domain'
    path.write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
    return root


def produce(task):
    """Chunk 1 writes two cards of the first domain; chunk 2 a card of the second domain on the same point."""
    text = task.packet['chunk']['text']
    cards = []
    if CARDS['lantern-queue']['source_quotes'][0] in text:
        cards += [{**CARDS['lantern-queue'], 'relations': []}, {**CARDS['queue-order'], 'relations': []}]
    if ORDER_QUOTE in text:
        cards += [{**CARDS['lit-marker-cost'], 'topic': TOPIC, 'relations': []}, dict(ARRIVAL)]
    rows = [{'source_quote': c['source_quotes'][0], 'disposition': 'card', 'cards': [c['slug']], 'reason': 'r'}
            for c in cards]
    rows.append({'source_quote': text.strip().splitlines()[-1].strip(), 'disposition': 'out_of_scope',
                 'reason': 'rest'})
    return {'cards': cards, 'new_topics': [], 'coverage': rows}


def review(task):
    cand = task.packet['candidate']
    return {'cards': [{'slug': c['slug'], 'decision': 'accept', 'overlap': 'distinct', 'reason': 'ok'}
                      for c in cand['cards']],
            'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}


#: The merge check's answer before the change: the second domain's card goes into the first domain's card.
MERGES_CROSS_DOMAIN = {'arrival-order': {'decision': 'same', 'with': 'queue-order',
                                        'reason': 'both say items keep arrival order'}}


def run(tmp_path, check, *, strong=True):
    root = build_ingest_vault(tmp_path / 'vault')
    if strong:
        add_strong_bridge_type(root)
    settings(chunk_chars=1000)
    (root / '00_Inbox' / SOURCE_NAME).write_text(SOURCE, encoding='utf-8')
    seen = []

    def reviewer(task):
        if is_source_check_task(task):
            seen.append(task)
            return check(task.packet)
        return review(task)
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=with_diagrams(produce)),
                            reviewer=FakeExecutor('claude-test', responder=reviewer))
    return root, job['job_id'], result, seen


def test_the_merge_check_keeps_the_same_concept_apart_in_two_domains_and_joins_it_with_a_bridge(tmp_path):
    root, job_id, result, seen = run(tmp_path, lambda packet: keep_all(packet, **MERGES_CROSS_DOMAIN))
    assert result['status'] == 'applied'
    ids = {p['slug']: p['id'] for p in result['produced']}
    # the second domain keeps its own narrow card; it is not carried into the first domain's card
    assert 'arrival-order' in ids and 'queue-order' in ids
    assert result['source_check']['merged'] == []
    [bridge] = result['source_check']['bridges']
    assert bridge['slug'] == 'arrival-order' and bridge['into'] == ids['queue-order']
    assert bridge['domains'] == [OTHER_DOMAIN, DOMAIN] and bridge['type'] == 'same_concept_as'
    cards = read_snapshot(root).cards
    assert cards[ids['arrival-order']].domain == OTHER_DOMAIN
    assert {'type': 'same_concept_as', 'target': ids['queue-order'],
            'note': 'the same concept in another domain'} in cards[ids['arrival-order']].fm['relations']
    # the first domain's card grew nothing: the other end names it back (an inverse relation), and no text came in
    assert {'type': 'same_concept_as', 'target': ids['arrival-order']} in cards[ids['queue-order']].fm['relations']
    assert 'The order holds while markers are lit.' not in cards[ids['queue-order']].body


def test_a_vault_without_a_strong_bridge_type_gets_the_weak_one_and_says_so(tmp_path):
    root, job_id, result, seen = run(tmp_path, lambda packet: keep_all(packet, **MERGES_CROSS_DOMAIN), strong=False)
    assert result['status'] == 'applied'
    [bridge] = result['source_check']['bridges']
    assert bridge['type'] == 'similar_to'
    ids = {p['slug']: p['id'] for p in result['produced']}
    cards = read_snapshot(root).cards
    assert {'type': 'similar_to', 'target': bridge['into'],
            'note': 'the same concept in another domain'} in cards[ids['arrival-order']].fm['relations']


def test_a_card_of_a_second_domain_that_extends_an_existing_card_is_written_next_to_it(tmp_path):
    """The chunk reviewer's level puts it in the patch set; the program keeps it because the domains differ."""
    root = add_strong_bridge_type(build_ingest_vault(tmp_path / 'vault'))
    settings(chunk_chars=1000)
    (root / '00_Inbox' / SOURCE_NAME).write_text(SOURCE, encoding='utf-8')
    pebble = {**CARDS['queue-order'], 'slug': 'pebble-order', 'title': 'Pebble order in a pebble queue',
              'domain': OTHER_DOMAIN, 'pillar': OTHER_PILLAR, 'topic': OTHER_TOPIC,
              'relations': [{'type': 'same_concept_as', 'target': EXISTING,
                             'note': 'the same concept in another domain'}],
              'source_quotes': [ORDER_QUOTE], 'propositions': ['Pebbles keep arrival order.']}

    def producer(task):
        text = task.packet['chunk']['text']
        if ORDER_QUOTE in text:
            return {'cards': [pebble], 'new_topics': [], 'coverage': [
                {'source_quote': ORDER_QUOTE, 'disposition': 'card', 'cards': [pebble['slug']], 'reason': 'r'},
                {'source_quote': text.strip().splitlines()[-1].strip(), 'disposition': 'out_of_scope',
                 'reason': 'rest'}]}
        return {'cards': [], 'new_topics': [], 'coverage': [
            {'source_quote': text.strip().splitlines()[-1].strip(), 'disposition': 'out_of_scope', 'reason': 'rest'}]}

    def reviewer(task):
        if is_source_check_task(task):  # one chunk only: it never runs, but keeps the answer shape
            return keep_all(task.packet)
        return {'cards': [{'slug': 'pebble-order', 'decision': 'accept', 'overlap': 'extends',
                           'with': [EXISTING], 'reason': 'it answers the existing card\'s question'}],
                'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=with_diagrams(producer)),
                            reviewer=FakeExecutor('claude-test', responder=reviewer))
    assert result['status'] == 'applied'
    # written next to the existing card, not merged into it, with the bridge to it
    [produced] = result['produced']
    assert produced['slug'] == 'pebble-order' and produced['domain'] == OTHER_DOMAIN
    [bridge] = result['bridges']
    assert bridge['into'] == EXISTING and bridge['domains'] == [OTHER_DOMAIN, DOMAIN]
    cards = read_snapshot(root).cards
    assert {'type': 'same_concept_as', 'target': EXISTING,
            'note': 'the same concept in another domain'} in cards[produced['id']].fm['relations']
    assert EXISTING in cards and cards[EXISTING].domain == DOMAIN


def test_a_chain_of_close_bridges_is_never_one_concept_but_a_chain_of_strong_ones_is():
    edges = [('a', 'same_concept_as', 'b'), ('b', 'same_concept_as', 'c')]
    assert bridges.same_concept(edges, 'a', 'c') is True
    close = [('a', 'same_concept_as', 'b'), ('b', 'similar_to', 'c')]
    assert bridges.same_concept(close, 'a', 'c') is False
    assert bridges.same_concept([('a', 'similar_to', 'b'), ('b', 'similar_to', 'c')], 'a', 'c') is False
    assert bridges.same_concept([('a', 'similar_to', 'b'), ('b', 'same_concept_as', 'c')], 'a', 'c') is False


def test_explain_says_in_which_domain_the_bridge_stands(tmp_path):
    root, job_id, result, seen = run(tmp_path, lambda packet: keep_all(packet, **MERGES_CROSS_DOMAIN))
    assert result['status'] == 'applied'
    ids = {p['slug']: p['id'] for p in result['produced']}
    from secondbrain import explain
    snapshot = read_snapshot(root)
    nodes = explain.build(snapshot, {'kind': 'card', 'ids': [ids['arrival-order']]})
    bridged = [n for n in nodes if n['kind'] == 'card' and n.get('bridges')]
    assert bridged and bridged[0]['card_id'] == ids['arrival-order']
    [bridge] = bridged[0]['bridges']
    assert bridge['target'] == ids['queue-order'] and bridge['strength'] == 'same'
    assert bridge['domain_title'] == 'Example Attention Map'
    page = explain.assemble({'language': 'en', 'anchor': {'kind': 'card', 'ids': [ids['arrival-order']]},
                             'nodes': nodes},
                            {'nodes': [{'id': n['id'], 'explain': 'It says what this card holds.',
                                        **({'recap': 'Back up: the two fit.'} if n['children'] else {})}
                                      for n in nodes]},
                            {}, snapshot)
    words = explain.to_markdown(page)
    assert 'bridge: the same concept also in "Example Attention Map"' in words
    assert 'bridge: the same concept also in' in explain.to_html(page)


def test_status_reads_the_cross_domain_line(tmp_path):
    root, job_id, result, seen = run(tmp_path, lambda packet: keep_all(packet, **MERGES_CROSS_DOMAIN))
    assert result['status'] == 'applied'
    status = service.Service(root).status()
    assert status['cross_domain']['bridges'] == 1
    assert status['cross_domain']['strong'] == 1 and status['cross_domain']['weak'] == 0
    assert status['cross_domain']['by_domain_pair'] == {'attention|pebbles': 1}
    assert status['cross_domain']['candidates'] == 0


# --- the rule stays in one place, and each step that judges a repeat asks it -------------------------------

def second_domain_card(relations=None):
    """A card of the second domain on the same point, with the relation its producer guessed (``similar_to``)."""
    return {**CARDS['queue-order'], 'slug': 'tide-order', 'title': 'Tide order in lantern queues',
            'domain': OTHER_DOMAIN, 'pillar': OTHER_PILLAR, 'topic': OTHER_TOPIC,
            'relations': [{'type': 'similar_to', 'target': EXISTING, 'note': 'the same point'}]
            if relations is None else list(relations),
            'propositions': ['A lantern queue keeps every item in arrival order.'],
            'flashcards': [{'q': 'Which order is kept?', 'a': 'Arrival order.'},
                           {'q': 'Does a lantern queue reorder items?', 'a': 'No.'}],
            'source_quotes': [ORDER_QUOTE]}


def one_chunk(tmp_path, level, *, relations=None, strong=True):
    """One chunk writes the second domain's card; the chunk reviewer judges it ``level`` against the existing card."""
    root = build_ingest_vault(tmp_path / 'vault')
    if strong:
        add_strong_bridge_type(root)
    settings(chunk_chars=1000)
    (root / '00_Inbox' / SOURCE_NAME).write_text(SOURCE, encoding='utf-8')
    card = second_domain_card(relations)

    def producer(task):
        text = task.packet['chunk']['text']
        cards = [card] if ORDER_QUOTE in text else []
        rows = [{'source_quote': c['source_quotes'][0], 'disposition': 'card', 'cards': [c['slug']], 'reason': 'r'}
                for c in cards]
        rows.append({'source_quote': text.strip().splitlines()[-1].strip(), 'disposition': 'out_of_scope',
                     'reason': 'rest'})
        return {'cards': cards, 'new_topics': [], 'coverage': rows}

    def reviewer(task):
        if is_source_check_task(task):  # one chunk only: it never runs, but keeps the answer shape
            return keep_all(task.packet)
        return {'cards': [{'slug': 'tide-order', 'decision': 'accept', 'overlap': level, 'with': [EXISTING],
                           'reason': 'the same point said in the second domain'}],
                'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=with_diagrams(producer)),
                            reviewer=FakeExecutor('claude-test', responder=reviewer))
    return root, result


def test_the_chunk_reviewer_judging_same_across_two_domains_writes_the_second_card_and_a_bridge(tmp_path):
    """The bad sample one level up: the chunk reviewer says ``same``, and the card it repeats is of another domain.

    Before the one rule, :func:`secondbrain.overlap.route` turned every ``same`` into a drop whatever the domains,
    so the second domain lost its card and got no bridge.
    """
    root, result = one_chunk(tmp_path, 'same')
    assert result['status'] == 'applied'
    # the card is written and not listed as a duplicate of the other domain's card
    assert [p['slug'] for p in result['produced']] == ['tide-order'] and result['duplicates'] == []
    [produced] = result['produced']
    assert produced['domain'] == OTHER_DOMAIN
    [bridge] = result['bridges']
    assert bridge['into'] == EXISTING and bridge['domains'] == [OTHER_DOMAIN, DOMAIN]
    cards = read_snapshot(root).cards
    held = [r for r in cards[produced['id']].fm['relations'] if r.get('target') == EXISTING]
    assert [r['type'] for r in held] == [bridge['type']] == ['same_concept_as']
    assert EXISTING in cards and cards[EXISTING].domain == DOMAIN  # the other domain's card stays as it was
    cross = service.Service(root).status()['cross_domain']
    assert cross['bridges'] == 1 and cross['strong'] == 1 and cross['weak'] == 0


def test_the_bridge_the_result_reports_is_the_one_the_card_holds(tmp_path):
    """The card links its existing card by its own guess: the program changes that one relation's type, so the
    report and the card agree (one relation per target survives the fold)."""
    root, result = one_chunk(tmp_path, 'extends')
    assert result['status'] == 'applied'
    [bridge] = result['bridges']
    cards = read_snapshot(root).cards
    [produced] = result['produced']
    held = [r for r in cards[produced['id']].fm['relations'] if r.get('target') == EXISTING]
    assert [r['type'] for r in held] == [bridge['type']] == ['same_concept_as']
    assert service.Service(root).status()['cross_domain']['strong'] == 1


def test_route_keeps_a_same_in_one_domain_a_drop_and_a_same_across_two_domains_a_patch():
    """The control for the level the chunk reviewer gives: only two domains change ``same`` from a drop."""
    entries: list = []
    decisions.record(entries, 'overlap.existing', 'card-a', {'overlap': 'same', 'with': ['card-b'], 'reason': 'r'},
                     role='reviewer', slot='c01')
    assert overlap.route(entries, 'card-a')['outcome'] == 'drop'  # no domains told: as before bridges
    assert overlap.route(entries, 'card-a', {'card-a': DOMAIN, 'card-b': DOMAIN}.get)['outcome'] == 'drop'
    apart = overlap.route(entries, 'card-a', {'card-a': DOMAIN, 'card-b': OTHER_DOMAIN}.get)
    assert apart['outcome'] == 'patch' and apart['with'] == ['card-b']
    assert overlap.outcome(entries, 'card-a', checked=False)['outcome'] == 'drop'
    assert overlap.outcome(entries, 'card-a', checked=False,
                           domain_of={'card-a': DOMAIN, 'card-b': OTHER_DOMAIN}.get)['outcome'] == 'patch'


def test_settle_gives_one_answer_for_every_level_and_both_domains():
    strong = lambda domain: ('similar_to', 'same_concept_as')  # noqa: E731
    assert bridges.settle('existing', 'distinct', 'other', DOMAIN, OTHER_DOMAIN, strong)['how'] == 'distinct'
    assert bridges.settle('existing', 'same', 'other', DOMAIN, DOMAIN, strong) == \
        {'how': 'drop', 'type': None, 'reason': None}
    assert bridges.settle('existing', 'extends', 'other', DOMAIN, DOMAIN, strong)['how'] == 'merge'
    assert bridges.settle('source', 'same', 'other', DOMAIN, DOMAIN, strong)['how'] == 'merge'
    apart = bridges.settle('existing', 'same', 'other', DOMAIN, OTHER_DOMAIN, strong)
    assert apart['how'] == 'apart' and apart['type'] == 'same_concept_as'
    assert 'one concept in two domains' in apart['reason'] and '(same_concept_as)' in apart['reason']
    assert bridges.settle('source', 'extends', 'other', DOMAIN, OTHER_DOMAIN, strong)['how'] == 'apart'
    weak = bridges.settle('existing', 'same', 'other', DOMAIN, OTHER_DOMAIN, lambda domain: ('similar_to',))
    assert weak['how'] == 'apart' and weak['type'] == 'similar_to'
    none = bridges.settle('existing', 'same', 'other', DOMAIN, OTHER_DOMAIN, lambda domain: ())
    assert none['how'] == 'apart' and none['type'] is None and 'no bridge type' in none['reason']
    # no domain known (a card the vault does not hold): the level's own outcome holds
    assert bridges.settle('existing', 'same', 'other', None, OTHER_DOMAIN, strong)['how'] == 'drop'
    assert bridges.settle('existing', 'same', None, DOMAIN, OTHER_DOMAIN, strong)['how'] == 'drop'


def test_a_bridge_type_whose_back_link_the_other_domain_forbids_is_not_chosen():
    """``choose`` asks what :func:`secondbrain.pipelines.ingest._closed_relation` asks: both ends must be open."""
    pair = {'same_concept_as': 'similar_to', 'similar_to': 'same_concept_as'}
    assert bridges.choose(('same_concept_as',), ('same_concept_as',)) == 'same_concept_as'
    assert bridges.choose(('same_concept_as',), ('same_concept_as',), pair) is None
    assert bridges.choose(('similar_to', 'same_concept_as'), ('similar_to', 'same_concept_as'), pair) == \
        'same_concept_as'  # strong first, and its back link is open to both
    # a weak type whose back link is the strong one, open to neither domain: no bridge this catalog allows
    assert bridges.choose(('similar_to',), ('similar_to',), pair) is None

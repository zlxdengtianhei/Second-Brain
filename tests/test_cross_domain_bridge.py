"""One concept in two domains: one description, one canonical card, and a pointer in the other domain.

SKOS reads a mapping in two strengths (W3C, section 10): `same_concept_as` says one concept (exactMatch) and may
be chained; `similar_to` says close (closeMatch) and never is. The program's side only: no model decides, and
the level a judge gave decides what is written (:func:`secondbrain.bridges.settle`):

- ``same`` across two domains: no second card. The second domain's pillar page carries a pointer to the canonical
  card (the one that had the concept first); the description stays in that one card.
- ``extends`` across two domains: a narrow card of its own in each, joined by a bridge.
- ``same`` inside one domain: the card is not written.
"""
from __future__ import annotations

import json

import pytest

from secondbrain import bridges, cli, decisions, explain, overlap, pointers, reviews, service
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest, source_check
from secondbrain.store import read_bytes
from secondbrain.vault import read_snapshot
from tests.helpers import DOMAIN, EXISTING, PILLAR, TOPIC
from tests.ingest_helpers import (CARDS, OTHER_DOMAIN, OTHER_MOC, OTHER_PILLAR, OTHER_TOPIC, SOURCE_NAME,
                                  build_ingest_vault, is_source_check_task, keep_all, with_diagrams)
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


#: The merge check's answer: the second domain's card is the same concept as the first domain's card.
SAME_ACROSS_DOMAINS = {'arrival-order': {'decision': 'same', 'with': 'queue-order',
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


def moc_pages(root, needle):
    """The generated map pages whose text holds ``needle``."""
    from secondbrain import layout as layouts
    found = {}
    for path in sorted((root / layouts.of(root).mocs).rglob('*.md')):
        text = path.read_text(encoding='utf-8')
        if needle in text:
            found[str(path.relative_to(root))] = text
    return found


def test_the_merge_check_judging_same_across_two_domains_writes_one_card_and_a_pointer(tmp_path):
    """The bad sample: the merge check says ``same``, and the card it repeats is of another domain."""
    root, job_id, result, seen = run(tmp_path, lambda packet: keep_all(packet, **SAME_ACROSS_DOMAINS))
    assert result['status'] == 'applied'
    ids = {p['slug']: p['id'] for p in result['produced']}
    # no second card, and no bridge: the concept has one description, in the card that had it first
    assert 'arrival-order' not in ids and 'queue-order' in ids
    assert result['source_check']['merged'] == [] and result['source_check']['bridges'] == []
    [pointer] = result['pointers']
    assert pointer['slug'] == 'arrival-order' and pointer['into'] == ids['queue-order']
    assert (pointer['domain'], pointer['pillar']) == (OTHER_DOMAIN, OTHER_PILLAR)
    # the description did not move or double: the canonical card holds it, verbatim, and nothing was added
    snapshot = read_snapshot(root)
    canonical = snapshot.cards[ids['queue-order']]
    assert canonical.domain == DOMAIN
    assert 'The order holds while markers are lit.' not in canonical.body
    assert not any(p.id == 'arrival-order' for p in snapshot.cards.values())
    # the second domain's pillar page carries the pointer, and only the pointer: no description of its own
    [text] = list(moc_pages(root, 'canonical card stands in').values())
    assert 'the canonical card stands in "attention"' in text
    assert OTHER_PILLAR in text
    # the record the readers use (one row, the digest of the card it points at)
    [row] = pointers.load(root)
    assert row['kind'] == 'pointer' and row['into'] == ids['queue-order'] and row['domain'] == OTHER_DOMAIN
    assert row['into_sha'] == canonical.sha256
    cross = service.Service(root).status()['cross_domain']
    assert cross['pointers'] == 1 and cross['bridges'] == 0 and cross['candidates'] == 0


def test_a_vault_without_a_strong_bridge_type_gets_the_weak_one_and_says_so(tmp_path):
    root, result = one_chunk(tmp_path, 'extends', strong=False)
    assert result['status'] == 'applied'
    [bridge] = result['bridges']
    assert bridge['type'] == 'similar_to'
    cards = read_snapshot(root).cards
    [produced] = result['produced']
    assert [r['type'] for r in cards[produced['id']].fm['relations']] == ['similar_to']


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
    # the producer wrote that relation itself, so its note stays
    assert {'type': 'same_concept_as', 'target': EXISTING,
            'note': 'the same concept in another domain'} in cards[produced['id']].fm['relations']
    assert EXISTING in cards and cards[EXISTING].domain == DOMAIN
    # and the pair is recorded, so a later change of the canonical card can be noticed (it is the bridge's end)
    [row] = pointers.load(root)
    assert row['kind'] == 'bridge' and row['narrow'] == produced['id'] and row['into'] == EXISTING


def test_a_chain_of_close_bridges_is_never_one_concept_but_a_chain_of_strong_ones_is():
    edges = [('a', 'same_concept_as', 'b'), ('b', 'same_concept_as', 'c')]
    assert bridges.same_concept(edges, 'a', 'c') is True
    close = [('a', 'same_concept_as', 'b'), ('b', 'similar_to', 'c')]
    assert bridges.same_concept(close, 'a', 'c') is False
    assert bridges.same_concept([('a', 'similar_to', 'b'), ('b', 'similar_to', 'c')], 'a', 'c') is False
    assert bridges.same_concept([('a', 'similar_to', 'b'), ('b', 'same_concept_as', 'c')], 'a', 'c') is False


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


def test_the_chunk_reviewer_judging_same_across_two_domains_writes_a_pointer_and_no_second_card(tmp_path):
    """The same concept seen one level up: the chunk reviewer says ``same`` against a card of another domain.

    Before the one rule, :func:`secondbrain.overlap.route` turned every ``same`` into a drop whatever the domains,
    so the second domain lost the concept; a version after that wrote a second card and a bridge, which left two
    descriptions to keep in step. Now the second domain's pillar page points at the one card that holds it.
    """
    root, result = one_chunk(tmp_path, 'same')
    assert result['status'] == 'applied'
    assert result['produced'] == [] and result['bridges'] == []
    [pointer] = result['pointers']
    assert pointer['into'] == EXISTING and (pointer['domain'], pointer['pillar']) == (OTHER_DOMAIN, OTHER_PILLAR)
    snapshot = read_snapshot(root)
    assert EXISTING in snapshot.cards and snapshot.cards[EXISTING].domain == DOMAIN
    assert not any(c.domain == OTHER_DOMAIN and c.title.startswith('Tide') for c in snapshot.cards.values())
    [text] = list(moc_pages(root, 'canonical card stands in').values())
    assert OTHER_PILLAR in text and 'the canonical card stands in "attention"' in text
    cross = service.Service(root).status()['cross_domain']
    assert cross['pointers'] == 1 and cross['bridges'] == 0


def test_the_same_inside_one_domain_is_still_dropped(tmp_path):
    """The control: one domain keeps the rule it had, and writes no pointer either."""
    root = build_ingest_vault(tmp_path / 'vault')
    add_strong_bridge_type(root)
    settings(chunk_chars=1000)
    (root / '00_Inbox' / SOURCE_NAME).write_text(SOURCE, encoding='utf-8')
    same_domain = {**second_domain_card(), 'domain': DOMAIN, 'pillar': PILLAR, 'topic': TOPIC}

    def producer(task):
        text = task.packet['chunk']['text']
        cards = [same_domain] if ORDER_QUOTE in text else []
        rows = [{'source_quote': c['source_quotes'][0], 'disposition': 'card', 'cards': [c['slug']], 'reason': 'r'}
                for c in cards]
        rows.append({'source_quote': text.strip().splitlines()[-1].strip(), 'disposition': 'out_of_scope',
                     'reason': 'rest'})
        return {'cards': cards, 'new_topics': [], 'coverage': rows}

    def reviewer(task):
        if is_source_check_task(task):
            return keep_all(task.packet)
        return {'cards': [{'slug': 'tide-order', 'decision': 'accept', 'overlap': 'same', 'with': [EXISTING],
                           'reason': 'it says what the existing card says'}],
                'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=with_diagrams(producer)),
                            reviewer=FakeExecutor('claude-test', responder=reviewer))
    assert result['status'] == 'applied'
    assert result['produced'] == [] and result['pointers'] == []
    [dup] = result['duplicates']
    assert dup['slug'] == 'tide-order' and dup['by'] == 'reviewer' and 'how' not in dup
    assert pointers.load(root) == []


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
    # the bridge says where the two differ, in the judge's words on this pair
    assert held[0]['note'] == 'the same point said in the second domain'
    assert service.Service(root).status()['cross_domain']['strong'] == 1


def test_the_program_says_which_relation_type_it_changed_for_a_bridge(tmp_path):
    """The judge's probe: the card named the other card ``depends_on``, and the bridge took that relation's place.

    A relation the producer wrote is not changed quietly: the result names the card, the target, the type it had
    and the one it now has (the step in the decision record says why, too).
    """
    root, result = one_chunk(tmp_path, 'extends', relations=[
        {'type': 'depends_on', 'target': EXISTING, 'note': 'it needs that card first'}])
    assert result['status'] == 'applied'
    [retyped] = result['retyped_relations']
    [produced] = result['produced']
    assert retyped == {'card': produced['id'], 'target': EXISTING, 'was': ['depends_on'],
                       'now': result['bridges'][0]['type']}
    cards = read_snapshot(root).cards
    assert [r['type'] for r in cards[produced['id']].fm['relations'] if r['target'] == EXISTING] == ['same_concept_as']
    # the record keeps the reason: a reader of the job can see what the bridge replaced
    steps = [e for e in result['decision_record'] if e.get('op') == 'retype_relation']
    assert steps and steps[-1]['data']['was'] == ['depends_on'] and 'depends_on' in steps[-1]['data']['why']


def test_route_keeps_a_same_in_one_domain_a_drop_and_a_same_across_two_domains_a_pointer():
    """The control for the level the chunk reviewer gives: only two domains change ``same`` into a pointer."""
    entries: list = []
    decisions.record(entries, 'overlap.existing', 'card-a', {'overlap': 'same', 'with': ['card-b'], 'reason': 'r'},
                     role='reviewer', slot='c01')
    assert overlap.route(entries, 'card-a')['outcome'] == 'drop'  # no domains told: as before bridges
    assert overlap.route(entries, 'card-a', {'card-a': DOMAIN, 'card-b': DOMAIN}.get)['outcome'] == 'drop'
    pointed = overlap.route(entries, 'card-a', {'card-a': DOMAIN, 'card-b': OTHER_DOMAIN}.get)
    assert pointed['outcome'] == 'drop' and pointed['how'] == 'point' and pointed['with'] == ['card-b']
    assert overlap.outcome(entries, 'card-a', checked=False)['outcome'] == 'drop'
    assert overlap.outcome(entries, 'card-a', checked=False,
                           domain_of={'card-a': DOMAIN, 'card-b': OTHER_DOMAIN}.get)['outcome'] == 'drop'


def test_the_source_levels_read_a_same_across_two_domains_as_nothing_written():
    """The merge check's level, read the way the write reads it: ``merge`` for one domain, nothing for two."""
    entries: list = []
    decisions.step(entries, 'classification', 'card-a', 'propose', role='producer', slot='c01',
                   data={'domain': DOMAIN, 'pillar': PILLAR, 'topic': TOPIC})
    decisions.record(entries, 'overlap.existing', 'card-a', {'overlap': 'distinct', 'with': [], 'reason': 'r'},
                     role='reviewer', slot='c01')
    decisions.record(entries, 'overlap.source', 'card-a', {'overlap': 'same', 'with': ['card-b'], 'reason': 'r'},
                     role='merge_check', slot='xc01')
    same = {'card-a': DOMAIN, 'card-b': DOMAIN}.get
    other = {'card-a': DOMAIN, 'card-b': OTHER_DOMAIN}.get
    assert overlap.outcome(entries, 'card-a', checked=True, domain_of=same)['outcome'] == 'merge'
    assert overlap.outcome(entries, 'card-a', checked=True, domain_of=other)['outcome'] == 'drop'


def test_the_apply_step_reads_the_source_outcomes_with_the_same_domains_as_the_write(tmp_path):
    """The judge's second point: ``_apply_job`` recomputed the outcomes of the patch set without the domains.

    It read right only because every card of the patch set has an ``outcome.program`` step (which short-circuits
    the levels). Should that step ever be missing, the same domains as the write decide now, so the two cannot
    differ.
    """
    entries: list = []
    decisions.step(entries, 'classification', 'card-a', 'propose', role='producer', slot='c01',
                   data={'domain': DOMAIN, 'pillar': PILLAR, 'topic': TOPIC})
    decisions.record(entries, 'overlap.existing', 'card-a', {'overlap': 'distinct', 'with': [], 'reason': 'r'},
                     role='reviewer', slot='c01')
    decisions.record(entries, 'overlap.source', 'card-a', {'overlap': 'same', 'with': ['card-b'], 'reason': 'r'},
                     role='merge_check', slot='xc01')
    cards = {'card-a': {'domain': DOMAIN, 'pillar': PILLAR, 'topic': TOPIC}}
    fates = {'card-a': {'how': 'written', 'target': 'card-b'}}
    with_domains = ingest._fate_outcomes(entries, [{'accepted': cards, 'repeats': {}}], fates, True,
                                         {'card-a': DOMAIN, 'card-b': OTHER_DOMAIN})
    assert with_domains == {'card-a': 'drop'}  # nothing is written: the pointer stands for it
    # the same record read without the domains is the old, wrong answer; it is what the fix replaced
    assert overlap.outcome(entries, 'card-a', checked=True)['outcome'] == 'merge'


def test_explain_says_in_which_domain_the_bridge_stands_and_what_that_side_says(tmp_path):
    root, result = one_chunk(tmp_path, 'extends')
    assert result['status'] == 'applied'
    [produced] = result['produced']
    snapshot = read_snapshot(root)
    nodes = explain.build(snapshot, {'kind': 'card', 'ids': [produced['id']]})
    bridged = [n for n in nodes if n['kind'] == 'card' and n.get('bridges')]
    assert bridged and bridged[0]['card_id'] == produced['id']
    [bridge] = bridged[0]['bridges']
    assert bridge['target'] == EXISTING and bridge['strength'] == 'same'
    assert bridge['domain_title'] == 'Example Attention Map'
    page = explain.assemble({'language': 'en', 'anchor': {'kind': 'card', 'ids': [produced['id']]},
                             'nodes': nodes},
                            {'nodes': [{'id': n['id'], 'explain': 'It says what this card holds.',
                                        **({'recap': 'Back up: the two fit.'} if n['children'] else {})}
                                      for n in nodes]},
                            {}, snapshot)
    words = explain.to_markdown(page)
    assert 'this concept is also used in "Example Attention Map"' in words
    assert 'what it says there: ' in words  # what the other side says, not only where it stands
    assert 'this concept is also used in' in explain.to_html(page)


def test_explain_says_that_the_other_domain_points_here(tmp_path):
    """The canonical card is explained and another domain only points at it: the sentence says so."""
    root, result = one_chunk(tmp_path, 'same')
    assert result['status'] == 'applied'
    snapshot = read_snapshot(root)
    nodes = explain.build(snapshot, {'kind': 'card', 'ids': [EXISTING]})
    [node] = [n for n in nodes if n['kind'] == 'card' and n.get('anchor')]
    assert node['pointers'] == ['Example Pebble Map']
    page = explain.assemble({'language': 'en', 'anchor': {'kind': 'card', 'ids': [EXISTING]}, 'nodes': nodes},
                            {'nodes': [{'id': n['id'], 'explain': 'It says what this card holds.',
                                        **({'recap': 'Back up: the two fit.'} if n['children'] else {})}
                                      for n in nodes]}, {}, snapshot)
    assert 'this concept is also used in "Example Pebble Map", which points here' in explain.to_markdown(page)


def test_status_reads_the_cross_domain_line(tmp_path):
    root, job_id, result, seen = run(tmp_path, lambda packet: keep_all(packet, **SAME_ACROSS_DOMAINS))
    assert result['status'] == 'applied'
    status = service.Service(root).status()
    assert status['cross_domain']['bridges'] == 0
    assert status['cross_domain']['pointers'] == 1
    assert status['cross_domain']['by_domain_pair'] == {}
    assert status['cross_domain']['candidates'] == 0


# --- one description: the pillar page is built again, and a bridge leaves its narrow card to be looked at -------

def test_a_pillar_page_with_a_pointer_is_built_again_when_the_canonical_card_changes(tmp_path):
    """The pointer line is read from the canonical card: changing that card must reach the other domain's page."""
    root, result = one_chunk(tmp_path, 'same')
    assert result['status'] == 'applied'
    [(page_path, page)] = moc_pages(root, 'canonical card stands in').items()
    assert '已有卡正文' in page  # what the canonical card says, read from that one card
    existing = read_snapshot(root).cards[EXISTING]
    raw = (root / existing.path).read_text(encoding='utf-8')
    (root / existing.path).write_text(raw.replace('已有卡正文', '已有卡正文写了新的一句', 1), encoding='utf-8')
    before = read_bytes(root, page_path)
    # only the canonical card's domain is named: the map that holds the pointer has to come with it
    state = service.refresh(root, job_id='moc-test', domains=[DOMAIN], apply=True)
    assert state['status'] == 'applied'
    after = read_bytes(root, page_path).decode('utf-8')
    assert after != before
    assert '已有卡正文写了新的一句' in after and '已有卡正文，保持' not in after
    # the row keeps the digest it was written with: the page is built again while the card differs from it
    [row] = pointers.load(root)
    assert row['into_sha'] != read_snapshot(root).cards[EXISTING].sha256


def test_a_bridge_leaves_its_narrow_card_to_be_looked_at_when_the_canonical_card_changes(tmp_path):
    """`sb review queue` lists the narrow card first, as ``reread``; editing that card ends the wait."""
    root, result = one_chunk(tmp_path, 'extends')
    assert result['status'] == 'applied'
    [produced] = result['produced']
    now = reviews.now_local()
    before = reviews.queue(root, now)
    assert before['reread_count'] == 0
    existing = read_snapshot(root).cards[EXISTING]
    raw = (root / existing.path).read_text(encoding='utf-8')
    (root / existing.path).write_text(raw.replace('已有卡正文', '已有卡正文改了', 1), encoding='utf-8')
    after = reviews.queue(root, now)
    assert after['reread_count'] == 1
    [row] = [r for r in after['cards'] if r['kind'] == 'reread']
    assert row['id'] == produced['id']
    assert service.Service(root).status()['cross_domain']['waiting'] == 1
    # looking at the narrow card is editing it: the wait ends
    narrow = read_snapshot(root).cards[produced['id']]
    narrow_raw = (root / narrow.path).read_text(encoding='utf-8')
    (root / narrow.path).write_text(narrow_raw + '\nOne more reading pass.\n', encoding='utf-8')
    assert reviews.queue(root, now)['reread_count'] == 0


def test_settle_gives_one_answer_for_every_level_and_both_domains():
    strong = lambda domain: ('similar_to', 'same_concept_as')  # noqa: E731
    assert bridges.settle('existing', 'distinct', 'other', DOMAIN, OTHER_DOMAIN, strong)['how'] == 'distinct'
    assert bridges.settle('existing', 'same', 'other', DOMAIN, DOMAIN, strong) == \
        {'how': 'drop', 'type': None, 'reason': None}
    assert bridges.settle('existing', 'extends', 'other', DOMAIN, DOMAIN, strong)['how'] == 'merge'
    assert bridges.settle('source', 'same', 'other', DOMAIN, DOMAIN, strong)['how'] == 'merge'
    # one concept: no second description, a pointer
    for side, level in (('existing', 'same'), ('source', 'same')):
        point = bridges.settle(side, level, 'other', DOMAIN, OTHER_DOMAIN, strong)
        assert point['how'] == 'point' and point['type'] is None and 'pointer' in point['reason']
    # close, but each domain says its own thing: a narrow card each and a bridge
    apart = bridges.settle('existing', 'extends', 'other', DOMAIN, OTHER_DOMAIN, strong)
    assert apart['how'] == 'apart' and apart['type'] == 'same_concept_as'
    assert 'one concept in two domains' in apart['reason'] and '(same_concept_as)' in apart['reason']
    assert bridges.settle('source', 'extends', 'other', DOMAIN, OTHER_DOMAIN, strong)['how'] == 'apart'
    weak = bridges.settle('existing', 'extends', 'other', DOMAIN, OTHER_DOMAIN, lambda domain: ('similar_to',))
    assert weak['how'] == 'apart' and weak['type'] == 'similar_to'
    none = bridges.settle('existing', 'extends', 'other', DOMAIN, OTHER_DOMAIN, lambda domain: ())
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

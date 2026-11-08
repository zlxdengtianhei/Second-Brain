"""Candidate cards (secondbrain.retrieval) and card views (secondbrain.cards.view), on invented cards."""
from __future__ import annotations

import ast
import inspect
import json
import textwrap

import pytest

from secondbrain import cards as cardfmt
from secondbrain import config, jobs, retrieval, search
from secondbrain.pipelines import ingest, source_check
from secondbrain.service import Service
from secondbrain.vault import Card, Snapshot, read_snapshot
from tests.helpers import DOMAIN, PILLAR, TOPIC
from tests.ingest_helpers import SOURCE_NAME, build_ingest_vault


def card(cid, title, essence, props, flashcards=(('What is it?', 'It is.'),), *, topic=TOPIC, relations=()):
    body = (f'## Essence\n{essence}\n\n## Propositions\n' + ''.join(f'- P{i}. {p}\n' for i, p in enumerate(props, 1))
            + '\n## Review cards\n' + ''.join(f'{q}::{a}\n' for q, a in flashcards))
    fm = {'id': cid, 'title': title, 'domain': DOMAIN, 'pillar': PILLAR, 'topic': topic,
          'relations': [{'type': 'similar_to', 'target': t} for t in relations]}
    return Card(id=cid, title=title, domain=DOMAIN, path=f'{cid}.md', raw=b'', fm=fm, body=body, sha256='')


def snap(*cards):
    return Snapshot(root=None, cards={c.id: c for c in cards}, registry={}, catalog={}, taxonomies={}, issues=[],
                    fingerprint='')


LANTERN = card('demo_lit-markers', 'Lit markers let readers skip', 'Writers light a marker every eight items.',
               ['A writer lights a marker every eight items.', 'A reader holding a marker skips the items between.'],
               [('How often is a marker lit?', 'Every eight items.')])


# --- views ---------------------------------------------------------------------------------------------------
def test_each_view_level_holds_the_ones_before_it():
    shown = {level: cardfmt.view(LANTERN, level) for level in cardfmt.VIEWS}
    assert shown['title'] == {'id': 'demo_lit-markers', 'title': 'Lit markers let readers skip'}
    assert shown['brief'] == {**shown['title'], 'domain': DOMAIN, 'pillar': PILLAR, 'topic': TOPIC,
                              'essence': 'Writers light a marker every eight items.'}
    assert shown['full'] == {**shown['brief'], 'propositions': ['A writer lights a marker every eight items.',
                                                                'A reader holding a marker skips the items between.'],
                             'flashcards': [{'q': 'How often is a marker lit?', 'a': 'Every eight items.'}]}
    assert shown['body'] == {**shown['brief'], 'body': LANTERN.body}
    with pytest.raises(ValueError, match='view level'):
        cardfmt.view(LANTERN, 'digest')


def test_the_essence_is_cut_and_a_card_out_of_format_shows_its_body_at_full():
    long = card('demo_long', 'Long', 'word ' * 200, ['one'])
    assert len(cardfmt.view(long, 'brief')['essence']) == cardfmt.ESSENCE_CHARS
    legacy = Card(id='demo_old', title='Old', domain=DOMAIN, path='o.md', raw=b'', fm={},
                  body='## Essence\nOld text.\n\n## Propositions\n- first point\n- second point\n', sha256='')
    shown = cardfmt.view(legacy, 'full')
    assert shown['format'] == 'legacy' and shown['excerpt'].startswith('## Essence') and 'propositions' not in shown
    broken = card('demo_broken', 'Broken', 'e', ['one'])
    broken = Card(**{**broken.__dict__, 'body': broken.body + 'a line without the separator\n'})
    assert cardfmt.view(broken, 'full')['format'] == 'legacy'


# --- routes --------------------------------------------------------------------------------------------------
def test_a_named_card_that_does_not_exist_is_an_error_not_a_silent_drop():
    with pytest.raises(retrieval.RetrievalError, match='demo_missing'):
        retrieval.candidates(None, snap(LANTERN), 'markers', k=0, named=['demo_missing'])


def test_routes_come_in_packing_order_and_a_card_found_twice_keeps_every_route():
    a = card('demo_a', 'A', 'a', ['a'], relations=['demo_b'])
    b = card('demo_b', 'B', 'b', ['b'])
    c = card('demo_c', 'C', 'c', ['c'], topic='Other')
    s = snap(a, b, c)
    found = retrieval.merge([[{'id': 'demo_b', 'rank': 2, 'via': ['words']}],
                             [{'id': 'demo_b', 'rank': 1, 'via': ['named']}, {'id': 'demo_c', 'rank': 1, 'via': ['hop']}]])
    assert found == [{'id': 'demo_b', 'rank': 1, 'via': ['words', 'named']}, {'id': 'demo_c', 'rank': 1, 'via': ['hop']}]
    assert retrieval.hop(s, ['demo_a'], 1) == ['demo_b'] and retrieval.hop(s, ['demo_a'], 0) == []
    assert retrieval.shelf(s, DOMAIN, PILLAR, TOPIC) == ['demo_a', 'demo_b']


def test_k_auto_keeps_each_roles_count_and_a_number_sets_every_role():
    assert [retrieval.k_for('auto', r) for r in ('reviewer', 'merge_check', 'written')] == [5, 3, 5]
    assert {retrieval.k_for(7, r) for r in ('reviewer', 'merge_check', 'written')} == {7}


# --- the budget ----------------------------------------------------------------------------------------------
def test_named_cards_are_not_counted_and_past_twice_the_budget_the_chunk_is_split():
    named = card('demo_named', 'Named', 'n', ['x' * 900])
    other = card('demo_other', 'Other', 'o', ['y' * 900])
    s = snap(named, other)
    entries = [{'id': 'demo_named', 'rank': 1, 'via': ['named']}, {'id': 'demo_other', 'rank': 1, 'via': ['words']}]
    cost = cardfmt.view_chars(cardfmt.view(other, 'full'))
    table, report = retrieval.fit(s, entries, cost, {'named': 'full', 'words': 'full'})
    assert 'propositions' in table['demo_named']  # not lowered, not counted
    assert 'propositions' in table['demo_other'] and report['chars'] - report['named_chars'] == cost
    table, _ = retrieval.fit(s, entries, cost - 1, {'named': 'full', 'words': 'full'})
    assert 'propositions' in table['demo_named'] and 'propositions' not in table['demo_other']
    with pytest.raises(retrieval.TooLarge) as raised:
        retrieval.fit(s, entries, cost // 2 - 10, {'named': 'full', 'words': 'full'})
    assert raised.value.code == 'CHUNK_TOO_LARGE'


def test_past_the_budget_titles_go_last_and_the_report_says_so():
    cards_ = [card(f'demo_{i}', f'Card {i}', 'e' * 50, ['p' * 100]) for i in range(5)]
    entries = [{'id': c.id, 'rank': n, 'via': ['words']} for n, c in enumerate(cards_, 1)]
    title = cardfmt.view_chars(cardfmt.view(cards_[0], 'title'))
    table, report = retrieval.fit(snap(*cards_), entries, 2 * title, {'words': 'full'})
    assert list(table) == ['demo_0', 'demo_1'] and report['left_out'] == ['demo_4', 'demo_3', 'demo_2']
    assert all(set(v) == {'id', 'title'} for v in table.values())


# --- every packet takes existing cards through cards.view -------------------------------------------------------
PACKET_BUILDERS = [ingest.producer_packet, ingest.review_packet, ingest._review_candidates,
                   ingest._producer_candidates, ingest._source_check_input, ingest.retry,
                   source_check.frozen_input, retrieval.candidates, retrieval.fit]


def body_reads(source: str) -> list[int]:
    """Lines of ``source`` that read a card's ``.body`` (or the old essence readers) instead of its view."""
    tree = ast.parse(textwrap.dedent(source))
    bad = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == 'body']
    bad += [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
            and getattr(n.func, 'id', getattr(n.func, 'attr', None)) in {'essence', '_card_digest', 'card_packet'}]
    return bad


@pytest.mark.parametrize('builder', PACKET_BUILDERS, ids=lambda f: f.__qualname__)
def test_packet_builders_read_existing_cards_only_through_their_view(builder):
    assert body_reads(inspect.getsource(builder)) == []


def test_the_view_check_turns_red_on_a_builder_that_reads_the_body():
    assert body_reads('def review_packet(snapshot, cid):\n    return {"x": snapshot.cards[cid].body}\n') == [2]


# --- old names -----------------------------------------------------------------------------------------------
def test_search_neighbors_is_an_alias_of_cards_by_id():
    s = snap(LANTERN)
    with pytest.warns(DeprecationWarning):
        old = search.neighbors(s, ['demo_lit-markers'])
    assert old == search.cards_by_id(s, ['demo_lit-markers'])


def test_read_task_candidates_part_reads_the_chunks_and_answers_to_the_old_name(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    service = Service(root)
    part = service.read_task(prepared['job_id'], 'candidates')
    chunk = jobs.read_input(root, prepared['job_id'])['chunks'][0]
    assert part[chunk['chunk_id']] == {'candidates': chunk['candidates'], 'candidate_cards': chunk['candidate_cards']}
    assert service.read_task(prepared['job_id'], 'neighbors') == part


def test_a_chunk_frozen_with_neighbors_reads_as_candidates():
    old = {'neighbors': [{'id': 'kc_a', 'title': 'A', 'domain': 'd', 'essence': 'e'}]}
    assert ingest._chunk_candidates(old) == {'candidates': {'chunk': [{'id': 'kc_a', 'rank': 1, 'via': ['words']}]},
                                             'candidate_cards': {'kc_a': old['neighbors'][0]}}


def test_candidate_settings_are_frozen_and_checked(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    settings = config.parse({'ingest': {'candidates_k': 7, 'candidates_chars': 20000, 'candidates_hop': 1,
                                        'neighbors_per_card': 3}})  # the retired key is read and ignored
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME, settings=settings)
    frozen = jobs.read_input(root, prepared['job_id'])['settings']
    assert (frozen['candidates_k'], frozen['candidates_chars'], frozen['candidates_hop']) == (7, 20000, 1)
    for bad in ({'candidates_k': 'many'}, {'candidates_chars': 10}, {'candidates_hop': -1}):
        with pytest.raises(ValueError):
            config.parse({'ingest': bad})
    assert json.dumps(frozen)  # plain values only

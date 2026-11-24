"""Two strengths of a bridge, told apart, and the step that gives an older vault the strong one.

Three answers for a concept met in a second domain (SKOS): one concept and nothing new -- a pointer, no second card;
one concept and something new -- a narrow card and a *strong* bridge (``same_concept_as``); alike but another
question -- a *weak* bridge (``similar_to``). The judge's level decides the first two; the strong type is the
program's step on a judged ``extends``, and a catalog written before the type existed gets it by
``sb bridges --upgrade`` and gives it back by ``--undo`` without a card edited by hand.
"""
from __future__ import annotations

import json

import pytest

from secondbrain import bridges, bridgetypes, cli, pointers, service
from secondbrain.catalog import validate_catalog
from secondbrain.moc import build_mocs
from secondbrain.vault import read_snapshot
from tests.helpers import EXISTING
from tests.ingest_helpers import OTHER_DOMAIN, build_ingest_vault
from tests.test_cross_domain_bridge import one_chunk

pytestmark = pytest.mark.usefixtures('old_review_rules')

#: Words of the existing card's title, as a map line shows it.
EXISTING_TITLE_PART = ''


def catalog_of(root):
    return json.loads((root / '_data' / 'secondbrain' / 'catalog.json').read_text(encoding='utf-8'))


# --- the catalog step -------------------------------------------------------------------------------------------

def test_the_upgrade_adds_one_type_and_the_undo_gives_the_catalog_back():
    before = {'relation_types': ['depends_on', 'required_by', 'similar_to'],
              'inverse_relations': {'depends_on': 'required_by', 'required_by': 'depends_on',
                                    'similar_to': 'similar_to'},
              'relation_gloss': {'similar_to': 'alike'}, 'domain_relations': {}, 'domains': {}, 'tracks': []}
    after = bridges.upgraded(before, 'zh')
    assert bridges.declared(after) == {'strong': ['same_concept_as'], 'weak': ['similar_to']}
    assert after['inverse_relations']['same_concept_as'] == 'same_concept_as'
    assert after['relation_gloss']['same_concept_as'] == bridges.strong_gloss('zh')
    assert bridges.upgraded(after, 'zh') is None  # declared already: nothing to do
    assert bridges.downgraded(after) == before
    assert bridges.downgraded(before) is None
    assert bridges.upgraded(before, 'en')['relation_gloss']['same_concept_as'] == bridges.strong_gloss('en')


def test_the_undo_takes_the_type_out_of_every_place_a_catalog_names_it():
    catalog = {'relation_types': ['similar_to'], 'inverse_relations': {'similar_to': 'similar_to',
                                                                       'same_concept_as': 'same_concept_as'},
               'domain_relations': {'a': ['same_concept_as']}, 'relation_gloss': {'same_concept_as': 'x'}}
    out = bridges.downgraded(catalog)
    assert out['domain_relations'] == {'a': []} and 'same_concept_as' not in out['inverse_relations']
    assert 'relation_gloss' not in out


def test_sb_bridges_upgrade_plans_then_applies_then_undoes(tmp_path, capsys):
    root = build_ingest_vault(tmp_path / 'vault')
    original = catalog_of(root)
    assert bridgetypes.run(root)['declared']['strong'] == []
    planned = bridgetypes.run(root, upgrade=True)
    assert planned['status'] == 'planned' and catalog_of(root) == original  # a plan writes nothing
    done = bridgetypes.run(root, upgrade=True, apply=True)
    assert done['declared_after']['strong'] == ['same_concept_as']
    upgraded = catalog_of(root)
    validate_catalog(upgraded)
    assert 'same_concept_as' in upgraded['relation_types']
    assert bridgetypes.run(root, upgrade=True, apply=True)['status'] == 'unchanged'
    # through the command line too
    assert cli.main(['--vault', str(root), 'bridges', '--undo', '--apply']) in (0, None)
    capsys.readouterr()
    assert catalog_of(root) == original


# --- strong for a judged extends, weak otherwise, and the undo of written bridges --------------------------------

def test_an_extends_across_two_domains_gets_the_strong_bridge_and_status_counts_it(tmp_path):
    root, result = one_chunk(tmp_path, 'extends')
    assert result['status'] == 'applied'
    [bridge] = result['bridges']
    assert bridge['type'] == 'same_concept_as'
    cross = service.Service(root).status()['cross_domain']
    assert (cross['bridges'], cross['strong'], cross['weak'], cross['strong_type']) == (1, 1, 0, True)


def test_a_strong_type_no_judge_stands_behind_is_written_weak(tmp_path):
    """The producer names the existing card of another domain ``same_concept_as``, the reviewer finds another
    question (``distinct``): the two are alike, not one concept, so the card holds the weak type."""
    rel = [{'type': 'same_concept_as', 'target': EXISTING, 'note': 'looks like the same thing'}]
    root, result = one_chunk(tmp_path, 'distinct', relations=rel)
    assert result['status'] == 'applied'
    [produced] = result['produced']
    cards = read_snapshot(root).cards
    assert [(r['type'], r['target']) for r in cards[produced['id']].fm['relations']] == [('similar_to', EXISTING)]
    [retyped] = [r for r in result['retyped_relations'] if r['target'] == EXISTING]
    assert retyped['was'] == ['same_concept_as'] and retyped['now'] == 'similar_to'
    cross = service.Service(root).status()['cross_domain']
    assert (cross['strong'], cross['weak']) == (0, 1)


def test_retype_unjudged_leaves_a_judged_pair_and_one_domain_alone():
    rels = [{'type': 'same_concept_as', 'target': 'x'}, {'type': 'same_concept_as', 'target': 'y'},
            {'type': 'same_concept_as', 'target': 'z'}, {'type': 'depends_on', 'target': 'x'}]
    domain = {'x': 'b', 'y': 'b', 'z': 'a'}.get
    out, changed = bridges.retype_unjudged(rels, 'a', domain, judged={'x'})
    assert [r['type'] for r in out] == ['same_concept_as', 'similar_to', 'same_concept_as', 'depends_on']
    assert changed == [{'target': 'y', 'was': 'same_concept_as', 'now': 'similar_to'}]


def test_the_undo_turns_written_strong_bridges_weak_on_both_ends(tmp_path):
    root, result = one_chunk(tmp_path, 'extends')
    [produced] = result['produced']
    narrow = produced['id']
    before = read_snapshot(root).cards
    assert any(r['type'] == 'same_concept_as' for r in before[narrow].fm['relations'])
    plan = bridgetypes.run(root, undo=True)
    assert plan['status'] == 'planned'
    assert {c['card'] for c in plan['cards_retyped']} >= {narrow}
    done = bridgetypes.run(root, undo=True, apply=True)
    assert done['declared_after']['strong'] == []
    cards = read_snapshot(root).cards
    for cid in (narrow, EXISTING):
        types = [r['type'] for r in cards[cid].fm.get('relations') or []]
        assert 'same_concept_as' not in types
        assert 'same_concept_as::' not in cards[cid].body
    assert any(r['type'] == 'similar_to' and r['target'] == EXISTING for r in cards[narrow].fm['relations'])
    assert 'similar_to::' in cards[narrow].body
    # the bridge rows follow, and the retyping is not taken for a change of what the canonical card says
    rows = pointers.load(root)
    assert [r['type'] for r in rows if r['kind'] == 'bridge'] == ['similar_to']
    cross = service.Service(root).status()['cross_domain']
    assert (cross['strong'], cross['weak'], cross['waiting'], cross['strong_type']) == (0, 1, 0, False)
    validate_catalog(catalog_of(root))
    assert read_snapshot(root).cards[narrow].domain == OTHER_DOMAIN


# --- what a reader sees ------------------------------------------------------------------------------------------

def test_status_says_when_the_catalog_cannot_hold_a_strong_bridge(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    status = service.Service(root).status()
    assert status['cross_domain']['strong_type'] is False
    assert 'sb bridges --upgrade' in status['cross_domain_strong_hint']
    bridgetypes.run(root, upgrade=True, apply=True)
    status = service.Service(root).status()
    assert status['cross_domain']['strong_type'] is True and 'cross_domain_strong_hint' not in status


def test_a_map_page_tells_a_strong_bridge_from_a_weak_one(tmp_path):
    root, result = one_chunk(tmp_path, 'extends')
    assert result['status'] == 'applied'
    texts = [v.decode('utf-8') for v in build_mocs(read_snapshot(root)).files.values()]
    lines = [line for text in texts for line in text.splitlines()]
    strong = [line for line in lines if 'same_concept_as→' in line]
    assert strong and all('same concept, other map' in line or '同一概念·跨域' in line for line in strong)
    # a weak bridge keeps the plain label
    root2, result2 = one_chunk(tmp_path / 'weak', 'distinct')
    texts = [v.decode('utf-8') for v in build_mocs(read_snapshot(root2)).files.values()]
    weak = [line for text in texts for line in text.splitlines() if 'similar_to→' in line and EXISTING_TITLE_PART in line]
    assert weak and not any('same concept' in line or '同一概念' in line for line in weak)

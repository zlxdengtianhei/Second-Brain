"""Relation direction: one meaning per type, shown to producer and reviewer, and contradictions refused."""
from __future__ import annotations

import json

import pytest

from secondbrain import jobs
from secondbrain.catalog import relation_meanings
from secondbrain.pipelines import ingest
from secondbrain.vault import read_snapshot
from tests.ingest_helpers import SOURCE_NAME, build_ingest_vault, first_candidate

#: The shape of a hand-kept catalog: glosses on one side of each pair, domain words, a symmetric type.
CATALOG = {
    'language': 'zh',
    'relation_types': ['depends_on', 'required_by', 'similar_to'],
    'inverse_relations': {'depends_on': 'required_by', 'required_by': 'depends_on', 'tool_for': 'used_by',
                          'used_by': 'tool_for', 'similar_to': 'similar_to'},
    'relation_gloss': {'depends_on': '前者依赖后者', 'tool_for': '前者是达成后者的工具/手段',
                       'similar_to': '相似，但不确定是否同一件事'},
    'domain_relations': {'astro': ['tool_for', 'used_by', 'step_of']},
}


def test_every_type_gets_one_meaning_from_its_own_gloss_or_its_inverse():
    meanings = relation_meanings(CATALOG)
    assert list(meanings) == ['depends_on', 'required_by', 'similar_to', 'tool_for', 'used_by', 'step_of']
    assert meanings['tool_for'] == 'A tool_for B：前者是达成后者的工具/手段'
    assert meanings['used_by'] == 'A used_by B 等于 B tool_for A；tool_for：前者是达成后者的工具/手段'
    assert meanings['required_by'] == 'A required_by B 等于 B depends_on A；depends_on：前者依赖后者'
    assert meanings['similar_to'] == 'A similar_to B 与 B similar_to A 同义：相似，但不确定是否同一件事'
    assert meanings['step_of'] == 'A step_of B'
    assert relation_meanings({**CATALOG, 'language': 'en'})['used_by'].startswith('A used_by B means B tool_for A')


def prepared(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    return root, job, jobs.read_input(root, job['job_id'])


def test_producer_and_reviewer_see_what_each_type_means(tmp_path):
    root, job, packet = prepared(tmp_path)
    state = jobs.load(root, job['job_id'])
    produce = ingest.producer_packet(root, state, packet, 'c01')
    assert produce['relation_types'] == relation_meanings(packet['catalog'])
    assert set(produce['relation_types']) == {'similar_to', 'part_of', 'depends_on'}
    prompt = (ingest.Path(ingest.__file__).parents[1] / 'prompts' / 'produce_cards.md').read_text(encoding='utf-8')
    assert 'A is the card you are writing and B is the `target`' in prompt
    review = (ingest.Path(ingest.__file__).parents[1] / 'prompts' / 'review_cards.md').read_text(encoding='utf-8')
    assert 'with A this card and B the target' in review


def check(root, packet, candidate):
    return ingest.validate_candidate(packet, candidate, slot='c01', other_cards=[], kept_slugs=set(),
                                     existing_ids=set(read_snapshot(root).cards), existing_titles=set())


def test_a_pair_written_both_ways_round_is_refused(tmp_path):
    root, _, packet = prepared(tmp_path)
    packet['catalog'] = {**packet['catalog'], **{k: CATALOG[k] for k in
                                                ('relation_types', 'inverse_relations', 'relation_gloss')}}
    packet['catalog']['relation_types'] = CATALOG['relation_types'] + ['tool_for', 'used_by']
    base = first_candidate()
    a, b = base['cards'][0]['slug'], base['cards'][1]['slug']

    def with_relations(rel_a, rel_b=()):
        candidate = json.loads(json.dumps(base))
        candidate['cards'][0]['relations'] = list(rel_a)
        candidate['cards'][1]['relations'] = list(rel_b)
        return candidate
    check(root, packet, with_relations([{'type': 'tool_for', 'target': b}], [{'type': 'used_by', 'target': a}]))
    with pytest.raises(ingest.IngestError, match='say opposite things'):
        check(root, packet, with_relations([{'type': 'tool_for', 'target': b}, {'type': 'used_by', 'target': b}]))
    with pytest.raises(ingest.IngestError, match='cannot both hold') as err:
        check(root, packet, with_relations([{'type': 'used_by', 'target': b}], [{'type': 'used_by', 'target': a}]))
    assert 'A used_by B means B tool_for A' in str(err.value)  # the meaning, in the vault's language


def test_a_contrast_relation_needs_the_contrast_written_out(tmp_path):
    root, _, packet = prepared(tmp_path)
    packet['catalog'] = {**packet['catalog'], 'relation_types': ['similar_to', 'part_of', 'depends_on',
                                                                  'contrasts_with']}
    candidate = json.loads(json.dumps(first_candidate()))  # the helper's cards are shared
    candidate['cards'][0]['relations'] = [{'type': 'contrasts_with', 'target': candidate['cards'][3]['slug']}]
    candidate['cards'][0]['confusions'] = []
    with pytest.raises(ingest.IngestError, match='states the contrast in confusions'):
        check(root, packet, candidate)
    candidate['cards'][0]['confusions'] = ['A lantern queue lets readers skip ahead; the order card only keeps order.']
    check(root, packet, candidate)


def task_text(name):
    return (ingest.Path(ingest.__file__).parents[1] / 'prompts' / name).read_text(encoding='utf-8')


def test_the_reviewer_is_asked_to_check_examples_links_and_better_domains():
    review = task_text('review_cards.md')
    assert 'every flashcard question and answer against the text' in review
    assert 'Check each example on its own' in review
    assert 'name the existing card id with the relation type to add' in review
    assert 'also when it is not among `taxonomies`: `domains` lists every domain' in review


def test_the_producer_is_asked_for_intuition_contrasts_and_links_to_neighbours():
    produce = task_text('produce_cards.md')
    assert 'write them whenever the chunk or the `neighbors` support them' in produce
    assert 'Link a card to every neighbour it is closely related to' in produce


@pytest.mark.parametrize('marked', [True, False])
def test_a_type_marked_as_its_own_inverse_is_written_back_the_same_way(tmp_path, marked):
    from secondbrain import layout
    from secondbrain.vault import parse_note
    from tests.helpers import EXISTING
    from tests.test_ingest import run_default
    root = build_ingest_vault(tmp_path / 'vault')
    path = root / layout.DEFAULT.catalog
    catalog = json.loads(path.read_text(encoding='utf-8'))
    if not marked:  # a hand-kept catalog may list similar_to without marking it symmetric
        del catalog['inverse_relations']['similar_to']
    path.write_text(json.dumps(catalog), encoding='utf-8')
    _, result, _ = run_default(root, layout.DEFAULT)
    assert result['status'] == 'applied'
    fm, body = parse_note(read_snapshot(root).cards[EXISTING].raw)
    back = [r for r in fm.get('relations') or [] if r['type'] == 'similar_to']
    assert bool(back) == marked and ('similar_to:: [[Lantern queue]]' in body) == marked

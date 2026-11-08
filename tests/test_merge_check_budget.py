"""The merge check's link candidates are packed like every other packet: within `candidates_chars`, each card once."""
from __future__ import annotations

import json

from secondbrain import cards as cardfmt
from secondbrain.pipelines import ingest
from secondbrain.vault import read_snapshot
from tests.helpers import DOMAIN, PILLAR, TOPIC, build_vault

TERMS = [f'vorlic{chr(97 + i // 26)}{chr(97 + i % 26)}' for i in range(60)]


def vault(tmp_path):
    root = build_vault(tmp_path / 'vault')
    folder = root / '10_Knowledge' / DOMAIN
    for term in TERMS:  # sixty cards on one subject, each essence ~300 characters
        (folder / f'demo_{term}.md').write_text(
            f'---\nid: demo_{term}\ntitle: The {term} gate\ndomain: {DOMAIN}\npillar: {PILLAR}\ntopic: {TOPIC}\n---\n'
            f'## Essence\nA {term} gate routes tokens. ' + 'It keeps routing tokens in order. ' * 9 + '\n', encoding='utf-8')
    return root


def decision_set(n):
    accepted = {f'new-{i}': {'title': f'Routing gate {i}', 'domain': DOMAIN, 'pillar': PILLAR, 'topic': TOPIC,
                             'essence': f'A {TERMS[i]} gate routes tokens in order.',
                             'propositions': [f'A {TERMS[i]} gate keeps order.'], 'flashcards': [], 'relations': []}
                for i in range(n)}
    return [{'chunk': 'c01', 'accepted': accepted, 'topics': {}, 'notes': [], 'repeats': {}}]


def packet_for(root, budget):
    return {'settings': {'candidates_k': 15, 'candidates_chars': budget}, 'source': {'filename': 'f.md', 'text': ''},
            'catalog': read_snapshot(root).catalog, 'chunks': [{'chunk_id': 'c01'}]}


def view_chars(frozen) -> int:
    """Characters of the existing cards' views in a merge check packet: the table, and views carried in rows."""
    rows = [{k: v for k, v in e.items() if k not in {'rank', 'via', 'domain'}} if isinstance(e, dict) else {'id': e}
            for c in frozen['cards'] for e in c['candidates']]
    inline = sum(cardfmt.view_chars(r) for r in rows if set(r) != {'id'})
    return inline + sum(cardfmt.view_chars(v) for v in (frozen.get('candidate_cards') or {}).values())


def test_twenty_cards_of_link_candidates_fit_the_budget_and_each_existing_card_is_shown_once(tmp_path):
    root = vault(tmp_path)
    budget = 8000
    frozen = ingest._source_check_input(root, packet_for(root, budget), decision_set(20))
    assert view_chars(frozen) <= budget
    shown = frozen['candidate_cards']
    rows = [e for c in frozen['cards'] for e in c['candidates']]
    assert len(rows) > len(shown)  # many cards share candidates; the table shows each once
    assert all(isinstance(e, str) and e in shown for e in rows)  # rows name ids only (second verdict, not blocking 1)
    assert all(v['domain'] for v in shown.values())
    assert any(set(v) == {'id', 'title', 'domain'} for v in shown.values())  # less close ones: the title only
    packing = frozen['packing']
    assert packing['view_chars'] <= budget and packing['row_chars'] == len(json.dumps([c['candidates']
                                                                                      for c in frozen['cards']]))
    assert packing['row_chars'] < 20 * 15 * 30  # about one id per candidate, not a row of four fields
    # each card still has its 15 closest while the budget allows it
    roomy = ingest._source_check_input(root, packet_for(root, 10 ** 7), decision_set(20))
    assert all(len(c['candidates']) == 15 for c in roomy['cards'])
    assert view_chars(roomy) > budget


def test_the_merge_check_still_accepts_a_link_to_a_candidate_shown_by_title_only(tmp_path):
    from secondbrain.pipelines import source_check
    root = vault(tmp_path)
    frozen = ingest._source_check_input(root, packet_for(root, 3000), decision_set(5))
    card = frozen['cards'][0]
    target = next(e for e in card['candidates'] if set(frozen['candidate_cards'][e]) == {'id', 'title', 'domain'})
    answer = {'cards': [{'slug': c['slug'], 'overlap': 'distinct', 'with': [], 'reason': 'own point',
                         **({'links': [{'target': target, 'type': 'similar_to', 'note': 'n'}]} if c is card
                            else {'alone': 'a test card'})}
                        for c in frozen['cards']], 'new_topics': []}
    assert source_check.validate_answer(frozen, answer) is answer

"""The producer's search units: a long block of item lines is searched line by line; k is 15 for every role."""
from __future__ import annotations

import pytest

from secondbrain import config
from secondbrain.pipelines import ingest, routing
from secondbrain.pipelines.routing import LINE_SPLIT_CHARS, passages, route
from secondbrain.vault import read_snapshot
from tests.helpers import DOMAIN, PILLAR, TOPIC, build_vault

#: An invented "summary chapter": one heading, then items with no blank line between them, one term each.
TERMS = [f'quarnel{chr(97 + i)}{chr(97 + i)}' for i in range(24)]
SUMMARY = '## Mix-ups\n\n' + '\n'.join(
    f'{n}. Confusing {term} with a plain counter -> {term} keeps its own order (see {n}.1)'
    for n, term in enumerate(TERMS, 1)) + '\n'


def summary_vault(tmp_path):
    root = build_vault(tmp_path / 'vault')
    folder = root / '10_Knowledge' / DOMAIN
    for term in TERMS:
        (folder / f'demo_{term}.md').write_text(
            f'---\nid: demo_{term}\ntitle: The {term}\ndomain: {DOMAIN}\npillar: {PILLAR}\ntopic: {TOPIC}\n---\n'
            f'## Essence\nA {term} is a structure of its own.\n\n## Propositions\n- P1. A {term} keeps order.\n\n'
            f'## Review cards\nWhat is a {term}?::A structure.\nDoes a {term} keep order?::Yes.\n', encoding='utf-8')
    return root


def found(candidates: dict) -> set:
    return {e['id'] for rows in candidates.values() for e in rows}


def test_a_long_block_of_item_lines_is_searched_line_by_line():
    parts = passages(SUMMARY)
    assert len(parts) == len(TERMS) and parts[0].startswith('Mix-ups\n1. Confusing quarnelaa')
    assert parts[5] == f'6. Confusing {TERMS[5]} with a plain counter -> {TERMS[5]} keeps its own order (see 6.1)'


@pytest.mark.parametrize('text, count', [
    ('Short list:\n' + ''.join(f'- item {i}\n' for i in range(9)), 1),                   # short: one passage
    (''.join(f'- a long item {i} that says enough to make the block long, and then some more.\n' for i in range(6)),
     1),                                                                                 # six items: one query
    ('A long prose paragraph on one line. ' * 20 + '\n', 1),                           # one line, however long
    ('```\n' + 'x = 1\n' * 120 + '```\n', 1),                                          # code stays whole
    ('First line of three.\n' + 'Second line, long enough to pass the limit. ' * 10 + '\nThird.\n', 1),  # 3 lines
    (''.join(f'- a long item {i} that says enough to make the block long, and then some more.\n' for i in range(7)),
     7),                                                                                 # seven items: seven
])
def test_only_long_blocks_of_seven_lines_or_more_are_split(text, count):
    assert len(passages(text)) == count


def test_the_summary_chapter_finds_every_card_line_by_line_and_misses_some_as_one_block(tmp_path, monkeypatch):
    root = summary_vault(tmp_path)
    snapshot = read_snapshot(root)
    targets = {f'demo_{t}' for t in TERMS}
    by_line = route(root, snapshot, SUMMARY, domains=1, k=15)
    assert targets <= found(by_line['candidates']) and by_line['passages'] == len(TERMS)
    for n, term in enumerate(TERMS, 1):  # each line finds its own card first
        assert by_line['candidates'][f'p{n}'][0]['id'] == f'demo_{term}'
    monkeypatch.setattr(routing, 'LINE_SPLIT_CHARS', 10 ** 9)  # the block as one query, as before
    by_block = route(root, snapshot, SUMMARY, domains=1, k=15)
    assert by_block['passages'] == 1 and len(targets - found(by_block['candidates'])) >= len(TERMS) - 15
    assert len(SUMMARY) > LINE_SPLIT_CHARS


def test_every_role_reads_fifteen_cards_per_query_by_default(tmp_path):
    assert config.IngestConfig().candidates_k == 15 and config.IngestConfig().candidates_hop == 0
    root = summary_vault(tmp_path)
    snapshot = read_snapshot(root)
    card = {'slug': 'x', 'title': 'quarnel order', 'essence': 'quarnel keeps order',
            'propositions': ['A quarnel keeps its own order.'], 'relations': []}
    packet = {'settings': {'candidates_k': 15, 'candidates_chars': 400000, 'candidates_hop': 0,
                           'candidates_shelf': False, 'review_named': True, 'review_words_view': 'full'}}
    per_card, _ = ingest._review_candidates(root, snapshot, packet, [card])
    assert len(per_card['x']) == 15
    old_job = {'settings': {}}  # frozen before the setting existed: each role's old count
    per_card, _ = ingest._review_candidates(root, snapshot, old_job, [card])
    assert len(per_card['x']) == 5


def test_a_new_job_freezes_fifteen_and_the_producer_reads_fifteen_per_passage(tmp_path):
    import json
    from secondbrain import layout as layouts
    root = summary_vault(tmp_path)
    registry = root / layouts.DEFAULT.registry  # a vault sb can write to: every card registered
    data = json.loads(registry.read_text(encoding='utf-8'))
    data['notes'] += [{'id': f'demo_{t}', 'path': layouts.DEFAULT.card_path(DOMAIN, f'demo_{t}.md'),
                       'title': f'The {t}', 'domain': DOMAIN} for t in TERMS]
    registry.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    (root / '00_Inbox').mkdir(exist_ok=True)
    (root / '00_Inbox' / 'mixups.md').write_text('# Mix-ups\n\n' + SUMMARY, encoding='utf-8')
    job = ingest.prepare(root, root / '00_Inbox' / 'mixups.md')
    from secondbrain import jobs
    packet = jobs.read_input(root, job['job_id'])
    assert packet['settings']['candidates_k'] == 15
    rows = {k: r for k, r in packet['chunks'][0]['candidates'].items() if not k.startswith(ingest.SHELF_KEY)}
    assert max(len(r) for r in rows.values()) == 15 and 'chunk' not in rows


def test_a_retry_reviewer_sees_fifteen_written_cards_per_card(tmp_path):
    root = summary_vault(tmp_path)
    snapshot = read_snapshot(root)
    card = {'slug': 'x', 'title': 'quarnel order', 'essence': 'quarnel keeps order',
            'propositions': ['A quarnel keeps its own order.'], 'relations': []}
    written = {'c01': [{'id': f'demo_{t}', 'slug': f's-{t}', 'title': f'The {t}'} for t in TERMS]}
    packet = {'settings': {'candidates_k': 15, 'candidates_chars': 400000, 'candidates_hop': 0,
                           'candidates_shelf': False, 'review_named': True, 'review_words_view': 'full'},
              'retry': {'written': written}}
    per_card, _ = ingest._review_candidates(root, snapshot, packet, [card])
    assert sum('written' in e['via'] for e in per_card['x']) == 15

"""The cross-domain line on the way back up: the review path of `sb explain` and the daily review page.

PR #4 gave a concept that two domains share one description (a pointer in the second domain for ``same``, a narrow
card and a bridge for ``extends``) and a sentence in the drill-down heading of a card that is one end of either.
The line of the way back up was missing: the review path of an explanation and the page's section "back up to the
higher level" said nothing of the other domain. These tests build the two-domain vault of the bridge tests (one
pointer, one bridge) and read each place a reader meets the concept.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from secondbrain import crossref, explain, jobs
from secondbrain.executors.fake import FakeExecutor
from secondbrain.projections import review_projection
from secondbrain.runner import run_job
from secondbrain.vault import read_snapshot
from tests.helpers import EXISTING
from tests.ingest_helpers import OTHER_DOMAIN, OTHER_PILLAR
from tests.test_cross_domain_bridge import one_chunk
from tests.test_explain import reviewer, writer

pytestmark = pytest.mark.usefixtures('old_review_rules')

NOTE = 'the same point said in the second domain'


def _page(root, anchor, language='en'):
    """The page data of an explanation whose texts a stand-in wrote (the program's lines are what is read)."""
    snapshot = read_snapshot(root)
    nodes = explain.build(snapshot, anchor)
    texts = writer(type('T', (), {'packet': {'nodes': nodes}})())
    return explain.assemble({'language': language, 'anchor': anchor, 'nodes': nodes}, texts, {}, snapshot)


def _parts(page):
    """The Markdown of a page split into the drill-down and the review path."""
    text = explain.to_markdown(page)
    return text.split('## Review: from the details up', 1)


def test_a_bridge_says_where_the_two_cards_differ_and_the_review_path_names_the_other_domain(tmp_path):
    root, result = one_chunk(tmp_path, 'extends')
    assert result['status'] == 'applied'
    [produced] = result['produced']
    snapshot = read_snapshot(root)
    [bridge] = crossref.bridged(snapshot, snapshot.cards[EXISTING])
    assert bridge['target'] == produced['id'] and bridge['note'] == NOTE
    page = _page(root, {'kind': 'card', 'ids': [EXISTING]})
    drill, path = _parts(page)
    # the drill-down heading had the sentence; now it also says where the two differ
    assert f'where the two differ: {NOTE}' in drill
    assert '.;' not in drill  # the essence's full stop does not stay before the semicolon
    # and the way back up: the step of the card carries the line, with the other domain and what it says
    assert '- Across domains: this concept is also used in "Example Pebble Map"' in path
    assert f'where the two differ: {NOTE}' in path
    html = explain.to_html(page)
    assert html.split('data-tab="path"', 1)[1].count('class="cross"') == 1


def test_a_pointer_is_named_at_the_canonical_card_step_and_at_the_pillar_that_holds_it(tmp_path):
    root, result = one_chunk(tmp_path, 'same')
    assert result['status'] == 'applied'
    drill, path = _parts(_page(root, {'kind': 'card', 'ids': [EXISTING]}))
    assert '- Across domains: this concept is also used in "Example Pebble Map", which points here' in path
    # the pillar of the second domain holds the concept only as a pointer, and says which card stands where
    page = _page(root, {'kind': 'pillar', 'domain': OTHER_DOMAIN, 'pillar': OTHER_PILLAR})
    [pillar] = [n for n in page['nodes'].values() if n['kind'] == 'pillar']
    assert [p['target'] for p in pillar['pointed']] == [EXISTING]
    drill, path = _parts(page)
    assert 'only as a pointer; its card stands in "Example Attention Map" and says: ' in drill
    back = path.split(f'Back up to "{OTHER_PILLAR}"', 1)[1]
    assert '- Across domains: this pillar holds the concept "已有卡" only as a pointer' in back


def test_a_card_without_a_bridge_or_pointer_gets_no_cross_domain_line(tmp_path):
    """The control: the review path of a card of the second domain that nothing joins stays as it was."""
    root, result = one_chunk(tmp_path, 'extends')
    plain = next(c.id for c in read_snapshot(root).cards.values()
                 if c.domain == OTHER_DOMAIN and not c.fm.get('relations'))
    drill, path = _parts(_page(root, {'kind': 'card', 'ids': [plain]}))
    assert 'Across domains' not in path and 'also used in' not in drill


def test_the_writer_is_shown_the_other_domain_and_both_pages_carry_the_line_in_either_mode(tmp_path):
    """A whole job: the writer's packet holds the bridge (card, domain, what it says, where it differs), whatever
    runs it, and the pages written at the end carry the program's line in the drill-down and the review path."""
    root, result = one_chunk(tmp_path, 'extends')
    seen = []

    def spy(task):
        packet = task.packet.get('input', task.packet)
        seen.extend(n['bridges'] for n in packet['nodes'] if n.get('bridges'))
        return writer(task)
    state = explain.prepare(root, ids=[EXISTING], out=root / 'out')
    packet = jobs.read_input(root, state['job_id'])  # what a host hand-off hands over, and an API call sends
    [held] = [n['bridges'] for n in packet['nodes'] if n.get('bridges')]
    assert held[0]['domain_title'] == 'Example Pebble Map' and held[0]['note'] == NOTE and held[0]['essence']
    done = run_job(root, state['job_id'], producer=FakeExecutor('deepseek-chat', responder=spy),
                   reviewer=FakeExecutor('glm-4', responder=reviewer()))
    assert done['status'] == 'applied' and seen
    md = Path(done['outputs']['markdown']).read_text(encoding='utf-8')
    assert md.count('this concept is also used in "Example Pebble Map"') == 2  # the heading and the review path
    assert 'class="cross"' in Path(done['outputs']['html']).read_text(encoding='utf-8')
    report = json.loads(Path(done['outputs']['report']).read_text(encoding='utf-8'))
    assert report['unresolved'] == {}


def _back_up(root):
    page = review_projection(read_snapshot(root), date(2026, 11, 22))
    text = next(v for k, v in page.files.items() if k.endswith('.md')).decode()
    return text.split('## After these cards, back up to the higher level', 1)[1]


def test_the_review_page_ties_a_bridge_to_the_card_of_the_other_domain(tmp_path):
    root, result = one_chunk(tmp_path, 'extends')
    [produced] = result['produced']
    back = _back_up(root)
    line = next(x for x in back.splitlines() if 'Across domains' in x)
    assert 'also has a card in "Example Pebble Map"' in line
    assert f'|{read_snapshot(root).cards[produced["id"]].title}]]' in line  # a link to the card there
    assert f'where the two differ: {NOTE}' in line


def test_the_review_page_says_that_the_other_domain_only_points_here(tmp_path):
    root, result = one_chunk(tmp_path, 'same')
    lines = [x for x in _back_up(root).splitlines() if 'Across domains' in x]
    assert any('is also used in "Example Pebble Map", which only points here' in x for x in lines)
    # and the pillar of the second domain, which holds the concept as a pointer, says which card stands where
    assert any('holds the concept [[' in x and 'of "Example Attention Map"' in x for x in lines)


def test_a_vault_with_no_bridge_or_pointer_has_no_cross_domain_line(tmp_path):
    from tests.ingest_helpers import build_ingest_vault
    root = build_ingest_vault(tmp_path / 'vault')
    assert 'Across domains' not in _back_up(root)

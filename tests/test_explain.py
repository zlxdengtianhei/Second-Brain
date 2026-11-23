"""`sb explain`: the drill-down tree, the program's check of the texts, the reviewed job and its pages, and the way
back up on the review page."""
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from secondbrain import explain, jobs
from secondbrain.executors.fake import FakeExecutor
from secondbrain.projections import review_projection
from secondbrain.runner import run_job
from secondbrain.schemas import AnswerError
from secondbrain.vault import read_snapshot

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples' / 'vault'


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv('SECONDBRAIN_STATE_DIR', str(tmp_path / 'state'))
    return shutil.copytree(EXAMPLE, tmp_path / 'v')


def _deep_card(snapshot):
    """A card with prerequisites that have prerequisites of their own."""
    def down(c):
        return [t for _, t in explain._down(c) if t in snapshot.cards]
    return max(snapshot.cards.values(), key=lambda c: (sum(1 + len(down(snapshot.cards[t])) for t in down(c)), c.id))


def writer(task):
    packet = task.packet.get('input', task.packet)
    rows = []
    for n in packet['nodes']:
        if n['kind'] == 'ref':
            continue
        kids = n['children']
        text = f"This level is {n.get('title', 'a group')}, as the cards say it. " + ' '.join(
            f'[[{c}]] adds one part of it.' for c in kids)
        row = {'id': n['id'], 'explain': text.strip().ljust(60, '.')}
        if kids:
            row['recap'] = 'Together ' + ', '.join(f'[[{c}]]' for c in kids) + ' make this level.'
        if n['kind'] == 'group':
            row['title'] = 'A group of cards'
        rows.append(row)
    return {'nodes': rows}


def reviewer(revise_first=None, always=False):
    calls = {'n': 0}

    def answer(task):
        calls['n'] += 1
        nodes = task.packet['candidate']['nodes']
        out = []
        for i, r in enumerate(nodes):
            bad = always or (calls['n'] == 1 and revise_first is not None and i == revise_first)
            out.append({'id': r['id'], 'decision': 'revise' if bad else 'accept',
                        'reason': 'the bridge to the child is not in the card' if bad else 'supported by the cards'})
        return {'items': out}
    return answer


def test_a_card_drills_down_its_prerequisites_and_a_repeated_card_is_a_reference(vault):
    snapshot = read_snapshot(vault)
    card = _deep_card(snapshot)
    nodes = explain.build(snapshot, {'kind': 'card', 'ids': [card.id]}, depth=3)
    kinds = [n['kind'] for n in nodes]
    assert kinds[:3] == ['domain', 'pillar', 'card'] and nodes[2]['card_id'] == card.id
    assert nodes[2]['children'], 'the card has prerequisites below it'
    by_id = {n['id']: n for n in nodes}
    for n in nodes:  # every child points back to its parent; a ref points to a card node already in the tree
        assert all(by_id[c]['parent'] == n['id'] for c in n['children'])
        if n['kind'] == 'ref':
            assert by_id[n['target']]['kind'] == 'card'
    cards = [n['card_id'] for n in nodes if n['kind'] == 'card']
    assert len(cards) == len(set(cards))  # a card stands once; again it is a reference


def test_a_pillar_lists_its_cards_or_groups_them(vault):
    snapshot = read_snapshot(vault)
    pillar = next(c.fm['pillar'] for c in snapshot.cards.values() if c.domain == 'learning')
    nodes = explain.build(snapshot, {'kind': 'pillar', 'domain': 'learning', 'pillar': pillar})
    assert [n['kind'] for n in nodes[:2]] == ['domain', 'pillar']
    assert {n['kind'] for n in nodes[2:]} <= {'group', 'card'}


def test_the_check_refuses_a_text_that_skips_a_child_or_the_way_back(vault):
    state = explain.prepare(vault, ids=[_deep_card(read_snapshot(vault)).id], out=vault / 'out')
    packet = jobs.read_input(vault, state['job_id'])
    good = writer(type('T', (), {'packet': packet})())
    assert explain.validate(packet, good) is good
    parent = next(r for r in good['nodes'] if r.get('recap'))
    skipped = json.loads(json.dumps(good))
    row = next(r for r in skipped['nodes'] if r['id'] == parent['id'])
    row['explain'] = row['explain'].split('[[')[0].ljust(60, '.')
    with pytest.raises(AnswerError, match='must name each child'):
        explain.validate(packet, skipped)
    no_recap = json.loads(json.dumps(good))
    next(r for r in no_recap['nodes'] if r['id'] == parent['id']).pop('recap')
    with pytest.raises(AnswerError, match='recap'):
        explain.validate(packet, no_recap)
    missing = {'nodes': good['nodes'][1:]}
    with pytest.raises(AnswerError, match='not written'):
        explain.validate(packet, missing)


def test_a_reviewed_explanation_is_repaired_once_then_written_with_its_review_path(vault):
    state = explain.prepare(vault, ids=[_deep_card(read_snapshot(vault)).id], out=vault / 'out')
    producer = FakeExecutor('deepseek-chat', responder=writer)
    review = FakeExecutor('glm-4', responder=reviewer(revise_first=1))
    done = run_job(vault, state['job_id'], producer=producer, reviewer=review)
    assert done['status'] == 'applied' and done['unresolved'] == []
    assert [t.step for t in producer.calls] == ['produce', 'repair']
    md = Path(done['outputs']['markdown']).read_text(encoding='utf-8')
    html = Path(done['outputs']['html']).read_text(encoding='utf-8')
    assert '## Drill-down' in md and '## Review: from the details up' in md and 'Back up to' in md
    assert '[[n' not in md  # child ids became their titles
    assert '<details id="n1" open>' in html and 'class="step back"' in html
    report = json.loads(Path(done['outputs']['report']).read_text(encoding='utf-8'))
    assert report['levels'] >= 4 and report['repair_rounds'] == 1
    assert not any(p.is_relative_to(vault / '10_Knowledge') for p in map(Path, done['outputs'].values()))


def test_a_node_the_review_never_passes_is_written_with_a_marker(vault):
    state = explain.prepare(vault, ids=[_deep_card(read_snapshot(vault)).id], out=vault / 'out')
    done = run_job(vault, state['job_id'], producer=FakeExecutor('deepseek-chat', responder=writer),
                   reviewer=FakeExecutor('glm-4', responder=reviewer(always=True)))
    assert done['status'] == 'applied' and done['escalation_required'] and done['unresolved']
    md = Path(done['outputs']['markdown']).read_text(encoding='utf-8')
    assert md.count('> Not passed by the review:') == len(done['unresolved'])


def test_the_review_page_goes_back_up_to_each_pillar_and_uses_a_pillar_recap(vault):
    snapshot = read_snapshot(vault)
    page = review_projection(snapshot, date(2026, 11, 22))
    text = next(v for k, v in page.files.items() if k.endswith('.md')).decode()
    assert '## After these cards, back up to the higher level' in text
    assert '- It answers: ' in text
    first = next(c for c in snapshot.cards.values() if f'|{c.title}]]' in text)  # a card of today's queue
    pillar, domain = first.fm['pillar'], first.domain
    state = explain.prepare(vault, pillar=pillar, domain=domain, out=vault / 'out')
    run_job(vault, state['job_id'], producer=FakeExecutor('deepseek-chat', responder=writer),
            reviewer=FakeExecutor('glm-4', responder=reviewer()))
    text = next(v for k, v in review_projection(read_snapshot(vault), date(2026, 11, 22)).files.items()
                if k.endswith('.md')).decode()
    part = text.split(f'**{pillar}**', 1)[1].split('\n- **', 1)[0]
    assert '  - Together: Together ' in part


def test_a_child_named_before_its_marker_is_shown_once(vault):
    """A writer that put a child's name just before its marker ("the four laws [[n4]]") is shown the name once:
    the marker becomes that name, bold in Markdown and a link in HTML, so the copy before it goes."""
    snapshot = read_snapshot(vault)
    state = explain.prepare(vault, ids=[_deep_card(snapshot).id], out=vault / 'out')
    packet = jobs.read_input(vault, state['job_id'])
    titles = {n['id']: n.get('title') for n in packet['nodes']}
    rows = writer(type('T', (), {'packet': packet})())['nodes']
    named = [(r, child) for r in rows for child in explain.MARKER.findall(r['explain']) if titles.get(child)]
    assert named, 'the writer names each child as a marker'
    for row, child in named:  # the same name twice, as the judge read it: "索引与向量数据库 **索引与向量数据库**"
        assert row['explain'].count(f'[[{child}]]') == 1
        row['explain'] = row['explain'].replace(f'[[{child}]]', f'{titles[child]} [[{child}]]')
    candidate = explain.validate(packet, {'nodes': rows})
    page = explain.assemble(packet, candidate, {}, snapshot)
    for _, child in named:
        title, once = titles[child], f'{titles[child]} [[{child}]] is the next step'
        assert explain._md_text(page, once).strip() == f'**{title}** is the next step'
        assert explain._html_text(page, once).strip() == (
            f'<a class="ref" href="#{child}">{title}</a> is the next step')
        # the pair alone, bold at the head of a text: nothing stands before it, and it still goes
        assert explain._md_text(page, f'**{title}** [[{child}]]').strip() == f'**{title}**'
        assert explain._html_text(page, f'**{title}** [[{child}]]').strip() == (
            f'<a class="ref" href="#{child}">{title}</a>')
    md, html = explain.to_markdown(page), explain.to_html(page)  # the pages carry no pair either
    for _, child in named:
        assert f'{titles[child]} **{titles[child]}**' not in md
        assert f'{titles[child]} <a class="ref"' not in html

"""A unit its writer never wrote leaves a marker in the article, where the unit would stand.

Before, such a unit was left out of the text: it was only named in the report (``escalated_units``, ``flagged``)
and on the owner's list, so a reader of the article could not see that a part was missing. As a paragraph the
review did not pass is marked in the text, the part now is too: a marker in its place says it was not written, why
(how many answers failed the checks, and the last message), and which cards it was to cover, as links. Markdown and
HTML alike; the report and the owner's list stay as they were.
"""
from __future__ import annotations

import re
from pathlib import Path

from secondbrain import jobs, labels
from secondbrain.config import ReviewPolicy
from secondbrain.executors.fake import FakeExecutor
from secondbrain.export import pipeline
from tests.test_export import DOMAIN, FakeMathJax, reviewer, vault_root, writer  # noqa: F401 - the fixture

LIMIT = ReviewPolicy().max_invalid_retries + 1
REASON = f'its writer answer failed validation {LIMIT} times: the answer needs a "parts" list'


def export_without(root, out, uid='u02'):
    job_id = pipeline.prepare(root, domain=DOMAIN, mode='sequential')['job_id']
    inner = writer()

    def produce(task):
        return {'parts': 5} if task.chunk == uid else inner(task)
    result = pipeline.advance(root, job_id, producer=FakeExecutor('gpt-writer', responder=produce),
                              reviewer=FakeExecutor('claude-reviewer', responder=reviewer()), checker=FakeMathJax(),
                              workers=1, out_dir=out, html=True)
    return job_id, result


def test_a_part_its_writer_never_wrote_is_marked_where_it_stands_with_why_and_its_cards(vault_root, tmp_path):
    from secondbrain.service import Service
    job_id, result = export_without(vault_root, tmp_path / 'out')
    assert result['status'] == 'complete'
    packet = jobs.read_input(vault_root, job_id)
    [unit] = [u for u in packet['units'] if u['id'] == 'u02']
    md = Path(result['files']['markdown']).read_text(encoding='utf-8')
    body = md.split('\n## 未写进正文的卡\n')[0]  # the text, without the list of unused cards at its end
    missing = [packet['cards'][cid]['cite'] for part in unit['parts'] for cid in part['cards']
               if f"[[{packet['cards'][cid]['cite']}]]" not in body]
    assert missing == [], f'the text does not show where these cards were to be: {missing}'
    text = labels.export('zh')
    for part in unit['parts']:  # one marker per part, under its heading, naming each of its cards as a link
        cards = text['card_sep'].join(f"[[{packet['cards'][cid]['cite']}]]" for cid in part['cards'])
        marker = text['missing'].format(reason=REASON, cards=cards)
        assert f'\n{marker}\n' in md
        if part['heading']:
            assert f"### {part['heading']}\n\n{marker}\n" in md
    # in its place: after what the unit before it wrote, before what the unit after it wrote
    first_card = lambda uid: packet['cards'][next(u for u in packet['units'] if u['id'] == uid)['parts'][0]['cards'][0]]
    before, after = first_card('u01')['cite'], first_card('u03')['cite']
    at = md.index(text['missing'].split('{')[0])
    assert md.index(f'[[{before}]]') < at < md.index(f'。[[{after}]]')
    # each link names a card of the export
    linked = re.findall(r'\[\[([^\]]+)\]\]', md[at:md.index('\n', at)])
    assert linked and set(linked) <= {c['cite'] for c in packet['cards'].values()}
    html = Path(result['files']['html']).read_text(encoding='utf-8')
    assert html.count('此处缺一段，没有写成') == len(unit['parts'])
    assert f'<span class="cite">{packet["cards"][unit["parts"][0]["cards"][0]]["cite"]}</span>' in html
    # the report and the owner's list as before
    report = result['report']
    assert 'u02' in report['escalated_units'] and [f['reason'] for f in report['flagged'] if f['paragraph'] == 'u02'] \
        == [REASON]
    assert report['coverage'] < 1.0
    assert all(packet['cards'][cid]['cite'] in report['unused'] for part in unit['parts'] for cid in part['cards'])
    items = [i for i in Service(vault_root).owner_items() if i.get('kind') == 'export_paragraph']
    assert 'u02' in {i['paragraph'] for i in items}


def test_an_english_article_marks_the_missing_part_in_english():
    from secondbrain.export import render
    article = {'title': 'T', 'language': 'en', 'meta': {'title': 'T'}, 'intro': [], 'unused': [],
               'sections': [{'pillar': 'Basics', 'transition': None, 'blocks': [
                   {'heading': 'Parts'}, {'id': 'u01/p1', 'missing': 'its writer answer failed validation 3 times: x',
                                          'cards': ['Card A', 'Card B']}]}]}
    md = render.to_markdown(article)
    assert '### Parts\n\n[A part is missing here; it was not written: its writer answer failed validation 3 times: x. ' \
           'It was to cover these cards: [[Card A]], [[Card B]]]\n' in md

"""Generated page text follows ``[cards] language`` (maps, HOME, entry block, review page).

The Chinese map pages must stay byte-identical to what earlier versions wrote:
``tests/fixtures/pages/moc_zh_golden.json`` was recorded by building
``tests/moc_fixture.snapshot()`` with the package at a87a7a8, before the page
text moved into :mod:`secondbrain.labels`.
"""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from secondbrain import cli, config, labels, moc, projections
from secondbrain.layout import VaultLayout
from secondbrain.vault import read_snapshot
from tests import moc_fixture
from tests.helpers import ENTRY_DOCS_LAYOUT, build_vault

GOLDEN = Path(__file__).parent / 'fixtures' / 'pages' / 'moc_zh_golden.json'
CJK = re.compile(r'[　-〿一-鿿＀-￯]')
DAY = date(2026, 10, 13)


def use(language):
    config.activate(replace(config.Settings(), card_language=language))


def literal_parts(template):
    """The fixed fragments of a %-format label (what must show up on a page)."""
    return [part for part in re.split(r'%[sdr]|%0\d+d', template) if part.strip()]


def built_pages(words):
    return {k: v.decode('utf-8') for k, v in moc.build_mocs(moc_fixture.snapshot(words)).files.items()}


def test_zh_map_pages_are_byte_identical_to_the_recorded_pages():
    use('zh')
    golden = json.loads(GOLDEN.read_text(encoding='utf-8'))
    files = moc.build_mocs(moc_fixture.snapshot(moc_fixture.ZH)).files
    assert sorted(files) == sorted(golden)
    for path, data in files.items():
        assert data == golden[path].encode('utf-8'), path


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_the_fixture_reaches_every_map_label(language):
    use(language)
    words = moc_fixture.ZH if language == 'zh' else moc_fixture.EN
    text = '\n'.join(built_pages(words).values())
    for key, value in labels.moc(language).items():
        for part in literal_parts(value):
            assert part in text, (key, part)


def test_english_map_pages_carry_no_chinese_text():
    use('en')
    pages = built_pages(moc_fixture.EN)
    assert 'Example Map A/00Unclassified--01.md' in ' '.join(pages).replace('20_MOCs/', '')
    for path, text in pages.items():
        assert not CJK.search(path + text), (path, CJK.findall(path + text))
    root = pages['20_MOCs/Example Map A.md']
    assert '## Pillars' in root and '## Related maps' in root and '- **(unknown)**' in root
    assert '[other map: Example Map B]' in pages['20_MOCs/Example Map A/01Structure--02.md']
    assert '[[10_Knowledge/db/c032.md|A/B compared]]' in pages['20_MOCs/Example Map A/01Structure--02.md']


def test_without_a_config_file_pages_are_english():
    pages = built_pages(moc_fixture.EN)
    assert not any(CJK.search(text) for text in pages.values())
    assert labels.language() == 'en'


def sb_commands(text):
    """Every `sb ...` span in ``text``, placeholders replaced by a value."""
    found = []
    for span in re.findall(r'`(sb [^`]+)`', text):
        found.append(shlex.split(re.sub(r'<[^>]+>', 'x', span))[1:])
    return found


def home_and_docs(tmp_path, language, lay=None):
    use(language)
    root = build_vault(tmp_path, lay=lay)
    for name in (lay.entry_docs if lay else ()):
        (root / name).write_text('<!-- GENERATED:BEGIN kb-stats -->old<!-- GENERATED:END kb-stats -->\n',
                                 encoding='utf-8')
    projection = projections.entry_projection(read_snapshot(root), DAY)
    return {k: v.decode('utf-8') for k, v in projection.files.items()}


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_home_points_daily_review_to_the_plugin_and_names_current_commands(tmp_path, language):
    files = home_and_docs(tmp_path, language, ENTRY_DOCS_LAYOUT)
    home = files['HOME.md']
    assert '\n```secondbrain-review\n```\n' in home
    assert '`sb review status`' in home and '`sb review queue`' in home
    for stale in ('Spaced Repetition', 'secondbrain today', 'secondbrain classify', 'secondbrain resume',
                  '`secondbrain '):
        assert stale not in home, stale
    # a plain link to the daily review page, for a vault where the plugin is not enabled yet
    page = projections.review_page(ENTRY_DOCS_LAYOUT, DAY)[:-3]
    block_end = home.index('```secondbrain-review\n```\n') + len('```secondbrain-review\n```\n')
    assert home[block_end:].lstrip('\n').startswith(f'[[{page}|') and '`sb today --apply`' in home
    commands = sb_commands(home) + sb_commands(files['使用指南.md'])
    assert ['docs', '--apply'] in commands and ['review', 'queue'] in commands
    for argv in commands:
        cli.parser().parse_args(argv)  # exits (fails the test) on a command the CLI does not have
    assert '[[使用指南]] · [[系统总览]]' in home
    if language == 'en':
        assert not CJK.search(home.replace('使用指南', '').replace('系统总览', ''))
        assert "## Today's review" in home and '## Commands' in files['使用指南.md']
    else:
        assert '## 今日复习' in home and '## 当前操作入口' in files['使用指南.md']


def test_entry_docs_are_empty_by_default(tmp_path):
    assert VaultLayout().entry_docs == ()
    files = home_and_docs(tmp_path, 'en')
    assert set(files) == {'HOME.md'}
    head = files['HOME.md'].split('## Maps')[0]
    assert re.findall(r'\[\[([^\]|]+)', head) == [projections.review_page(VaultLayout(), DAY)[:-3]]  # no doc links


@pytest.mark.parametrize('language, name, title', [
    ('zh', '50_Reviews/今日复习-2026-10-13.md', '# 今日复习 2026-10-13'),
    ('en', '50_Reviews/Daily review 2026-10-13.md', '# Daily review 2026-10-13'),
])
def test_review_page_name_and_text_follow_the_language(tmp_path, language, name, title):
    use(language)
    root = build_vault(tmp_path)
    files = projections.review_projection(read_snapshot(root), DAY).files
    assert name in files
    text = files[name].decode('utf-8')
    assert title in text and 'Spaced Repetition' not in text
    T = labels.pages(language)
    for key in ('due_cards', 'new_today'):
        assert f'## {T[key]}\n' in text, key
    assert T['new_left'].format(available=1, n=1) in text and '`sb review queue`' in text
    # the one card of the fixture is new (no registry events); its title is content, not page text
    [card] = read_snapshot(root).cards.values()
    assert f'[[{card.path[:-3]}|{card.title}]]' in text
    if language == 'en':
        assert not CJK.search(text.replace(card.title, ''))


def test_sb_docs_apply_rewrites_only_home(tmp_path, capsys):
    root = build_vault(tmp_path)
    (root / 'HOME.md').write_text('# old home\n', encoding='utf-8')
    assert cli.main(['--vault', str(root), 'docs', '--apply']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'applied' and result['applied_paths'] == ['HOME.md']
    assert '```secondbrain-review' in (root / 'HOME.md').read_text(encoding='utf-8')


@pytest.mark.parametrize('language, meaning', [
    ('en', 'A controlled_by B means B controls A; controls: steers'),
    ('zh', 'A controlled_by B 等于 B controls A；controls：steers'),
])
def test_a_new_inverse_relation_word_is_explained_from_its_gloss_in_the_vault_language(language, meaning):
    from secondbrain import catalog as catalogs, domcreate
    from tests.helpers import CATALOG
    use('zh' if language == 'en' else 'en')  # the catalog's recorded language decides, not the active one
    packet = {'domain': 'robotics', 'moc': 'Robotics Map', 'question': 'How do robots move?', 'track': 'ml'}
    proposal = {'relation_words': [{'type': 'controls', 'inverse': 'controlled_by', 'gloss': 'steers'}]}
    out = domcreate._catalog_after({**CATALOG, 'language': language}, packet, proposal)
    assert out['relation_gloss'] == {'controls': 'steers'}  # the inverse is not glossed separately
    assert {'controls', 'controlled_by'} <= set(out['relation_types'])  # both words are usable relations
    assert catalogs.relation_meanings(out)['controlled_by'] == meaning

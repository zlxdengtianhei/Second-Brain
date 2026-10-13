"""The English example vault in examples/vault works as the README quick start says.

The vault was built by the real ingest chain; these tests guard what a visitor
sees in a fresh copy with no config file: no problems, maps and HOME that
match the cards, English text only, links and relations that resolve, the
two untouched inbox notes, and the counts the READMEs give.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml

from secondbrain import cli

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples' / 'vault'
CJK = re.compile(r'[　-ヿ㐀-鿿豈-﫿＀-￯]')
WIKI = re.compile(r'\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]')


@pytest.fixture
def vault(tmp_path):
    return shutil.copytree(EXAMPLE, tmp_path / 'sb-example')


def run(capsys, root, *argv):
    code = cli.main(['--vault', str(root), *argv])
    out, err = capsys.readouterr()
    assert code == 0, err
    return json.loads(out)


def cards():
    return sorted((EXAMPLE / '10_Knowledge').rglob('*.md'))


def front_matter(path: Path) -> dict:
    text = path.read_text(encoding='utf-8')
    return yaml.safe_load(text.split('---\n', 2)[1])


def test_status_is_clean_and_two_notes_wait_in_the_inbox(vault, capsys):
    status = run(capsys, vault, 'status')
    assert status['issues'] == []
    assert status['cards'] == len(cards()) and 30 <= status['cards'] <= 45
    assert status['missing_classification'] == 0
    assert status['inbox'] == {'new': 2}
    inbox = run(capsys, vault, 'inbox', 'list')
    assert sorted(i['name'] for i in inbox) == ['Correlation and causation.md', 'Sleep and memory.md']
    language = run(capsys, vault, 'language')
    assert language['recorded'] == language['detected'] == 'en' and language['writes_allowed']


def test_maps_and_home_match_the_cards(vault, capsys):
    assert run(capsys, vault, 'refresh')['changed_files'] == 0
    from secondbrain.projections import generate
    counted = re.search(r'Counted on (\d{4}-\d{2}-\d{2})', (vault / 'HOME.md').read_text(encoding='utf-8'))
    result = generate(vault, job_id='docs-example-check', today=date.fromisoformat(counted[1]),
                      reviews=False, entries=True)
    assert result['changed_files'] == 0, result.get('paths')


def test_review_log_was_written_by_sb_review_log(vault, capsys):
    pytest.importorskip('fsrs')
    logs = sorted((EXAMPLE / '_data' / 'review').glob('events-*.jsonl'))
    assert [p.name for p in logs] == ['events-example.jsonl']
    events = [json.loads(line) for line in logs[0].read_text(encoding='utf-8').splitlines()]
    ids = {front_matter(p)['id'] for p in cards()}
    assert 3 <= len(events) <= 10 and {e['card'] for e in events} <= ids
    assert all(e['ts'].endswith('+00:00') and e['device'] == 'example' for e in events)
    totals = run(capsys, vault, 'review', 'status')['totals']
    assert totals['total'] == len(ids) and totals['learned'] == len({e['card'] for e in events})


def test_text_is_english():
    files = [p for p in EXAMPLE.rglob('*') if p.is_file()]
    assert files
    for path in files:
        text = path.read_text(encoding='utf-8')
        assert not CJK.search(text), (path.relative_to(EXAMPLE), CJK.search(text)[0])


def test_cards_have_english_sections_and_resolving_relations():
    catalog = json.loads((EXAMPLE / '_data/secondbrain/catalog.json').read_text(encoding='utf-8'))
    by_id = {front_matter(p)['id']: p for p in cards()}
    domains = {d for p in cards() for d in [front_matter(p)['domain']]}
    assert domains == set(catalog['domains'])
    cross_domain = 0
    for path in cards():
        text = path.read_text(encoding='utf-8')
        for heading in ('## Essence', '## Propositions', '## Review cards'):
            assert f'\n{heading}\n' in text, (path.name, heading)
        fm = front_matter(path)
        for rel in fm.get('relations') or []:
            target = by_id[rel['target']]
            back = {(r['type'], r['target']) for r in front_matter(target).get('relations') or []}
            assert (catalog['inverse_relations'][rel['type']], fm['id']) in back, (path.name, rel)
            cross_domain += front_matter(target)['domain'] != fm['domain']
    assert cross_domain >= 2
    assert sum('```mermaid' in p.read_text(encoding='utf-8') for p in cards()) >= 3


def test_docs_state_the_vault_counts():
    """The numbers the READMEs and the agents guide give for the example vault are the vault's."""
    by_domain = {}
    for path in cards():
        domain = front_matter(path)['domain']
        by_domain[domain] = by_domain.get(domain, 0) + 1
    n, diagrams = len(cards()), sum('```mermaid' in p.read_text(encoding='utf-8') for p in cards())
    sources = sum(p.is_dir() for p in (EXAMPLE / '40_Sources').iterdir())
    reviews = len((EXAMPLE / '_data/review/events-example.jsonl').read_text(encoding='utf-8').splitlines())
    words = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']
    assert len(by_domain) == 3
    expected = {
        'README.md': [f'The example vault has {n} cards in three domains', f'{diagrams} of them with a diagram',
                      f'from {sources} short notes', f'{words[reviews]} reviews were logged'],
        'README.zh-CN.md': [f'示例库有 {n} 张英文卡片，分属三个领域', f'其中 {diagrams} 张带图',
                            f'从 {sources} 份短笔记', f'记了 {reviews} 次复习'],
        'examples/README.md': [f'{n} cards in three domains: `learning` ({by_domain["learning"]}), '
                               f'`statistics` ({by_domain["statistics"]}), `computing` ({by_domain["computing"]}); '
                               f'{diagrams} cards have a diagram',
                               f'the {sources} source notes', f'a review log with {reviews} reviews'],
        'docs/agents.md': [f'{sources} sources, 34 sub-agents', f'{n} cards'],
    }
    for name, phrases in expected.items():
        text = (ROOT / name).read_text(encoding='utf-8')
        for phrase in phrases:
            assert phrase in text, (name, phrase)


def test_wiki_links_resolve():
    names = {p.relative_to(EXAMPLE).as_posix() for p in EXAMPLE.rglob('*.md')}
    stems = {p.stem for p in EXAMPLE.rglob('*.md')}
    for path in EXAMPLE.rglob('*.md'):
        for target in WIKI.findall(path.read_text(encoding='utf-8')):
            target = target.strip()
            ok = target in names or f'{target}.md' in names or target in stems
            assert ok, (path.relative_to(EXAMPLE).as_posix(), target)

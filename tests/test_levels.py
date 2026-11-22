"""Three levels (domain, pillar, card): a pillar without topics files its cards directly; `sb flatten` moves a map
there; the map shows how its pillars connect; a pillar is looked at again from 50 cards."""
import json
import shutil
from pathlib import Path

import pytest
import yaml

from secondbrain import cli, levels, topics
from secondbrain.vault import DELETE, parse_note, patch_frontmatter, read_snapshot

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples' / 'vault'


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv('SECONDBRAIN_STATE_DIR', str(tmp_path / 'state'))
    return shutil.copytree(EXAMPLE, tmp_path / 'v')


def run(capsys, root, *argv):
    code = cli.main(['--vault', str(root), *argv])
    out, err = capsys.readouterr()
    assert code == 0, err
    return json.loads(out)


def frontmatter(path: Path) -> dict:
    return parse_note(path.read_bytes())[0]


def test_the_rule_reads_an_implicit_topic_and_never_stores_it():
    assert levels.pillar_topics({'name': 'P'}) == ['P']
    assert levels.is_flat({'name': 'P'}) and levels.is_flat({'name': 'P', 'topics': ['P']})
    assert not levels.is_flat({'name': 'P', 'topics': ['P', 'Q']})
    tax = {'title': 'M', 'pillars': [{'name': 'P', 'order': 1, 'why': 'w'}, {'name': 'Q', 'topics': ['a']}]}
    normal = levels.normalise_taxonomy(tax)
    assert normal['pillars'][0]['topics'] == ['P'] and 'topics' not in tax['pillars'][0]
    assert levels.stored_taxonomy(normal) == tax
    assert levels.stored_topic('P', 'P') is None and levels.stored_topic('P', 'a') == 'a'
    pillars = {p['name']: p for p in normal['pillars']}
    assert levels.card_topic({'pillar': 'P'}, pillars) == 'P'
    assert levels.card_topic({'pillar': 'Q'}, pillars) is None  # a pillar with topics of its own: still missing


def test_patch_frontmatter_deletes_a_key_and_keeps_every_other_byte():
    raw = b'---\nid: a\npillar: P\ntopic: T\ntags:\n- x\n---\nbody\n'
    out = patch_frontmatter(raw, {'topic': DELETE})
    assert out == b'---\nid: a\npillar: P\ntags:\n- x\n---\nbody\n'
    assert patch_frontmatter(out, {'topic': DELETE}) == out  # absent: nothing happens


def test_flatten_plans_then_writes_three_levels_and_rolls_back(vault, capsys):
    tax_rel = '20_MOCs/_pillars/How We Learn.yaml'
    run(capsys, vault, 'refresh', '--apply')  # the example's pages as this version writes them
    before = {p: p.read_bytes() for p in vault.rglob('*') if p.is_file()}
    planned = run(capsys, vault, 'flatten', '--domain', 'learning')
    assert planned['status'] == 'planned' and planned['cards_changed'] > 0
    assert {p: p.read_bytes() for p in vault.rglob('*') if p.is_file()} == before  # a plan writes nothing

    applied = run(capsys, vault, 'flatten', '--domain', 'learning', '--apply')
    assert applied['status'] == 'applied'
    tax = yaml.safe_load((vault / tax_rel).read_text(encoding='utf-8'))
    assert all('topics' not in p for p in tax['pillars'])
    learning = [p for p in (vault / '10_Knowledge' / 'learning').glob('*.md')]
    assert learning and all('topic' not in frontmatter(p) for p in learning)
    for p in learning:  # only the topic line went: the body and every other field stay
        old_fm, old_body = parse_note(before[p])
        new_fm, new_body = parse_note(p.read_bytes())
        assert new_body == old_body and {k: v for k, v in old_fm.items() if k != 'topic'} == new_fm
        assert applied['topics_before'][new_fm['id']] == old_fm['topic']
    other = vault / '10_Knowledge' / 'statistics'
    assert all(p.read_bytes() == before[p] for p in other.glob('*.md'))  # another map is not touched
    registry = json.loads((vault / '_data' / 'notes_registry.json').read_text(encoding='utf-8'))
    ids = {frontmatter(p)['id'] for p in learning}
    assert all('topic' not in n for n in registry['notes'] if n['id'] in ids)

    status = run(capsys, vault, 'status')
    assert status['issues'] == [] and status['missing_classification'] == 0
    assert run(capsys, vault, 'refresh')['changed_files'] == 0  # the maps it wrote are the maps of the new taxonomy
    snapshot = read_snapshot(vault)
    assert all(c.fm['topic'] == c.fm['pillar'] for c in snapshot.cards.values() if c.domain == 'learning')
    page = (vault / '20_MOCs' / 'How We Learn' / '02Studystrategies--01.md').read_text(encoding='utf-8')
    assert '\n## Spacing' not in page and '[[10_Knowledge/learning/' in page  # cards stand under the pillar

    run(capsys, vault, 'recover', applied['job_id'], '--rollback')
    assert {p: p.read_bytes() for p in vault.rglob('*') if p.is_file() and p in before} == before


def test_flatten_removes_a_pillar_page_the_new_map_no_longer_has(vault, capsys):
    folder = vault / '20_MOCs' / 'How We Learn'
    stale = folder / '01Memory--02.md'
    stale.write_bytes((folder / '01Memory--01.md').read_bytes())  # as a long topic's second page would be
    custom = folder / 'notes.md'
    custom.write_text('# my own page\n', encoding='utf-8')
    applied = run(capsys, vault, 'flatten', '--domain', 'learning', '--apply')
    assert applied['pages_removed'] == ['20_MOCs/How We Learn/01Memory--02.md']
    assert not stale.exists() and custom.exists()  # a page the program did not write stays


def test_the_root_page_shows_how_pillars_connect_across_maps(vault, capsys):
    run(capsys, vault, 'refresh', '--apply')
    root = (vault / '20_MOCs' / 'Computing Foundations.md').read_text(encoding='utf-8')
    section = root.split('## How the pillars connect', 1)[1].split('\n## ', 1)[0]
    assert 'Reasoning with Data·Probability' in section  # a pillar of another map
    assert '→' in section and '↔' in section
    page = (vault / '20_MOCs' / 'Computing Foundations' / '02Datastructures--01.md').read_text(encoding='utf-8')
    assert 'Connected pillars: ' in page


def test_a_flat_pillar_is_looked_at_again_from_50_cards_not_at_40(vault):
    snapshot = read_snapshot(vault)
    pillar = next(iter(snapshot.taxonomies['How We Learn']['pillars']))['name']
    cards = [c for c in snapshot.cards.values() if c.domain == 'learning']
    flat_tax = levels.normalise_taxonomy({**snapshot.taxonomies['How We Learn'], 'pillars': [
        {k: v for k, v in p.items() if k != 'topics'} for p in snapshot.taxonomies['How We Learn']['pillars']]})

    def with_cards(n):
        from dataclasses import replace
        many = {}
        for i in range(n):
            base = cards[i % len(cards)]
            fm = {**base.fm, 'id': f'kc_x{i}', 'pillar': pillar, 'topic': pillar, 'relations': []}
            many[f'kc_x{i}'] = replace(base, id=f'kc_x{i}', fm=fm)
        return replace(snapshot, cards=many, taxonomies={**snapshot.taxonomies, 'How We Learn': flat_tax})
    assert topics.hints(with_cards(49))['oversize'] == []
    big = topics.hints(with_cards(50))['oversize']
    assert [(h['pillar'], h['level'], h['cards'], h['limit']) for h in big] == [(pillar, 'pillar', 50, 50)]
    assert 'groups' in big[0]['proposal'] or 'unavailable' in big[0]['proposal']


def test_ingest_refuses_a_new_topic_under_a_pillar_without_topics(tmp_path):
    import copy as copy_module
    from secondbrain.pipelines import ingest
    from tests import test_owner_items as t
    root = t.build_ingest_vault(tmp_path / 'vault')
    candidate = copy_module.deepcopy(t.first_candidate())
    pillar = candidate['new_topics'][0]['pillar']
    for path in (root / '20_MOCs' / '_pillars').glob('*.yaml'):
        tax = yaml.safe_load(path.read_text(encoding='utf-8'))
        for p in tax['pillars']:
            if p['name'] == pillar:
                p.pop('topics', None)
        path.write_text(yaml.safe_dump(tax, allow_unicode=True, sort_keys=False), encoding='utf-8')
    with pytest.raises(ingest.IngestError, match='files its cards directly'):
        t.check(root, candidate)

"""`sb repillar` files cards under another pillar of their domain (by hand, by a plan file, or as a reviewed
`sb advise` split advised); `sb domcreate` takes out the pillars its moves leave empty; a gateway prefix does not
decide a model's family. The example vault's cards are used; the advice answers are invented."""
import copy
import json
import shutil
from pathlib import Path

import pytest
import yaml

from secondbrain import advise, cli, config, domcreate, gates, jobs, topics
from secondbrain.executors.fake import FakeExecutor
from secondbrain.runner import run_job
from secondbrain.vault import parse_note, read_snapshot

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples' / 'vault'
TAX = '20_MOCs/_pillars/How We Learn.yaml'
MAP = '20_MOCs/How We Learn'


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv('SECONDBRAIN_STATE_DIR', str(tmp_path / 'state'))
    return shutil.copytree(EXAMPLE, tmp_path / 'v')


@pytest.fixture
def flat(vault, capsys):
    """The example with the learning map at three levels and its pages as this version writes them."""
    run(capsys, vault, 'refresh', '--apply')
    run(capsys, vault, 'flatten', '--domain', 'learning', '--apply')
    return vault


def run(capsys, root, *argv, code=0):
    got = cli.main(['--vault', str(root), *argv])
    out, err = capsys.readouterr()
    assert got == code, err
    return json.loads(out) if code == 0 else json.loads(err)


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def taxonomy(root):
    return yaml.safe_load((root / TAX).read_text(encoding='utf-8'))


def ids_of(root, pillar):
    return sorted(c.id for c in read_snapshot(root).cards.values() if c.fm.get('pillar') == pillar)


def changed_lines(before: bytes, after: bytes) -> set:
    """The lines only one side has (a line diff without order)."""
    a, b = before.decode('utf-8').splitlines(), after.decode('utf-8').splitlines()
    return set(a) ^ set(b)


def test_cards_move_by_hand_and_only_their_pillar_line_changes(flat, capsys):
    moving = ids_of(flat, 'Study strategies')[:2]
    before = files(flat)
    planned = run(capsys, flat, 'repillar', '--ids', *moving, '--to', 'Memory')
    assert planned['status'] == 'planned' and planned['cards_changed'] == 2
    assert files(flat) == before  # a plan writes nothing

    applied = run(capsys, flat, 'repillar', '--ids', *moving, '--to', 'Memory', '--apply')
    assert applied['status'] == 'applied' and applied['pillars_added'] == [] and applied['pillars_removed'] == []
    after = files(flat)
    snapshot = read_snapshot(flat)
    for cid in moving:
        rel = snapshot.cards[cid].path
        assert changed_lines(before[rel], after[rel]) == {'pillar: Study strategies', 'pillar: Memory'}
        assert len(before[rel]) - len(after[rel]) == len('Study strategies') - len('Memory')
    registry = json.loads(after['_data/notes_registry.json'])
    assert {n['id']: n['pillar'] for n in registry['notes'] if n['id'] in moving} == dict.fromkeys(moving, 'Memory')
    untouched = [rel for rel in before if rel.startswith('10_Knowledge/') and rel not in
                 {snapshot.cards[c].path for c in moving}]
    assert all(before[rel] == after[rel] for rel in untouched)  # no other card changes

    status = run(capsys, flat, 'status')
    assert status['issues'] == [] and status['missing_classification'] == 0
    assert run(capsys, flat, 'refresh')['changed_files'] == 0  # the maps it wrote are the maps of the new filing

    run(capsys, flat, 'recover', applied['job_id'], '--rollback')
    assert files(flat) == before
    again = run(capsys, flat, 'repillar', '--ids', *moving, '--to', 'Memory', '--apply')  # the same move, again
    assert again['status'] == 'applied' and again['job_id'] == applied['job_id'] + '-2'


def test_a_new_pillar_needs_its_why_and_stands_after_the_pillar_its_cards_came_from(flat, capsys):
    moving = ids_of(flat, 'Memory')[:1]
    refused = run(capsys, flat, 'repillar', '--ids', *moving, '--to', 'Forgetting', code=2)
    assert 'say what it answers' in refused['error']
    applied = run(capsys, flat, 'repillar', '--ids', *moving, '--to', 'Forgetting',
                  '--why', 'How fast memories fade.', '--apply')
    names = [(p['name'], p['order']) for p in taxonomy(flat)['pillars']]
    assert names == [('Memory', 1), ('Forgetting', 2), ('Study strategies', 3), ('Instruction', 4)]
    assert applied['pillars_renumbered'] == {'Study strategies': [2, 3], 'Instruction': [3, 4]}
    pages = {p.name for p in (flat / MAP).iterdir()}
    assert {'02Forgetting--01.md', '03Studystrategies--01.md'} <= pages and '02Studystrategies--01.md' not in pages
    assert all('topics' not in p for p in taxonomy(flat)['pillars'])  # the new pillar files its cards directly
    assert run(capsys, flat, 'refresh')['changed_files'] == 0


def test_a_pillar_the_move_empties_goes_with_its_page_and_an_empty_one_of_the_owner_stays(flat, capsys):
    moving = ids_of(flat, 'Memory')
    applied = run(capsys, flat, 'repillar', '--ids', *moving, '--to', 'Study strategies', '--apply')
    assert applied['pillars_removed'] == ['Memory']
    assert [p['name'] for p in taxonomy(flat)['pillars']] == ['Study strategies', 'Instruction']  # Instruction had none
    assert f'{MAP}/01Memory--01.md' in applied['pages_removed'] and not (flat / MAP / '01Memory--01.md').exists()
    status = run(capsys, flat, 'status')
    assert status['issues'] == [] and run(capsys, flat, 'refresh')['changed_files'] == 0
    run(capsys, flat, 'recover', applied['job_id'], '--rollback')
    assert (flat / MAP / '01Memory--01.md').exists() and taxonomy(flat)['pillars'][0]['name'] == 'Memory'


def test_a_page_the_owner_wrote_that_names_a_removed_page_stops_the_move(flat, capsys):
    (flat / 'notes.md').write_text('see [[20_MOCs/How We Learn/01Memory--01|memory]]\n', encoding='utf-8')
    before = files(flat)
    refused = run(capsys, flat, 'repillar', '--ids', *ids_of(flat, 'Memory'), '--to', 'Study strategies', code=3)
    assert 'notes.md' in refused['error'] and refused['conflict']
    assert files(flat) == before


def test_an_unknown_card_is_refused(flat, capsys):
    assert 'no card kc_nope' in run(capsys, flat, 'repillar', '--ids', 'kc_nope', '--to', 'Memory', code=2)['error']


def test_cards_of_two_domains_and_a_pillar_with_topics_are_refused(vault, capsys):
    snapshot = read_snapshot(vault)
    learning = next(c.id for c in snapshot.cards.values() if c.domain == 'learning')
    other = next(c.id for c in snapshot.cards.values() if c.domain == 'statistics')
    two = run(capsys, vault, 'repillar', '--ids', learning, other, '--to', 'Memory', code=2)
    assert '2 domains' in two['error']
    four = run(capsys, vault, 'repillar', '--ids', learning, '--to', 'Memory', code=2)
    assert 'sb flatten --domain learning first' in four['error']


def test_a_plan_file_moves_several_groups_in_one_transaction(flat, capsys, tmp_path):
    study = ids_of(flat, 'Study strategies')
    plan = {'pillars': [{'name': 'Spacing', 'why': 'When to review.', 'ids': study[:5]},
                        {'name': 'Practice', 'why': 'How to practise.', 'ids': study[5:]}]}
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan), encoding='utf-8')
    before = files(flat)
    applied = run(capsys, flat, 'repillar', '--plan', str(path), '--apply')
    assert applied['pillars_removed'] == ['Study strategies']
    assert [(p['name'], p['order']) for p in taxonomy(flat)['pillars']] == [
        ('Memory', 1), ('Spacing', 2), ('Practice', 3), ('Instruction', 4)]
    assert ids_of(flat, 'Spacing') == study[:5] and ids_of(flat, 'Practice') == study[5:]
    run(capsys, flat, 'recover', applied['job_id'], '--rollback')
    assert files(flat) == before


SPLIT = {'decision': 'split_pillars',
         'argument': 'The two groups answer different parts of the question and link only inside themselves.',
         'pillars': [{'name': 'Spacing and testing', 'why': 'When and how to practise recall.', 'groups': ['g1']},
                     {'name': 'Making sense', 'why': 'How new material is tied to what is known.', 'groups': ['g2']}]}


def _advice(root, decision='accept', answer=SPLIT):
    job_id = advise.prepare(root, domain='learning', pillar='Study strategies')['job_id']
    run_job(root, job_id, producer=FakeExecutor('gpt-test', responder=lambda task: copy.deepcopy(answer)),
            reviewer=FakeExecutor('claude-test', responder=lambda task: {'items': [
                {'id': 'advice', 'decision': decision, 'reason': 'the counts are the packet’s'}]}))
    return job_id


def test_a_reviewed_split_advice_is_carried_out(flat, capsys):
    job_id = _advice(flat)
    groups = {g['id']: sorted(c['id'] for c in g['cards']) for g in jobs.read_input(flat, job_id)['groups']}
    applied = run(capsys, flat, 'repillar', '--advice', job_id, '--apply')
    assert applied['from'] == f'advice {job_id}'
    assert ids_of(flat, 'Spacing and testing') == groups['g1'] and ids_of(flat, 'Making sense') == groups['g2']
    tax = {p['name']: p for p in taxonomy(flat)['pillars']}
    assert tax['Making sense']['why'] == 'How new material is tied to what is known.'
    scattered = applied['kept_in_place']
    assert ('Study strategies' in tax) == bool(scattered)  # a card no group took stays where it was
    assert run(capsys, flat, 'status')['issues'] == []


@pytest.mark.parametrize('decision, answer, problem', [
    ('revise', SPLIT, 'did not accept'),
    ('accept', {'decision': 'keep', 'argument': SPLIT['argument']}, 'not split_pillars'),
])
def test_an_advice_is_carried_out_only_as_a_reviewed_split(flat, capsys, decision, answer, problem):
    job_id = _advice(flat, decision, answer)
    before = files(flat)
    assert problem in run(capsys, flat, 'repillar', '--advice', job_id, code=2)['error']
    assert files(flat) == before


def test_an_advice_whose_pillar_changed_is_asked_again(flat, capsys):
    job_id = _advice(flat)
    moving = ids_of(flat, 'Study strategies')[:1]
    run(capsys, flat, 'repillar', '--ids', *moving, '--to', 'Memory', '--apply')
    refused = run(capsys, flat, 'repillar', '--advice', job_id, code=3)
    assert 'changed after the advice' in refused['error'] and 'sb advise' in refused['error']


def test_domcreate_takes_out_the_pillar_its_moves_empty(vault, capsys):
    memory = ids_of(vault, 'Memory')
    snapshot = read_snapshot(vault)
    run(capsys, vault, 'refresh', '--apply')
    before = files(vault)
    domcreate.prepare(vault, domain='memory', moc='Memory Map', question='What happens to what we learn?',
                      track='learning-science', evidence_ids=memory, job_id='dc',
                      reason='the memory cards answer a question of their own')
    proposal = {'domain': 'memory', 'moc': 'Memory Map', 'question': 'What happens to what we learn?',
                'track': 'learning-science',
                'taxonomy': {'title': 'Memory Map', 'narrative': 'how memories fade',
                             'pillars': [{'name': 'Forgetting', 'order': 1, 'why': 'how fast memories fade',
                                          'topics': ['Forgetting']}]},
                'migrations': [{'id': cid, 'pillar': 'Forgetting', 'topic': 'Forgetting'} for cid in memory],
                'relation_words': [], 'boundary_reason': 'memory only', 'difference_reason': 'not study methods',
                'impact_reason': f'{len(memory)} cards move'}
    state = run_job(vault, 'dc', producer=FakeExecutor('gpt-test', responder=lambda t: copy.deepcopy(proposal)),
                    reviewer=FakeExecutor('claude-test', responder=lambda t: {'items': [
                        {'domain': 'memory', 'decision': 'accept', 'reason': 'a specific reason'}]}))
    assert state['status'] == 'applied', state
    assert state['pillars_removed'] == {'How We Learn': ['Memory']}
    assert [p['name'] for p in taxonomy(vault)['pillars']] == ['Study strategies', 'Instruction']
    assert not (vault / MAP / '01Memory--01.md').exists()
    status = run(capsys, vault, 'status')
    assert status['issues'] == []
    assert not [e for e in topics.hints(read_snapshot(vault))['empty'] if e['pillar'] == 'Memory']
    assert all(c.domain == 'memory' for c in read_snapshot(vault).cards.values() if c.id in memory)
    assert snapshot.cards[memory[0]].domain == 'learning'
    run(capsys, vault, 'recover', 'dc', '--rollback')
    assert {k: v for k, v in files(vault).items() if k in before} == before


@pytest.mark.parametrize('model, family', [
    ('ollama-cloud/glm-5.3-flash', 'zhipu'),
    ('ollama/llama3', 'meta'),
    ('meta-llama/Llama-3.3-70B-Instruct', 'meta'),
    ('openrouter/anthropic/claude-opus-4', 'anthropic'),
    ('magpie/kimi-code/k3.1-pro', 'moonshot'),
    ('glm-4.6', 'zhipu'),
    ('ollama-cloud/some-new-model', None),
])
def test_a_gateway_prefix_does_not_decide_the_family(model, family):
    assert gates.model_family(model, config.DEFAULT_MODEL_FAMILIES) == family


def test_a_card_of_a_pillar_with_topics_loses_its_topic_line_in_a_new_pillar(vault, capsys):
    card = next(c for c in read_snapshot(vault).cards.values() if c.fm.get('pillar') == 'Memory')
    before = (vault / card.path).read_bytes()
    stored, _ = parse_note(before)
    run(capsys, vault, 'repillar', '--ids', card.id, '--to', 'Fading', '--why', 'How fast memories fade.', '--apply')
    after = (vault / card.path).read_bytes()
    assert changed_lines(before, after) == {'pillar: Memory', f'topic: {stored["topic"]}', 'pillar: Fading'}
    tax = {p['name']: p for p in taxonomy(vault)['pillars']}
    assert 'topics' not in tax['Fading'] and tax['Memory']['topics']  # the other pillars keep their topics
    assert run(capsys, vault, 'status')['issues'] == []

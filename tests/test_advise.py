"""`sb advise`: the frozen evidence of one pillar, the program's check of an advice, the reviewed job, and how
`sb status` shows the advice until the pillar's cards change."""
import copy
import json
import shutil
from pathlib import Path

import pytest

from secondbrain import advise, cli, jobs
from secondbrain.executors.fake import FakeExecutor
from secondbrain.runner import run_job
from secondbrain.schemas import AnswerError
from secondbrain.vault import read_snapshot

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples' / 'vault'
#: The example pillar whose cards fall into two groups of related cards.
DOMAIN, PILLAR = 'learning', 'Study strategies'


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv('SECONDBRAIN_STATE_DIR', str(tmp_path / 'state'))
    return shutil.copytree(EXAMPLE, tmp_path / 'v')


def packet(root):
    return advise.packet_of(read_snapshot(root), DOMAIN, PILLAR)


SPLIT = {'decision': 'split_pillars',
         'argument': 'The two groups answer different parts of the question and link only inside themselves.',
         'pillars': [{'name': 'Spacing and testing', 'why': 'When and how to practise recall.', 'groups': ['g1']},
                     {'name': 'Making sense', 'why': 'How new material is tied to what is known.', 'groups': ['g2']}]}


def writer(answer):
    return lambda task: copy.deepcopy(answer)


def reviewer(decision='accept'):
    return lambda task: {'items': [{'id': 'advice', 'decision': decision, 'reason': 'the counts are the packet’s'}]}


def test_the_evidence_holds_each_card_once_and_counts_each_link_once(vault):
    snapshot = read_snapshot(vault)
    e = advise.evidence(snapshot, DOMAIN, PILLAR)
    ids = [c['id'] for g in e['groups'] for c in g['cards']] + [c['id'] for c in e['scattered']]
    mine = sorted(cid for cid, c in snapshot.cards.items() if c.domain == DOMAIN and c.fm.get('pillar') == PILLAR)
    assert sorted(ids) == mine and len(ids) == len(set(ids)) == e['pillar']['cards']
    assert [g['size'] for g in e['groups']] == sorted((g['size'] for g in e['groups']), reverse=True)
    pairs = set()
    for cid in mine:
        for rel in snapshot.cards[cid].fm.get('relations') or []:
            if rel.get('target') in mine:
                pairs.add(tuple(sorted((cid, rel['target']))))
    assert sum(g['links']['inside'] + g['links']['other_groups'] for g in e['groups']) == len(pairs)
    assert e['domain']['pillars'] and all('why' in p for p in e['domain']['pillars'])
    assert all(len(c['essence']) <= advise.ESSENCE for g in e['groups'] for c in g['cards'])


def test_the_job_id_follows_the_evidence(vault):
    assert advise.prepare(vault, pillar=PILLAR)['job_id'] == advise.job_id_of(packet(vault))
    with pytest.raises(ValueError, match='no cards'):
        advise.prepare(vault, pillar='No such pillar')


def _pillar(i, **change):
    return [{**p, **change} if j == i else p for j, p in enumerate(SPLIT['pillars'])]


@pytest.mark.parametrize('answer, problem', [
    ({**SPLIT, 'pillars': [{**SPLIT['pillars'][0], 'groups': ['g1', 'g2']}]}, 'at least two pillars'),
    ({**SPLIT, 'pillars': _pillar(1, groups=[])}, 'every group in exactly one'),
    ({**SPLIT, 'pillars': _pillar(1, groups=['g1', 'g2'])}, 'every group in exactly one'),
    ({**SPLIT, 'pillars': _pillar(0, name='Memory')}, 'already another pillar'),
    ({**SPLIT, 'pillars': _pillar(1, name='Spacing and testing')}, 'own name'),
    ({**SPLIT, 'argument': 'short'}, 'argument must be'),
    ({'decision': 'keep', 'argument': SPLIT['argument'], 'pillars': SPLIT['pillars']}, 'keep takes no pillars'),
    ({'decision': 'new_domain', 'argument': SPLIT['argument'],
      'new_domain': {'groups': ['g2'], 'name': 'statistics', 'question': 'What is it?'}}, 'is a domain already'),
    ({'decision': 'new_domain', 'argument': SPLIT['argument'],
      'new_domain': {'groups': ['g9'], 'name': 'Sense making', 'question': 'How is new material tied in?'}},
     'new_domain.groups'),
    ({'decision': 'merge', 'argument': SPLIT['argument']}, 'decision must be one of'),
])
def test_the_program_refuses_an_advice_that_does_not_fit_the_evidence(vault, answer, problem):
    with pytest.raises(AnswerError, match=problem):
        advise.validate(packet(vault), answer)


def test_a_lawful_advice_of_each_kind_passes(vault):
    p = packet(vault)
    assert advise.validate(p, SPLIT) == SPLIT
    advise.validate(p, {'decision': 'keep', 'argument': SPLIT['argument']})
    advise.validate(p, {'decision': 'new_domain', 'argument': SPLIT['argument'],
                        'new_domain': {'groups': ['g2'], 'name': 'Sense making',
                                       'question': 'How is new material tied to what is known?'}})


def test_a_pillar_without_groups_can_only_be_kept(vault):
    p = advise.packet_of(read_snapshot(vault), 'statistics', 'Probability')
    assert p['groups'] == []
    with pytest.raises(AnswerError, match='only keep fits'):
        advise.validate(p, {'decision': 'split_pillars', 'argument': SPLIT['argument'], 'pillars': []})
    advise.validate(p, {'decision': 'keep', 'argument': SPLIT['argument']})


def test_a_reviewed_advice_is_kept_and_found_again_until_the_cards_change(vault):
    job_id = advise.prepare(vault, pillar=PILLAR)['job_id']
    before = {p.relative_to(vault): p.read_bytes() for p in vault.rglob('*') if p.is_file()}
    state = run_job(vault, job_id, producer=FakeExecutor('gpt-test', responder=writer(SPLIT)),
                    reviewer=FakeExecutor('claude-test', responder=reviewer()))
    assert state['status'] == 'applied' and state['applied_paths'] == []
    assert state['advice']['decision'] == 'split_pillars'
    assert [(p['name'], p['cards']) for p in state['advice']['pillars']] == [('Spacing and testing', 6),
                                                                              ('Making sense', 5)]
    assert state['advice']['review']['decision'] == 'accept'
    assert {p.relative_to(vault): p.read_bytes() for p in vault.rglob('*') if p.is_file()} == before  # no split
    found = advise.lookup(vault, read_snapshot(vault), DOMAIN, PILLAR)
    assert found['advice']['job'] == job_id and found['advice']['decision'] == 'split_pillars'
    # a card of the pillar loses a relation: the evidence changed, the advice is asked for again
    card = next(p for p in (vault / '10_Knowledge').rglob('*.md')
                if f'pillar: {PILLAR}' in p.read_text(encoding='utf-8') and 'target:' in p.read_text(encoding='utf-8'))
    text = card.read_text(encoding='utf-8').splitlines(keepends=True)
    i = next(i for i, line in enumerate(text) if line.lstrip().startswith('- type:'))
    del text[i:i + 2]
    card.write_text(''.join(text), encoding='utf-8')
    again = advise.lookup(vault, read_snapshot(vault), DOMAIN, PILLAR)
    assert again == {'advise': f'sb advise --domain {DOMAIN} --pillar {PILLAR} --executor api'}


def test_an_advice_the_last_review_still_revises_is_kept_with_that_review(vault):
    job_id = advise.prepare(vault, pillar=PILLAR)['job_id']
    state = run_job(vault, job_id, producer=FakeExecutor('gpt-test', responder=writer(SPLIT)),
                    reviewer=FakeExecutor('claude-test', responder=reviewer('revise')))
    assert state['status'] == 'applied'
    assert state['advice']['review']['decision'] == 'revise'


def test_the_command_freezes_the_evidence_without_a_model(vault, capsys):
    code = cli.main(['--vault', str(vault), 'advise', '--pillar', PILLAR, '--prepare-only'])
    out, err = capsys.readouterr()
    assert code == 0, err
    result = json.loads(out)
    assert result['status'] == 'prepared' and result['evidence']['pillar']['name'] == PILLAR
    assert jobs.load(vault, result['job_id'])['kind'] == 'advice'


def test_without_networkx_the_command_names_the_extra(vault, monkeypatch):
    from secondbrain import topics

    def missing(*a, **k):
        raise ImportError('networkx')
    monkeypatch.setattr(topics, 'partition', missing)
    with pytest.raises(ValueError, match='curate'):
        advise.prepare(vault, pillar=PILLAR)
    assert advise.lookup(vault, read_snapshot(vault), DOMAIN, PILLAR) == {}

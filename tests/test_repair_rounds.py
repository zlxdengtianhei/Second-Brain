"""Content repair rounds: each role has its budget in the role table, and one function says how many are left.

The fifth verdict found the repair rounds (a slot sent back with a reason and answered again) counted in five places,
each with its own comparison: a chunk, the merge check's rounds after a dispute, a split slot, an export unit and a
maintenance job. Now each ingest role carries its budget (``AnswerRole.repairs``), and every place asks one function,
``budget.rounds_left``. The table test takes every role from ``ingest.ANSWER_ROLES``: a role added without an entry
fails it. The second test breaks that one function (no round left, ever): each place must stop after its first round,
so a place that counted on its own would show here.

The source is the invented two-chunk source of tests/test_invariants.py; the export vault is tests/test_export.py's.
"""
from __future__ import annotations

from collections import Counter

import pytest

from secondbrain import budget, config, runner
from secondbrain.config import ReviewPolicy
from secondbrain.executors import FakeExecutor
from secondbrain.export import pipeline as export
from secondbrain.pipelines import ingest, split
from tests.helpers import EXISTING
from tests.ingest_helpers import keep_all
from tests.test_classification import ready
from tests.test_export import DOMAIN, FakeMathJax, vault_root, writer  # noqa: F401 - the fixture
from tests.test_patch_set import job, producer, reviewer, run

ROUNDS = ReviewPolicy().max_repair_rounds

#: role name -> its budget of content repair rounds (``max_repair_rounds``, or none: it is never sent back to answer
#: again with a reason; a dispute's judgement again is itself one of the merge check's rounds)
REPAIRS = {'chunk producer': ROUNDS, 'chunk reviewer': ROUNDS, 'merge check': ROUNDS, 'split producer': ROUNDS,
           'split reviewer': ROUNDS, 'dispute re-judge': 0, 'context note': 0, 'diagram': 0, 'note check': 0}


def test_every_role_has_its_repair_budget_in_the_role_table():
    assert sorted(REPAIRS) == sorted(r.name for r in ingest.ANSWER_ROLES)
    for spec in ingest.ANSWER_ROLES:
        assert spec.repairs(ReviewPolicy()) == REPAIRS[spec.name], spec.name


# --- each place, with the one function as it is and broken ------------------------------------------------------------
def chunk_rounds(tmp_path):
    """The chunk reviewer sends every card of c01 back, every round: the producer answers c01 again each round."""
    root, job_id, _ = job(tmp_path)
    asked, inner = [], producer()

    def produce(task):
        if ingest._base(task.chunk) == 'c01' and not ingest._aux_slot(task.chunk):
            asked.append(task.chunk)
        return inner(task)
    review_all = reviewer()

    def review(task):
        if ingest._base(task.chunk) == 'c01' and not ingest._aux_slot(task.chunk):
            unfixed = {'fixed': [{'finding': 'not yet', 'fixed': False}]} if '.r' in task.chunk else {}
            return {'cards': [{'slug': c['slug'], 'decision': 'revise', 'reason': 'not yet', **unfixed}
                              for c in task.packet['candidate']['cards']],
                    'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}
        return review_all(task)
    run(root, job_id, review, produce)
    return asked


def merge_rounds(tmp_path):
    """The merge check disputes lantern-queue every round, and its judgement again keeps it in the patch set."""
    root, job_id, _ = job(tmp_path)
    asked = []

    def check(packet):
        held = {c['slug'] for g in packet.get('patch_set') or [] for c in g['from']}
        return keep_all(packet, _disputes=tuple(s for s in ('lantern-queue',) if s in held))
    inner = reviewer(check=check, rejudge=lambda p: {'slug': 'lantern-queue', 'overlap': 'extends',
                                                     'with': [EXISTING], 'reason': 'again'})

    def review(task):
        if task.chunk.startswith('xc'):
            asked.append(task.chunk)
        return inner(task)
    run(root, job_id, review)
    return asked


def split_rounds(tmp_path):
    root, job_id, _ = job(tmp_path, n_props=11)
    seen = []
    run(root, job_id, reviewer(split_review=lambda p: {'decision': 'revise', 'restates': True, 'reason': 'P2 again'}),
        producer(seen=seen))
    return [t.chunk for t in seen if split.is_slot(t.chunk.split('.')[0])]


def export_rounds(tmp_path, root):
    """The reviewer sends a paragraph of unit u01 back every round: its writer answers again each round."""
    config.set_review_policy(ReviewPolicy())
    job_id = export.prepare(root, domain=DOMAIN, mode='parallel')['job_id']
    asked, inner = [], writer()

    def produce(task):
        if task.chunk == 'u01':
            asked.append(task.step)
        return inner(task)

    def review(task):
        return {'items': [{'id': p['id'], 'decision': 'revise' if task.chunk == 'u01' else 'accept',
                           'reason': 'not in the card' if task.chunk == 'u01' else 'faithful'}
                          for p in task.packet['paragraphs']]}
    export.advance(root, job_id, producer=FakeExecutor('gpt-writer', responder=produce),
                   reviewer=FakeExecutor('claude-reviewer', responder=review), checker=FakeMathJax(), workers=1)
    return asked


def maintenance_rounds(tmp_path):
    """A classification job whose reviewer rejects every row, every time: the rows are classified again each round
    (in a repair job of their own)."""
    config.set_review_policy(ReviewPolicy())
    root, _, row, _ = ready(tmp_path)
    asked = []

    def produce(task):
        asked.append(task.step)
        packet = task.packet.get('input', task.packet)
        return {'items': [{**row, 'id': c['id'], 'input_sha256': c['sha256']} for c in packet['cards']]}

    def review(task):
        return {'items': [{'id': i['id'], 'decision': 'revise', 'reason': 'needs the passage'}
                          for i in task.packet['candidate']['items']]}
    runner.run_job(root, 'classify', producer=FakeExecutor('gpt-test', responder=produce),
                   reviewer=FakeExecutor('claude-test', responder=review), apply=False)
    return asked


PLACES = {'chunk': chunk_rounds, 'merge check': merge_rounds, 'split': split_rounds,
          'maintenance': maintenance_rounds, 'export': None}


@pytest.mark.parametrize('broken', [False, True], ids=['as-it-is', 'no-round-left'])
@pytest.mark.parametrize('place', sorted(PLACES))
def test_each_place_repairs_while_the_one_function_says_rounds_are_left(tmp_path, request, monkeypatch, place,
                                                                         broken):
    root = request.getfixturevalue('vault_root') if place == 'export' else None
    if broken:
        monkeypatch.setattr(budget, 'rounds_left', lambda used, allowed: 0)
    asked = export_rounds(tmp_path, root) if place == 'export' else PLACES[place](tmp_path / place.replace(' ', '_'))
    assert len(asked) == (1 if broken else 1 + ROUNDS), asked
    if place in ('chunk', 'split', 'merge check'):
        assert Counter('.r' in a for a in asked)[True] == (0 if broken else ROUNDS)

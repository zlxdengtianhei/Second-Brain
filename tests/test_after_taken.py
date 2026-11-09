"""An answer the program fails to read after it was taken goes through the same gate as one it fails to check.

A deep reading of the one entry point found that what reads an answer once it is kept (a chunk's outcome, the packet
of its review) did not go through the gate: an answer a check let through but the program could not read stopped
every later ``advance`` with the error, and was never counted. Now the answer is set aside: the slot no longer holds
it, its run is failed (a host cannot submit to it again), the fault is listed for the owner with its stage, and the
answer is counted against the slot's budget, so the step is asked again until the budget is spent and the slot is
then settled as any other. The checks here are made lenient on purpose, to let such answers in; the cards and
answers are invented.
"""
from __future__ import annotations

import copy

import pytest

from secondbrain import cli, jobs
from secondbrain.config import ReviewPolicy
from secondbrain.executors import FakeExecutor
from secondbrain.executors.host import HostExecutor
from secondbrain.pipelines import ingest
from secondbrain.service import Service
from secondbrain.store import Conflict
from tests.ingest_helpers import first_candidate
from tests.test_answer_repair import lantern_job


def lenient(monkeypatch, role):
    """The check and the record of ``role``'s answers take any object."""
    real, record = ingest.validate_submission, ingest.record_decisions

    def check(packet, payload, **kw):
        return payload if kw['role'] == role else real(packet, payload, **kw)

    def recorder(packet, payload, **kw):
        return None if kw['role'] == role else record(packet, payload, **kw)
    monkeypatch.setattr(ingest, 'validate_submission', check)
    monkeypatch.setattr(ingest, 'record_decisions', recorder)


def limit(role):
    return ingest.answer_role('c01', role).retries(ReviewPolicy()) + 1


def faults(root, job_id):
    """Each fault kept, and the owner's list of them (one item per slot, role and step)."""
    rows = [(r['stage'], r['type']) for r in jobs.load(root, job_id).get('program_errors') or []]
    items = [i for i in Service(root).owner_items() if i.get('kind') == 'program_error']
    return rows, items


def test_a_host_candidate_its_review_packet_cannot_read_is_counted_until_the_slot_is_settled(tmp_path, monkeypatch):
    lenient(monkeypatch, 'producer')
    root, job_id, _ = lantern_job(tmp_path)
    host = HostExecutor()
    path = tmp_path / 'answer.json'
    path.write_text('{"cards": "not a list", "new_topics": [], "coverage": []}', encoding='utf-8')
    runs = []
    for _ in range(limit('producer') + 1):
        out = ingest.advance(root, job_id, producer=host, reviewer=host)  # never raises
        handed = [h for h in out.get('handoffs') or [] if h['role'] == 'producer']
        if not handed:
            break
        runs.append(handed[0]['run_id'])
        assert cli.main(['--vault', str(root), 'job', 'submit', job_id, '--role', 'producer', '--run-id',
                         runs[-1], '--file', str(path), '--model', 'gpt-test']) == 0  # the lenient check takes it
    info = jobs.load(root, job_id)['chunks']['c01']
    assert len(runs) == limit('producer') and ingest._refusals(info, ingest.answer_role('c01', 'producer')) == len(runs)
    assert info.get('candidate') is None and info.get('given_up')
    rows, [item] = faults(root, job_id)
    assert rows == [('review_packet', 'AttributeError')] * len(runs) and item['stage'] == 'review_packet'
    assert all(jobs.load_run(root, job_id, r)['status'] == 'failed' for r in runs)
    with pytest.raises(Conflict, match='TERMINAL_RUN'):
        Service(root).submit(job_id, role='producer', run_id=runs[0], raw=path.read_bytes())
    # the only chunk is settled as one whose producer kept failing: the job ends as it then always did
    assert out['status'] == 'failed' and any(p.get('chunk') == 'c01' for p in out['error']['pending'])


def test_a_review_the_chunks_outcome_cannot_read_is_counted_until_the_cards_wait_for_the_owner(tmp_path, monkeypatch):
    lenient(monkeypatch, 'reviewer')
    root, job_id, _ = lantern_job(tmp_path)
    reviews = []

    def review(task):
        reviews.append(task.run_id)
        return {'cards': [], 'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'covered'}}
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=lambda t: first_candidate()),
                          reviewer=FakeExecutor('claude-test', responder=review))  # never raises
    info = jobs.load(root, job_id)['chunks']['c01']
    assert len(reviews) == limit('reviewer') and info.get('review') is None
    assert ingest._refusals(info, ingest.answer_role('c01', 'reviewer')) == limit('reviewer')
    rows, [item] = faults(root, job_id)
    assert rows == [('outcome', 'KeyError')] * limit('reviewer') and item['stage'] == 'outcome'
    # the cards of the chunk wait for the owner, as when its reviews keep failing the checks
    slugs = {c['slug'] for c in first_candidate()['cards']}
    assert done['status'] == 'failed' and slugs <= {p.get('slug') for p in done['error']['pending']}

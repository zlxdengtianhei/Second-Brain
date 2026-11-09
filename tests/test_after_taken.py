"""An answer the program fails to read after it was taken goes through the same gate as one it fails to check.

A deep reading of the one entry point found that what reads an answer once it is kept (a chunk's outcome, the packet
of its review) did not go through the gate: an answer a check let through but the program could not read stopped
every later ``advance`` with the error, and was never counted. Now the answer is set aside: the slot no longer holds
it, its run is failed (a host cannot submit to it again), the fault is listed for the owner with its stage, and the
answer is counted against the slot's budget, and the step is asked again. When the next answer fails at the same
stage with the same error, the error is not the answer's: the job is parked instead of setting aside one innocent
answer after another, and the advance returns. The checks here are made lenient on purpose, to let such answers in;
the cards and answers are invented.
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


def test_a_host_candidate_its_review_packet_cannot_read_is_set_aside_and_the_same_error_again_parks_the_job(
        tmp_path, monkeypatch):
    """The first answer is set aside and counted, and asked for again; the same error again is not that answer's
    (it came back with the next one), so the job is parked for the owner instead of spending the slot's budget."""
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
    state = jobs.load(root, job_id)
    info = state['chunks']['c01']
    assert len(runs) == 2 and ingest._refusals(info, ingest.answer_role('c01', 'producer')) == 1
    assert jobs.load_run(root, job_id, runs[0])['status'] == 'failed'  # set aside
    assert jobs.load_run(root, job_id, runs[1])['status'] == 'submitted'  # kept: not the answer's fault
    with pytest.raises(Conflict, match='TERMINAL_RUN'):
        Service(root).submit(job_id, role='producer', run_id=runs[0], raw=path.read_bytes())
    rows, items = faults(root, job_id)
    assert rows == [('review_packet', 'AttributeError')] * 2
    assert {(i['slot'], i['role'], i['stage']) for i in items} == {('c01', 'producer', 'review_packet'),
                                                                  (None, None, 'review_packet')}
    assert state['parked']['stage'] == 'review_packet' and out.get('parked')
    again = ingest.advance(root, job_id, producer=host, reviewer=host)  # parked again, counted on the parking
    assert jobs.load(root, job_id)['parked']['times'] == 2 and not again.get('handoffs')


def test_a_review_the_chunks_outcome_cannot_read_is_set_aside_and_the_same_error_again_parks_the_job(
        tmp_path, monkeypatch):
    lenient(monkeypatch, 'reviewer')
    root, job_id, _ = lantern_job(tmp_path)
    reviews = []

    def review(task):
        reviews.append(task.run_id)
        return {'cards': [], 'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'covered'}}
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=lambda t: first_candidate()),
                          reviewer=FakeExecutor('claude-test', responder=review))  # never raises
    info = jobs.load(root, job_id)['chunks']['c01']
    assert len(reviews) == 2 and ingest._refusals(info, ingest.answer_role('c01', 'reviewer')) == 1
    rows, items = faults(root, job_id)
    assert rows == [('outcome', 'KeyError')] * 2
    assert {(i['slot'], i['role'], i['stage']) for i in items} == {('c01', 'reviewer', 'outcome'),
                                                                  (None, None, 'outcome')}
    assert done['parked']['stage'] == 'outcome' and done['status'] != 'failed'

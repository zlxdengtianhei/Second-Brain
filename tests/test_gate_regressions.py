"""Every independence check guards the ingest chain: tampering with run records or review
bytes after review makes `advance` refuse to write, and the vault stays untouched."""
from __future__ import annotations

import json

import pytest

from secondbrain import config, gates, jobs
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest
from secondbrain.store import Conflict, state_root
from tests.ingest_helpers import (DIMMING_IR, SOURCE_NAME, build_ingest_vault, first_candidate, first_review,
                                  repair_candidate, repair_review, scripted, tree)


def reviewed_job(root):
    produce, review, _ = scripted([first_candidate(), repair_candidate()], [first_review(), repair_review()],
                                  {'marker-dimming': DIMMING_IR})
    producer = FakeExecutor('gpt-test', responder=produce)
    reviewer = FakeExecutor('claude-test', responder=review)
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    state = ingest.advance(root, prepared['job_id'], producer=producer, reviewer=reviewer, apply=False)
    assert state['status'] == 'reviewed'
    return prepared['job_id'], producer, reviewer


def edit_run(root, job_id, run_id, **fields):
    path = state_root(root) / jobs.run_location(job_id, run_id)
    run = json.loads(path.read_text(encoding='utf-8'))
    run.update(fields)
    path.write_text(json.dumps(run), encoding='utf-8')


TAMPER = {
    'review run bound to another input': ({'packet_sha256': '0' * 64}, 'not bound to the frozen job input'),
    'review run bound to another candidate': ({'candidate_sha256': '1' * 64},
                                              'not bound to the current candidate version'),
    'review bytes not the reviewer output': ({'output_sha256': '2' * 64}, 'Review bytes are not the reviewer run'),
    'review run not submitted': ({'status': 'started'}, 'has not submitted a result'),
}


@pytest.mark.parametrize('name', sorted(TAMPER))
def test_advance_refuses_a_tampered_review_run(tmp_path, name):
    fields, message = TAMPER[name]
    root = build_ingest_vault(tmp_path / 'vault')
    job_id, producer, reviewer = reviewed_job(root)
    review_ref = jobs.load(root, job_id)['chunks']['c01']['review']
    before = tree(root)
    edit_run(root, job_id, review_ref['run_id'], **fields)
    with pytest.raises(Conflict, match=message):
        ingest.advance(root, job_id, producer=producer, reviewer=reviewer)
    assert tree(root) == before


def test_advance_refuses_a_tampered_producer_run(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    job_id, producer, reviewer = reviewed_job(root)
    candidate_ref = jobs.load(root, job_id)['chunks']['c01']['candidate']
    edit_run(root, job_id, candidate_ref['run_id'], output_sha256='3' * 64)
    with pytest.raises(Conflict, match='Candidate bytes are not the producer run output'):
        ingest.advance(root, job_id, producer=producer, reviewer=reviewer)


def test_advance_refuses_changed_review_bytes(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    job_id, producer, reviewer = reviewed_job(root)
    ref = jobs.load(root, job_id)['chunks']['c01']['review']
    out = [p for p in (state_root(root) / 'jobs' / job_id / 'out').iterdir() if ref['run_id'] in p.name]
    assert len(out) == 1
    out[0].write_text(out[0].read_text(encoding='utf-8').replace('faithful and atomic', 'looks fine'),
                      encoding='utf-8')
    with pytest.raises(Conflict):
        ingest.advance(root, job_id, producer=producer, reviewer=reviewer)


def test_api_mode_same_family_review_is_refused_at_the_gate(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    job_id, producer, reviewer = reviewed_job(root)
    ref = jobs.load(root, job_id)['chunks']['c01']['review']
    edit_run(root, job_id, ref['run_id'], model_reported='gpt-other', attestation='provider_response')
    with pytest.raises(ValueError, match='different model families'):
        ingest.advance(root, job_id, producer=producer, reviewer=reviewer)


def run(run_id, role, **extra):
    return {'run_id': run_id, 'role': role, 'status': 'submitted', 'packet_sha256': 'in', 'output_sha256': role,
            'model_reported': 'gpt-x' if role == 'producer' else 'claude-x', 'attestation': 'test', **extra}


def test_gate_unit_checks():
    policy = config.ReviewPolicy()
    cand = {'run_id': 'p', 'sha256': 'producer'}
    review = {'run_id': 'r', 'sha256': 'reviewer', 'candidate_sha256': 'producer'}
    producer, reviewer = run('p', 'producer'), run('r', 'reviewer', candidate_sha256='producer')
    assert gates.check_independence('in', cand, review, producer, reviewer, policy)['same_family'] is False
    with pytest.raises(Conflict, match='two distinct runs'):
        gates.check_independence('in', cand, {**review, 'run_id': 'p'}, producer,
                                 {**reviewer, 'run_id': 'p'}, policy)
    with pytest.raises(Conflict, match='roles'):
        gates.check_independence('in', cand, review, producer, {**reviewer, 'role': 'producer'}, policy)
    with pytest.raises(Conflict, match='frozen job input'):
        gates.check_independence('other', cand, review, producer, reviewer, policy)
    with pytest.raises(Conflict, match='current candidate version'):
        gates.check_independence('in', cand, {**review, 'candidate_sha256': 'x'}, producer, reviewer, policy)
    with pytest.raises(ValueError, match='different model families'):
        gates.check_independence('in', cand, review, producer, {**reviewer, 'model_reported': 'gpt-y'}, policy)
    with pytest.raises(ValueError, match='Unknown model family'):
        gates.check_independence('in', cand, review, producer, {**reviewer, 'model_reported': 'mystery'}, policy)
    host = {**reviewer, 'model_reported': 'gpt-y', 'attestation': 'self_reported'}
    assert gates.check_independence('in', cand, review, producer, host, policy)['same_family'] is True


def test_a_coverage_quote_must_be_verbatim(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    packet = jobs.read_input(root, prepared['job_id'])
    candidate = first_candidate()
    candidate['coverage'][-1]['source_quote'] = 'Thanks for reading, see you next month!'
    with pytest.raises(ingest.IngestError, match=r'coverage\[\d+\]\.source_quote: not a verbatim slice'):
        ingest.validate_candidate(packet, candidate, slot='c01', other_cards=[], kept_slugs=set(),
                                  existing_ids={'card_exist'}, existing_titles=set())

"""Deterministic independence gate between a candidate and its review.

The gate replaces session-provenance proofs with three light checks that hold
for every executor: both runs are bound to the same frozen input, the review is
bound to the exact candidate bytes, and the two runs are distinct (and, by
policy, from different model families). Per-item review coverage is checked
by each pipeline's own ``review_rows``.
"""
from __future__ import annotations

from .config import ReviewPolicy
from .store import Conflict

HOST_ATTESTATION = 'self_reported'


def model_family(model: str | None, families: dict) -> str | None:
    """Longest configured substring contained in ``model`` decides the family."""
    if not isinstance(model, str) or not model:
        return None
    name = model.lower()
    best = None
    for needle, family in families.items():
        if needle.lower() in name and (best is None or len(needle) > len(best[0])):
            best = (needle, family)
    return best[1] if best else None


def assess_families(producer_run: dict, reviewer_run: dict, policy: ReviewPolicy) -> dict:
    """Compare the two runs' model families; raise when policy forbids the pair."""
    producer_model = producer_run.get('model_reported') or producer_run.get('model_requested')
    reviewer_model = reviewer_run.get('model_reported') or reviewer_run.get('model_requested')
    a = model_family(producer_model, policy.model_families)
    b = model_family(reviewer_model, policy.model_families)
    host = HOST_ATTESTATION in {producer_run.get('attestation'), reviewer_run.get('attestation')}
    warnings = []
    same = a is not None and a == b
    if a is None or b is None:
        unknown = producer_model if a is None else reviewer_model
        if policy.require_different_family and not host:
            raise ValueError(f'Unknown model family for {unknown!r}; add it to [model_families]')
        warnings.append(f'unknown model family for {unknown!r}')
    if same and policy.require_different_family and not (host and policy.allow_same_family_in_host_mode):
        raise ValueError('Producer and reviewer must come from different model families '
                         f'({producer_model!r} and {reviewer_model!r} are both {a!r})')
    return {'producer_family': a, 'reviewer_family': b, 'same_family': same,
            'host_mode': host, 'warnings': warnings}


def check_independence(input_sha256: str, candidate_ref: dict, review_ref: dict,
                       producer_run: dict, reviewer_run: dict, policy: ReviewPolicy) -> dict:
    """Raise :class:`Conflict` / ``ValueError`` unless the pair may be applied."""
    if producer_run.get('role') != 'producer' or reviewer_run.get('role') != 'reviewer':
        raise Conflict('Run roles do not match candidate/review')
    if producer_run.get('run_id') == reviewer_run.get('run_id'):
        raise Conflict('Candidate and review must come from two distinct runs')
    for run in (producer_run, reviewer_run):
        if run.get('status') != 'submitted':
            raise Conflict(f"Run {run.get('run_id')} has not submitted a result")
        if run.get('packet_sha256') != input_sha256:
            raise Conflict(f"Run {run.get('run_id')} is not bound to the frozen job input")
    if candidate_ref.get('run_id') != producer_run.get('run_id') or \
            producer_run.get('output_sha256') != candidate_ref.get('sha256'):
        raise Conflict('Candidate bytes are not the producer run output')
    if review_ref.get('run_id') != reviewer_run.get('run_id') or \
            reviewer_run.get('output_sha256') != review_ref.get('sha256'):
        raise Conflict('Review bytes are not the reviewer run output')
    if review_ref.get('candidate_sha256') != candidate_ref.get('sha256') or \
            reviewer_run.get('candidate_sha256') != candidate_ref.get('sha256'):
        raise Conflict('The review is not bound to the current candidate version')
    return assess_families(producer_run, reviewer_run, policy)

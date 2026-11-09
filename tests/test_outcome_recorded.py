"""Every fate of a card that goes into another is in the decision record, and the job result reads it from there.

The fifth verdict found that a card of the patch set left waiting for the owner (a dispute not settled, a judgement
again whose answers kept failing the checks, a split card not done) had no entry of its own in the decision record:
the record and the job's ``outcomes`` said ``patch``, ``patch_set`` said ``pending``, and the card had gone into no
card at all (probe p5, its two "judged again in the wrong shape" scenarios, and P-6c). Now the program records each
fate (``outcome.program``: patched, split, written, covered or pending, with the reason), and ``outcomes``,
``patch_set``, ``merge_set`` and the pending list are all read back from the record, so they cannot differ.

The source is the invented two-chunk source of tests/test_invariants.py.
"""
from __future__ import annotations

import pytest

from secondbrain import decisions, overlap
from tests.helpers import EXISTING
from tests.ingest_helpers import keep_all
from tests.test_patch_set import job, reviewer, run

ONE_CHUNK = {'lantern-queue': {'overlap': 'extends', 'with': [EXISTING]},
             'lit-marker-cost': {'overlap': 'same', 'with': [EXISTING]},
             'arrival-order': {'overlap': 'same', 'with': [EXISTING]}}
ONLY_REPEAT = {**ONE_CHUNK, 'queue-order': {'overlap': 'same', 'with': [EXISTING]},
               'filler-note': {'overlap': 'same', 'with': [EXISTING]}}


def disputes_lantern(packet):
    held = {c['slug'] for g in packet.get('patch_set') or [] for c in g['from']}
    return keep_all(packet, _disputes=tuple(s for s in ('lantern-queue',) if s in held))


def broken(packet):  # the judgement again, in the wrong shape every time
    return {'slug': 'lantern-queue', 'overlap': 'sideways', 'with': [], 'reason': 'r'}


def agree(done, slug, outcome, how):
    """The result's outcomes, patch_set and pending list, and the decision record, all say the same of ``slug``."""
    record = done['decision_record']
    entry = decisions.current(record, 'outcome.program', slug)
    assert entry is not None, f'the decision record holds no fate for {slug}'
    assert entry['data']['outcome'] == outcome and entry['data']['how'] == how
    assert overlap.outcome(record, slug, checked=True)['outcome'] == outcome == done['outcomes'][slug]
    assert done['patch_set'][slug] == how
    waiting = [p for p in done['pending'] if p['slug'] == slug]
    assert (how == 'pending') == bool(waiting)
    if waiting:
        assert waiting[0]['reason'] == entry['data']['reason'] and waiting[0]['reason']
    for other, went in done['outcomes'].items():
        assert overlap.outcome(record, other, checked=True)['outcome'] == went
    return entry['data']


@pytest.mark.parametrize('levels', [ONE_CHUNK, ONLY_REPEAT], ids=['with-other-cards', 'only-the-patch-set-card'])
def test_a_card_whose_judgement_again_keeps_failing_is_pending_in_the_record_and_the_result(tmp_path, levels):
    root, job_id, _ = job(tmp_path)
    done = run(root, job_id, reviewer(check=disputes_lantern, rejudge=broken, levels=levels))
    assert done['status'] == 'partial'
    data = agree(done, 'lantern-queue', 'pending', 'pending')
    assert 'failed' in data['reason'] or 'validation' in data['reason']


def test_p6c_a_dispute_judged_again_in_the_wrong_shape_waits_for_the_owner_and_the_record_says_so(tmp_path):
    root, job_id, _ = job(tmp_path)
    done = run(root, job_id, reviewer(check=lambda p: keep_all(p, _disputes=('lantern-queue',)), rejudge=broken))
    assert done['status'] == 'partial'
    agree(done, 'lantern-queue', 'pending', 'pending')


def test_a_dispute_still_open_when_the_rounds_run_out_is_pending_in_the_record(tmp_path):
    root, job_id, _ = job(tmp_path)
    again = lambda p: {'slug': 'lantern-queue', 'overlap': 'extends', 'with': [EXISTING], 'reason': 'again'}  # noqa
    done = run(root, job_id, reviewer(check=disputes_lantern, rejudge=again))
    assert done['status'] == 'partial'
    agree(done, 'lantern-queue', 'pending', 'pending')


def test_a_patched_card_has_its_fate_in_the_record_too(tmp_path):
    root, job_id, _ = job(tmp_path)
    done = run(root, job_id, reviewer())
    assert done['status'] == 'applied'
    data = agree(done, 'lantern-queue', 'patch', 'patched')
    assert data['target'] == EXISTING

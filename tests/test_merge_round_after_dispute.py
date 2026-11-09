"""After a dispute, the merge check runs its next round even when the rest of the source alone would not need it.

The decision set lies in one chunk (queue-order and filler-note; the cards of the second chunk repeat existing
cards), so only the patch-set card lantern-queue makes the merge check needed. Round 0 merges filler-note into
queue-order (`same`) and disputes lantern-queue. Its judgement again takes it out of the patch set (judged `same`
as the existing card, `distinct`, or given up after answers that failed the checks). Before, the merge check then
counted as not needed: round 0 was neither applied nor replaced, and the write stopped with "the decision record says
merge, the write would write". Now the next round judges the cards that are left, so round 0's merge is judged again
and the write and the record agree. Synthetic cards only.
"""
from __future__ import annotations

import pytest

from secondbrain import overlap
from tests.helpers import EXISTING
from tests.ingest_helpers import keep_all
from tests.test_patch_set import job, reviewer, run

LEVELS = {'lantern-queue': {'overlap': 'extends', 'with': [EXISTING]},
          'lit-marker-cost': {'overlap': 'same', 'with': [EXISTING]},
          'arrival-order': {'overlap': 'same', 'with': [EXISTING]}}


def check(p):
    held = {c['slug'] for g in p.get('patch_set') or [] for c in g['from']}
    return keep_all(p, _disputes=tuple(s for s in ('lantern-queue',) if s in held),
                    **{'filler-note': {'overlap': 'same', 'with': ['queue-order'], 'reason': 'the same point'}})


@pytest.mark.parametrize('again, lantern', [('same', 'drop'), ('distinct', 'write'), ('broken', 'pending')])
def test_a_round_after_a_dispute_keeps_the_merge_of_round_0_and_the_record_agrees(tmp_path, again, lantern):
    root, job_id, _ = job(tmp_path)

    def rejudge(p):
        if again == 'broken':  # fails the checks every time: the card waits for the owner
            return {'slug': 'lantern-queue', 'overlap': 'sideways', 'with': [], 'reason': 'r'}
        return {'slug': 'lantern-queue', 'overlap': again, 'with': [EXISTING] if again == 'same' else [],
                'reason': 'judged again'}
    checks = []
    done = run(root, job_id, reviewer(checks, check=check, rejudge=rejudge, levels=LEVELS))
    assert len(checks) == 2 and checks[1].chunk == 'xc01.r1'  # the round after the dispute ran
    assert done['source_check']['status'] == 'checked'
    assert [m['slug'] for m in done['source_check']['merged']] == ['filler-note']  # round 0's merge, judged again
    assert done['outcomes']['filler-note'] == 'merge' and done['outcomes']['lantern-queue'] == lantern
    for slug, went in done['outcomes'].items():
        assert overlap.outcome(done['decision_record'], slug, checked=True)['outcome'] == went
    assert done['status'] == ('partial' if again == 'broken' else 'applied')

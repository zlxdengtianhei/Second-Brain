"""Every role's answers that fail the program's checks are counted in one place, against one budget.

The fourth verdict found two roles with no budget at all (R6c): an answer of the split reviewer or of the dispute
re-judge that failed the checks stopped the job, and every `sb job advance` asked again, without end; the item never
reached the owner. Now every role that asks a model goes through one entry (``jobs.refuse``, by ``ingest.record_refusal``) that counts
per slot in the job state, and the roles are one table (``ingest.ANSWER_ROLES``), the one an answer's schema is read
from. This test takes every role from that table: a role added later without a scenario here fails it.

Host mode is the same column again: a sub-agent that keeps submitting answers that fail the checks is refused at most
``max_invalid_retries`` + 1 times in a slot, the same count as an API run's (``ingest.record_refusal`` at the gate),
then its run is closed and the slot is settled as its role says.

For each role, an answer that always fails the checks is asked for at most ``max_invalid_retries`` + 1 times in one
``advance`` (a diagram once per slot: its step asks again in a rewrite slot of its own); then what it was for waits for the owner with the reason (``pending`` roles: the job ends ``partial``) or
the job goes on without it (``without`` roles: the merge check, a model note, a diagram), and the slot says why.
"""
from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import pytest

from secondbrain import config, jobs
from secondbrain.executors import FakeExecutor
from secondbrain.executors.base import ExecutorFailure
from secondbrain.executors.host import HostExecutor
from secondbrain.pipelines import ingest, split
from secondbrain.store import Conflict
from tests.ingest_helpers import keep_all, packet_in
from tests.test_invariants import LEVELS
from tests.test_patch_set import job, producer, reviewer

LIMIT = config.ReviewPolicy().max_invalid_retries + 1


def bad_answer(spec, slot):
    """An answer of the right kind with every required field in the wrong type: refused at the gate."""
    return {key: 12345 for key in spec.schema(slot).get('required', [])} or {'x': 1}


def disputes_lantern_queue(packet):
    """The merge check disputes lantern-queue while it is in the patch set."""
    held = {c['slug'] for g in packet.get('patch_set') or [] for c in g['from']}
    return keep_all(packet, _disputes=tuple(s for s in ('lantern-queue',) if s in held))


#: lantern-queue (a concept card, which gets a diagram) written as a card of its own
DISTINCT = {k: v for k, v in LEVELS.items() if k != 'lantern-queue'}

#: role name -> (job fields, chunk reviewer's levels, the merge check's answer, which of the role's slots answer badly)
SCENARIOS = {
    'chunk producer': ({}, LEVELS, None, lambda slot: ingest._base(slot) == 'c01'),
    'chunk reviewer': ({}, LEVELS, None, lambda slot: ingest._base(slot) == 'c01'),
    'merge check': ({}, LEVELS, None, None),
    'dispute re-judge': ({}, LEVELS, disputes_lantern_queue, None),
    'split producer': ({'n_props': 11}, LEVELS, None, None),
    'split reviewer': ({'n_props': 11}, LEVELS, None, None),
    'context note': ({'context_note': 'summary'}, LEVELS, None, None),
    'diagram': ({}, DISTINCT, None, None),
}


def test_every_role_that_asks_a_model_is_in_this_test():
    assert sorted(SCENARIOS) == sorted(r.name for r in ingest.ANSWER_ROLES)
    for slot, role in (('c01', 'producer'), ('c01.r1', 'reviewer'), ('xc01.r1', 'reviewer'), ('xr1-a-b', 'reviewer'),
                       ('sp01.r1', 'producer'), ('sp02', 'reviewer'), ('nt-concepts', 'producer'), ('dg01.r1', 'producer')):
        assert ingest.answer_role(slot, role) in ingest.ANSWER_ROLES  # one role per slot and executor role
    with pytest.raises(ingest.IngestError):
        ingest.answer_role('dg01', 'reviewer')


def broken(name, only, inner, asked):
    def respond(task):
        spec = ingest.answer_role(task.chunk, task.role)
        if spec.name == name and (only is None or only(task.chunk)):
            asked.append(task.chunk)
            return bad_answer(spec, task.chunk)
        return inner(task)
    return respond


@pytest.mark.parametrize('name', sorted(SCENARIOS))
def test_an_answer_that_keeps_failing_the_checks_is_asked_for_within_the_budget_then_given_up(tmp_path, name):
    spec = next(r for r in ingest.ANSWER_ROLES if r.name == name)
    fields, levels, check, only = SCENARIOS[name]
    fields = dict(fields)
    root, job_id, _ = job(tmp_path, n_props=fields.pop('n_props', 2), **fields)
    asked = []
    produce, review = producer(), reviewer(check=check, levels=levels)
    done = ingest.advance(root, job_id,
                          producer=FakeExecutor('gpt-test', responder=broken(name, only, produce, asked)),
                          reviewer=FakeExecutor('claude-test', responder=broken(name, only, review, asked)))
    per_slot, asks = Counter(asked), spec.retries(config.ReviewPolicy()) + 1
    assert asks <= LIMIT and per_slot and set(per_slot.values()) == {asks}, per_slot  # one advance: within budget
    state = jobs.load(root, job_id)
    for slot in per_slot:
        info = state['chunks'][slot]
        assert info[spec.count] == asks
        assert f'failed validation {asks} times' in info['given_up'] and 'must be' in info['given_up']
    if spec.used_up == 'pending':
        assert done['status'] == 'partial', done.get('error')
        reasons = [p.get('reason') or '' for p in done['pending']]
        assert any(f'failed validation {LIMIT} times' in r and 'must be' in r for r in reasons), reasons
    else:  # the job goes on without the answer, and the slot says why
        assert done['status'] == 'applied', done.get('pending')
        assert all(spec.skip in state['chunks'][slot]['skipped'] for slot in per_slot)
    again = []
    ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=broken(name, only, produce, again)),
                   reviewer=FakeExecutor('claude-test', responder=broken(name, only, review, again)))
    assert again == []


# --- host mode: the sub-agents submit through `sb job submit`, the same gate and the same count ----------------------
def host_drive(root, job_id, name, only, produce, review, refused):
    """Run the job in host mode as a coordinator would: hand each step to a sub-agent and submit its answer; the
    role ``name`` answers badly and submits again until its run is closed. Returns the job's last state."""
    host = HostExecutor()
    for _ in range(40):
        out = ingest.advance(root, job_id, producer=host, reviewer=host)
        if not out.get('handoffs'):
            return out
        for handoff in out['handoffs']:
            spec = ingest.answer_role(handoff['chunk'], handoff['role'])
            text = open(handoff['packet_path'], encoding='utf-8').read()
            task = SimpleNamespace(packet=packet_in(text), chunk=handoff['chunk'], role=handoff['role'], prompt=text,
                                   step=handoff['step'])
            model = 'gpt-test' if handoff['role'] == 'producer' else 'claude-test'
            if spec.name == name and (only is None or only(handoff['chunk'])):
                for _ in range(10):  # a sub-agent that keeps answering in the wrong shape
                    try:
                        jobs.submit(root, job_id, bad_answer(spec, handoff['chunk']), role=handoff['role'],
                                    run_id=handoff['run_id'], model_reported=model)
                    except Conflict as closed:  # the run is closed: its slot was given up
                        assert 'TERMINAL_RUN' in str(closed)
                        break
                    except ValueError:
                        refused.append(handoff['chunk'])
                continue
            answer = (produce if handoff['role'] == 'producer' else review)(task)
            try:  # another role's answer may be refused too (a chunk that links a card the broken one never wrote)
                jobs.submit(root, job_id, answer, role=handoff['role'], run_id=handoff['run_id'], model_reported=model)
            except ValueError:
                pass  # handed out again at the next advance, until its own budget is used up
    raise AssertionError('the job did not settle')


@pytest.mark.parametrize('name', sorted(SCENARIOS))
def test_in_host_mode_answers_that_keep_failing_are_refused_within_the_budget_then_the_slot_is_settled(tmp_path,
                                                                                                         name):
    spec = next(r for r in ingest.ANSWER_ROLES if r.name == name)
    fields, levels, check, only = SCENARIOS[name]
    fields = dict(fields)
    root, job_id, _ = job(tmp_path, n_props=fields.pop('n_props', 2), **fields)
    refused = []
    done = host_drive(root, job_id, name, only, producer(), reviewer(check=check, levels=levels), refused)
    per_slot, asks = Counter(refused), spec.retries(config.ReviewPolicy()) + 1
    assert per_slot and set(per_slot.values()) == {asks}, per_slot
    state = jobs.load(root, job_id)
    for slot in per_slot:
        info = state['chunks'][slot]
        assert info[spec.count] == asks and f'failed validation {asks} times' in info['given_up']
        runs = [jobs.load_run(root, job_id, r) for r in state['runs']]
        assert [r['status'] for r in runs if r.get('chunk') == slot and r['role'] == spec.role
                and r.get('rejected_submits')] == ['failed']  # closed once given up
    if spec.used_up == 'pending':
        assert done['status'] == 'partial', done.get('error')
        assert any(f'failed validation {asks} times' in (p.get('reason') or '') for p in done['pending'])
    else:
        assert done['status'] == 'applied', done.get('pending')
        assert all(spec.skip in state['chunks'][slot]['skipped'] for slot in per_slot)


# --- P-6c: the two roles that had no budget, driven as `sb job advance` would be, again and again ------------------
def drive(root, job_id, respond, times=8):
    ends, done = [], None
    for _ in range(times):
        try:
            done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=producer()),
                                  reviewer=FakeExecutor('claude-test', responder=respond))
            ends.append(done['status'])
            break
        except ExecutorFailure as exc:
            ends.append(exc.code)
    return ends, done


def test_p6c_a_split_review_without_matches_is_asked_three_times_then_the_split_card_waits_for_the_owner(tmp_path):
    root, job_id, _ = job(tmp_path, n_props=11)
    asked, inner = [], reviewer()

    def respond(task):
        if split.is_slot(task.chunk.split('.')[0]):
            asked.append(task.chunk)
            return {'decision': 'accept', 'restates': False, 'reason': 'faithful'}  # no matches: refused
        return inner(task)
    ends, done = drive(root, job_id, respond)
    assert ends == ['partial'] and len(asked) == LIMIT
    [pending] = [p for p in done['pending'] if p['slug'] == 'lantern-queue']
    assert f"the split reviewer's answer failed validation {LIMIT} times" in pending['reason']
    assert 'matches' in pending['reason']
    assert done['patch_set'] == {'lantern-queue': 'pending'}


def test_p6c_a_judgement_again_of_the_wrong_shape_is_asked_three_times_then_the_card_waits_with_the_dispute(tmp_path):
    root, job_id, _ = job(tmp_path)
    asked, inner = [], reviewer(check=disputes_lantern_queue)

    def respond(task):
        if task.chunk.startswith('xr'):
            asked.append(task.chunk)
            return {'slug': 'lantern-queue', 'overlap': 'sideways', 'with': [], 'reason': 'r'}
        return inner(task)
    ends, done = drive(root, job_id, respond)
    assert ends == ['partial'] and asked == ['xr1-lantern-queue'] * LIMIT
    [pending] = [p for p in done['pending'] if p['slug'] == 'lantern-queue']
    assert 'it answers another question' in pending['reason']  # the dispute
    assert f"the dispute re-judge's answer failed validation {LIMIT} times" in pending['reason']
    assert done['patch_set'] == {'lantern-queue': 'pending'}
    assert {p['slug'] for p in done['produced']} >= {'filler-note', 'lit-marker-cost', 'arrival-order'}  # the rest


def test_a_judgement_again_that_passes_on_the_second_ask_goes_on_as_before(tmp_path):
    root, job_id, _ = job(tmp_path)
    asked, inner = [], reviewer(check=disputes_lantern_queue)

    def respond(task):
        if task.chunk.startswith('xr'):
            asked.append(task)
            if len(asked) == 1:
                return {'slug': 'lantern-queue', 'overlap': 'sideways', 'with': [], 'reason': 'r'}
        return inner(task)
    ends, done = drive(root, job_id, respond)
    assert ends == ['applied'] and len(asked) == 2
    assert 'rejected by the validator' in asked[1].prompt
    assert all(p.get('slug') != 'lantern-queue' for p in done['pending'])

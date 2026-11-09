"""Every kind's ``advance`` reads the answers it took behind one guard.

Four reviews in a row found an error in handling an answer that went past the count, the last on a maintenance job
whose review was taken but could not be read by its apply. The entry point had become one; the layer after it was
still a ``try`` written at each place that read an answer, and the place no one wrote was the one that failed. Now
every kind's ``advance`` runs inside ``jobs.guarded``, and the code that reads taken answers carries a mark
(``jobs.reading``) that names the answer to blame, or none. An error that passes a mark is never raised: the answer
it names is set aside and counted (the step is asked again until the budget is spent), or, when no one answer is to
blame, the job is parked; either way the owner's list has it. A stop on purpose (``store.Halt``: a conflict, a
review whose models may not judge each other, a write that would lose what it carries, a checker that cannot run)
still stops the job, and is listed too.

The table is read from ``jobs.KINDS``: each kind that asks a model runs a whole flow here, once for every read of a
taken answer in it (``jobs.artifact``, ``jobs.require_independent_review``), with that one read failing. A kind
without a row, or a read of a taken answer outside the guard, fails here. The cards and answers are invented.
"""
from __future__ import annotations

import copy

import pytest

from secondbrain import config, jobs, runner
from secondbrain.config import ReviewPolicy
from secondbrain.executors import ExecutorFailure, FakeExecutor
from secondbrain.export import pipeline as export
from secondbrain.pipelines import ingest
from secondbrain.service import Service
from secondbrain.store import Conflict, Halt
from tests import test_export as te
from tests.ingest_helpers import diagram_answer, first_candidate, is_diagram_task, is_source_check_task, keep_all
from tests.test_answer_repair import accept_all, lantern_job
from tests.test_maintenance_reviews import JOBS as MAINTENANCE, covering


def ingest_flow(tmp_path, monkeypatch):
    root, job_id, _ = lantern_job(tmp_path)

    def produce(task):
        return diagram_answer(task.packet, None) if is_diagram_task(task) else copy.deepcopy(first_candidate())

    def review(task):
        return keep_all(task.packet) if is_source_check_task(task) else accept_all(task)
    return root, job_id, lambda: ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                                                reviewer=FakeExecutor('claude-test', responder=review))


def ingest_split_flow(tmp_path, monkeypatch):
    """Three chunks whose cards meet in the merge check, and a card split off an existing one (a split slot)."""
    from types import SimpleNamespace

    from secondbrain.pipelines import split
    from tests.ingest_helpers import SOURCE_NAME, build_ingest_vault, with_diagrams
    from tests.test_ingest import settings
    from tests.test_invariants import SOURCE, two_chunk_producer
    from tests.test_patch_set import SPLIT_CARD, reviewer, run, sb_existing
    from tests.test_split_repair import BOLD, PLAIN
    root = build_ingest_vault(tmp_path / 'vault')
    settings(chunk_chars=1000)
    (root / '00_Inbox' / SOURCE_NAME).write_text(SOURCE.replace(PLAIN, BOLD), encoding='utf-8')
    job_id = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)['job_id']
    sb_existing(root, 11)

    def produce(task):
        if split.is_slot(task.chunk.split('.')[0]):
            return {'card': copy.deepcopy(SPLIT_CARD)}
        plain = {**task.packet, 'chunk': {**task.packet['chunk'], 'text': task.packet['chunk']['text'].replace(
            BOLD, PLAIN)}}
        return two_chunk_producer(SimpleNamespace(packet=plain))
    return root, job_id, lambda: run(root, job_id, reviewer(), with_diagrams(produce))


def export_flow(tmp_path, monkeypatch):
    monkeypatch.setenv('SECONDBRAIN_NO_CACHE', '1')
    config.activate(config.Settings(card_language='zh'))
    root = te.build(tmp_path / 'vault')
    config.set_review_policy(ReviewPolicy(max_repair_rounds=1))
    job_id = export.prepare(root, domain=te.DOMAIN, mode='parallel')['job_id']
    return root, job_id, lambda: export.advance(
        root, job_id, producer=FakeExecutor('gpt-writer', responder=te.writer()),
        reviewer=FakeExecutor('claude-reviewer', responder=te.reviewer()), checker=te.FakeMathJax(), workers=1)


def maintenance_flow(kind):
    def flow(tmp_path, monkeypatch):
        root, job_id, produce, _ = MAINTENANCE[kind](tmp_path)
        review = FakeExecutor('claude-test', responder=lambda task: covering(kind, task.packet['candidate']))
        return root, job_id, lambda: runner.run_job(root, job_id, producer=produce, reviewer=review)
    return flow


FLOWS = {'ingest': ingest_flow, 'export': export_flow, **{k: maintenance_flow(k) for k in MAINTENANCE}}
#: More flows of a kind, for the stages one flow does not reach (a merge check, a split card).
MORE = {'ingest-split': ingest_split_flow}


def test_every_kind_that_asks_a_model_has_a_flow_here():
    assert {kind for kind, asks in jobs.KINDS.items() if asks} == set(FLOWS)
    assert {name.split('-')[0] for name in MORE} <= set(FLOWS)


class Reads:
    """Count the reads of taken answers; make the ``fail``-th one raise."""

    def __init__(self, monkeypatch, fail=None):
        self.n, self.fail, self.failed = 0, fail, False
        for name in ('artifact', 'require_independent_review'):
            real = getattr(jobs, name)
            monkeypatch.setattr(jobs, name, self.wrap(real))

    def wrap(self, real):
        def read(*args, **kwargs):
            self.n += 1
            if self.n == self.fail:
                self.failed = True
                raise RuntimeError(f'read {self.n} of a taken answer broke')
            return real(*args, **kwargs)
        return read


def run_flow(flow, tmp_path, monkeypatch, fail=None):
    root, job_id, advance = flow(tmp_path, monkeypatch)
    reads = Reads(monkeypatch, fail)
    for _ in range(4):  # a maintenance job raises each answer the entry point refuses; ask again as a caller would
        try:
            return root, job_id, advance(), reads
        except ExecutorFailure as failure:
            assert failure.error.get('counted'), failure.error  # the entry point's count, not this layer's
    raise AssertionError('the flow did not settle')


def reads_of(kind, tmp_path, monkeypatch):
    with monkeypatch.context() as m:
        _, _, out, reads = run_flow({**FLOWS, **MORE}[kind], tmp_path / 'plain', m)
    assert reads.n > 0 and out['status'] in {'applied', 'complete', 'published', 'partial'}, out['status']
    return reads.n


@pytest.mark.parametrize('kind', sorted({**FLOWS, **MORE}))
def test_a_failing_read_of_a_taken_answer_at_any_stage_is_counted_or_parked_and_listed(tmp_path, monkeypatch, kind):
    total = reads_of(kind, tmp_path, monkeypatch)
    for n in range(1, total + 1):
        with monkeypatch.context() as m:
            root, job_id, out, reads = run_flow({**FLOWS, **MORE}[kind], tmp_path / f'r{n}', m, fail=n)  # no raise
        assert reads.failed, n
        state = jobs.load(root, job_id)
        faults = [r for r in state.get('program_errors') or [] if r.get('type') == 'RuntimeError']
        assert faults, (n, state.get('status'))
        parked = (state.get('parked') or {}).get('type') == 'RuntimeError'
        aside = [r for r in faults if r.get('role')]
        assert parked or aside, n
        items = [i for i in Service(root).owner_items()
                 if i.get('kind') == 'program_error' and i.get('job_id') == job_id and i['error'] == 'RuntimeError']
        assert items, n


@pytest.mark.parametrize('kind', sorted(FLOWS))
def test_a_stop_on_purpose_still_stops_the_job_and_is_listed(tmp_path, monkeypatch, kind):
    root, job_id, advance = FLOWS[kind](tmp_path, monkeypatch)

    def conflict(*args, **kwargs):
        raise Conflict('INPUT_CHANGED')
    advance_once = advance
    if kind == 'ingest':
        monkeypatch.setattr(ingest, 'chunk_outcome', conflict)
    elif kind == 'export':
        monkeypatch.setattr(export, '_plan', conflict)
    else:
        monkeypatch.setattr(jobs, 'artifact', conflict)
        advance_once = advance
    for _ in range(2):
        with pytest.raises(Halt):
            try:
                advance_once()
            except ExecutorFailure:  # a maintenance producer's first answer, refused as it cannot be read
                advance_once()
    [item] = [i for i in Service(root).owner_items() if i.get('kind') == 'job_stopped' and i['job_id'] == job_id]
    assert (item['error'], item['message'], item['times']) == ('Conflict', 'INPUT_CHANGED', 2)


def test_a_review_set_aside_takes_its_decisions_with_it_and_the_review_asked_again_is_recorded(tmp_path, monkeypatch):
    """The review's verdicts went into the decision record when it was taken; set aside, they no longer count (a
    ``void`` entry), so the review asked again is recorded as a first verdict, and the job is written."""
    root, job_id, advance = ingest_flow(tmp_path, monkeypatch)
    real, broke = ingest.chunk_outcome, []

    def outcome(root_, state, packet, base):
        if not broke and ((state.get('chunks') or {}).get(base) or {}).get('review'):
            broke.append(state['chunks'][base]['review']['run_id'])
            raise KeyError('a value the outcome did not expect')
        return real(root_, state, packet, base)
    monkeypatch.setattr(ingest, 'chunk_outcome', outcome)
    done = advance()
    assert done['status'] == 'applied', done.get('pending')
    entries = jobs.load(root, job_id)['decisions']
    [void] = [e for e in entries if e['kind'] == 'void']
    assert void['run'] == broke[0] and void['entries'] == jobs.load_run(root, job_id, broke[0])['recorded']
    assert jobs.load_run(root, job_id, broke[0])['set_aside'] == 'outcome'


def test_a_split_card_its_review_packet_cannot_read_is_set_aside_and_asked_again(tmp_path):
    """The mark on the split card's review packet names that card: it is set aside and counted, and the card is
    asked for again (without the mark, the split stage would park the whole job)."""
    from types import SimpleNamespace

    from secondbrain.pipelines import split
    from tests.ingest_helpers import SOURCE_NAME, build_ingest_vault, with_diagrams
    from tests.test_ingest import settings
    from tests.test_invariants import SOURCE, two_chunk_producer
    from tests.test_patch_set import SPLIT_CARD, reviewer, run, sb_existing
    from tests.test_split_repair import BOLD, PLAIN
    root = build_ingest_vault(tmp_path / 'vault')
    settings(chunk_chars=1000)
    (root / '00_Inbox' / SOURCE_NAME).write_text(SOURCE.replace(PLAIN, BOLD), encoding='utf-8')
    job_id = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)['job_id']
    sb_existing(root, 11)
    produced = []

    def produce(task):
        if split.is_slot(task.chunk.split('.')[0]):
            produced.append(task.chunk)
            return {'card': copy.deepcopy(SPLIT_CARD)}
        plain = {**task.packet, 'chunk': {**task.packet['chunk'], 'text': task.packet['chunk']['text'].replace(
            BOLD, PLAIN)}}
        return two_chunk_producer(SimpleNamespace(packet=plain))
    real, broke = ingest._program_view, []

    def program_view(root_, state, slot, candidate):
        if split.is_slot(slot.split('.')[0]) and not broke:
            broke.append(slot)
            raise KeyError('a value the review packet did not expect')
        return real(root_, state, slot, candidate)
    with pytest.MonkeyPatch.context() as m:
        m.setattr(ingest, '_program_view', program_view)
        run(root, job_id, reviewer(), with_diagrams(produce))
    [row] = [r for r in jobs.load(root, job_id).get('program_errors') or [] if r.get('type') == 'KeyError']
    assert (row['slot'], row['role'], row['stage']) == (broke[0], 'producer', 'review_packet')
    assert jobs.load_run(root, job_id, row['run_id'])['set_aside'] == 'review_packet'
    assert produced.count(broke[0]) == 2  # asked for again
    assert not jobs.load(root, job_id).get('parked')

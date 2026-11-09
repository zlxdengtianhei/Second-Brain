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

import ast
import copy
from pathlib import Path

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

PACKAGE = Path(jobs.__file__).resolve().parent


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


def old_rules(tmp_path, monkeypatch):
    """One repair round and a full review in it, as the flows of the repair-round tests were written for."""
    path = tmp_path / 'old-rules.toml'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[review]\nmax_repair_rounds = 1\nfocused_review = false\n', encoding='utf-8')
    monkeypatch.setenv('SECONDBRAIN_CONFIG', str(path))
    config.activate(None)


def ingest_merge_rounds_flow(tmp_path, monkeypatch):
    """The merge check disputes a card every round and its judgement again keeps it (rounds of the merge check)."""
    from tests import test_patch_set as tp
    old_rules(tmp_path, monkeypatch)
    root, job_id, _ = tp.job(tmp_path)

    def check(packet):
        held = {c['slug'] for g in packet.get('patch_set') or [] for c in g['from']}
        return keep_all(packet, _disputes=tuple(s for s in ('lantern-queue',) if s in held))
    review = tp.reviewer(check=check, rejudge=lambda p: {'slug': 'lantern-queue', 'overlap': 'extends',
                                                         'with': [tp.EXISTING], 'reason': 'again'})
    return root, job_id, lambda: tp.run(root, job_id, review)


def ingest_split_rounds_flow(tmp_path, monkeypatch):
    """A split card whose review asks for it again (a split repair round)."""
    from tests import test_patch_set as tp
    old_rules(tmp_path, monkeypatch)
    root, job_id, _ = tp.job(tmp_path, n_props=11)
    review = tp.reviewer(split_review=lambda p: {'decision': 'revise', 'restates': True, 'reason': 'P2 again'})
    return root, job_id, lambda: tp.run(root, job_id, review, tp.producer())


def ingest_packets_flow(tmp_path, monkeypatch):
    """The packets a host asks for (`sb job packet`), each step answered at once, then the advance that writes."""
    root, job_id, _ = ingest_split_flow(tmp_path, monkeypatch)
    from tests.test_patch_set import reviewer
    from secondbrain.executors import FakeExecutor as Fake
    return root, job_id, lambda: _by_packets(root, job_id, Fake('gpt-test', responder=_split_producer()),
                                             Fake('claude-test', responder=reviewer()))


def ingest_diagram_packets_flow(tmp_path, monkeypatch):
    """The same with the diagrams on: the producer's last packets are the diagrams'."""
    root, job_id, _ = ingest_flow(tmp_path, monkeypatch)

    def produce(task):
        return diagram_answer(task.packet, None) if is_diagram_task(task) else copy.deepcopy(first_candidate())

    def review(task):
        return keep_all(task.packet) if is_source_check_task(task) else accept_all(task)
    assert jobs.read_input(root, job_id)['settings'].get('diagrams')
    return root, job_id, lambda: _by_packets(root, job_id, FakeExecutor('gpt-test', responder=produce),
                                             FakeExecutor('claude-test', responder=review))


def _by_packets(root, job_id, produce, review):
    idle = 0
    for _ in range(16):  # until no step of either role is due, twice (the second reads what the first made)
        due = False
        for role, executor in (('producer', produce), ('reviewer', review)):
            try:
                ingest.packet_for(root, job_id, role=role, chunk=None, executor=executor)
                due = True
            except ValueError as said:  # the executor answered at once, or no step of the role is due
                due = due or 'is due' not in str(said)
        idle = 0 if due else idle + 1
        if idle == 2:
            break
    return ingest.advance(root, job_id, producer=produce, reviewer=review)


def _split_producer():
    from types import SimpleNamespace

    from secondbrain.pipelines import split
    from tests.ingest_helpers import with_diagrams
    from tests.test_invariants import two_chunk_producer
    from tests.test_patch_set import SPLIT_CARD
    from tests.test_split_repair import BOLD, PLAIN

    def produce(task):
        if split.is_slot(task.chunk.split('.')[0]):
            return {'card': copy.deepcopy(SPLIT_CARD)}
        plain = {**task.packet, 'chunk': {**task.packet['chunk'], 'text': task.packet['chunk']['text'].replace(
            BOLD, PLAIN)}}
        return two_chunk_producer(SimpleNamespace(packet=plain))
    return with_diagrams(produce)


def interrupted(monkeypatch):
    """The first write of a frozen plan fails, so the next advance resumes it."""
    real, calls = jobs.apply_changes, []

    def apply_changes(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('the disk failed while the frozen plan was written')
        return real(*args, **kwargs)
    monkeypatch.setattr(jobs, 'apply_changes', apply_changes)


def ingest_resume_flow(tmp_path, monkeypatch):
    root, job_id, advance = ingest_flow(tmp_path, monkeypatch)
    interrupted(monkeypatch)
    return root, job_id, lambda: (advance(), advance())[1]


def curation_resume_flow(tmp_path, monkeypatch):
    root, job_id, advance = maintenance_flow('curation')(tmp_path, monkeypatch)
    interrupted(monkeypatch)
    return root, job_id, lambda: (advance(), advance())[1]


def classification_child_flow(tmp_path, monkeypatch):
    """The first review sends rows back: a repair child job for them."""
    from tests.test_classification import ready
    from tests.test_runner import pair
    root, _, row, _ = ready(tmp_path / 't')
    producer, reviewer, _ = pair(root, row, reject_reviews=1)
    return root, 'classify', lambda: runner.run_job(root, 'classify', producer=producer, reviewer=reviewer)


def curation_revise_flow(tmp_path, monkeypatch):
    """The first review sends the map back: a repair round of the producer."""
    from tests import test_curation as tc
    root, job_id, produce, _ = MAINTENANCE['curation'](tmp_path)
    asked = []

    def review(task):
        asked.append(1)
        return {'items': [{'domain': tc.DOM, 'decision': 'revise' if len(asked) == 1 else 'accept',
                           'reason': f'a specific reason {len(asked)}'}]}
    return root, job_id, lambda: runner.run_job(root, job_id, producer=produce,
                                                reviewer=FakeExecutor('claude-test', responder=review))


def ingest_note_check_flow(tmp_path, monkeypatch):
    """A minor note that may name a blocking problem: its check says yes once, and the card is written again."""
    from secondbrain.pipelines import notecheck
    from tests import test_patch_set as tp
    from tests.test_invariants import LEVELS
    old_rules(tmp_path, monkeypatch)
    root, job_id, _ = tp.job(tmp_path)
    noted = {**LEVELS, 'lit-marker-cost': {'overlap': 'distinct',
                                           'notes': [{'category': 'minor', 'text': '没写明一个标记占几个字'}]}}
    inner, asked = tp.reviewer(levels=noted), []

    def review(task):
        if not notecheck.is_slot(task.chunk):
            return inner(task)
        asked.append(task.chunk)
        yes = len(asked) == 1
        return {'answers': [{'id': n['id'], 'answer': 'yes' if yes else 'no', 'category': 'unreadable' if yes else None,
                             'reason': 'the card does not give the size'} for c in task.packet['cards']
                            for n in c['notes']]}
    return root, job_id, lambda: tp.run(root, job_id, review)


FLOWS = {'ingest': ingest_flow, 'export': export_flow, **{k: maintenance_flow(k) for k in MAINTENANCE}}
#: More flows of a kind, for the marks one flow does not reach: a merge check and a split card, merge-check rounds,
#: a split repair round, the packets a host asks for, a write resumed, a classification repair child, a repair round
#: of a maintenance producer, the check of a review's minor notes.
MORE = {'ingest-split': ingest_split_flow, 'ingest-merge-rounds': ingest_merge_rounds_flow,
        'ingest-note-check': ingest_note_check_flow,
        'ingest-split-rounds': ingest_split_rounds_flow, 'ingest-packets': ingest_packets_flow,
        'ingest-diagram-packets': ingest_diagram_packets_flow,
        'ingest-resume': ingest_resume_flow, 'curation-resume': curation_resume_flow,
        'classification-child': classification_child_flow, 'curation-revise': curation_revise_flow}
MAINTENANCE_FLOWS = {FLOWS[k] for k in MAINTENANCE} | {curation_resume_flow, classification_child_flow,
                                                      curation_revise_flow}


def test_every_kind_that_asks_a_model_has_a_flow_here():
    assert {kind for kind, asks in jobs.KINDS.items() if asks} == set(FLOWS)
    assert {name.split('-')[0] for name in MORE} <= set(FLOWS)


class Reads:
    """Count the reads of taken answers (and the copies of them packets show: ``jobs.as_given``,
    ``runner.withhold_reasons``); make the ``fail``-th one raise, and note the mark its error passed first
    (``marks``: file and line)."""

    def __init__(self, monkeypatch, fail=None, copies=False):
        self.n, self.fail, self.failed, self.marks = 0, fail, False, set()
        points = [(jobs, 'artifact'), (jobs, 'require_independent_review')]
        if copies:  # a maintenance job's packets show the answer it read once: its copies fail too
            points += [(jobs, 'as_given'), (runner, 'withhold_reasons')]
        for module, name in points:
            monkeypatch.setattr(module, name, self.wrap(getattr(module, name)))
        self.parked = []
        park = jobs.park

        def parked(root, job_id, exc, stage):
            if 'of a taken answer broke' in str(exc):
                self.parked.append(stage)
            return park(root, job_id, exc, stage)
        monkeypatch.setattr(jobs, 'park', parked)
        mark = jobs._mark

        def noted(exc, where):
            if where and not hasattr(exc, jobs._TAKEN) and 'of a taken answer broke' in str(exc):
                self.marks.add((Path(where[2][0]).resolve().relative_to(PACKAGE).as_posix(), where[2][1]))
            return mark(exc, where)
        monkeypatch.setattr(jobs, '_mark', noted)

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
    reads = Reads(monkeypatch, fail, copies=flow in MAINTENANCE_FLOWS)
    for _ in range(4):  # a maintenance job raises each answer the entry point refuses; ask again as a caller would
        try:
            return root, job_id, advance(), reads
        except ExecutorFailure as failure:
            assert failure.error.get('counted'), failure.error  # the entry point's count, not this layer's
    raise AssertionError('the flow did not settle')


def reads_of(kind, tmp_path, monkeypatch):
    with monkeypatch.context() as m:
        _, _, out, reads = run_flow({**FLOWS, **MORE}[kind], tmp_path / 'plain', m)
    assert reads.n > 0 and out['status'] in {'applied', 'complete', 'published', 'partial', 'needs_revision'}, \
        out['status']
    return reads.n


#: The marks each flow's failing reads passed, filled as the flows run (``test_every_mark_...`` runs the rest).
REACHED: dict = {}


def run_all_reads(kind, tmp_path, monkeypatch):
    total = reads_of(kind, tmp_path, monkeypatch)
    marks = set()
    for n in range(1, total + 1):
        with monkeypatch.context() as m:
            root, job_id, out, reads = run_flow({**FLOWS, **MORE}[kind], tmp_path / f'r{n}', m, fail=n)  # no raise
        marks |= reads.marks
        yield n, root, job_id, reads
    REACHED[kind] = marks


@pytest.mark.parametrize('kind', sorted({**FLOWS, **MORE}))
def test_a_failing_read_of_a_taken_answer_at_any_stage_is_counted_or_parked_and_listed(tmp_path, monkeypatch, kind):
    for n, root, job_id, reads in run_all_reads(kind, tmp_path, monkeypatch):
        assert reads.failed, n
        states = [jobs.load(root, e['job_id']) for e in jobs.list_jobs(root) if e.get('kind') != 'transaction']
        faults = [r for st in states for r in st.get('program_errors') or [] if r.get('type') == 'RuntimeError']
        # counted (set aside, or at the entry point when the read was the check's), or parked; a parking the flow
        # then got past is cleared, from the job and from the owner's list
        assert faults or reads.parked, n
        if faults:
            items = [i for i in Service(root).owner_items()
                     if i.get('kind') == 'program_error' and i['error'] == 'RuntimeError']
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


def test_a_split_card_its_repair_packet_cannot_read_sets_aside_the_last_review_and_asks_it_again(
        tmp_path, monkeypatch):
    """The mark on a split card's repair packet names the last round's review (the packet shows its card and
    findings): that review is set aside and asked again (without the mark, the split stage would park the job)."""
    from secondbrain.pipelines import split
    root, job_id, advance = ingest_split_rounds_flow(tmp_path, monkeypatch)
    real, broke = jobs.as_given, []

    def as_given(answer):
        if isinstance(answer, dict) and 'card' in answer and not broke:  # a split card's answer, shown again
            broke.append(1)
            raise KeyError('a value the repair packet did not expect')
        return real(answer)
    monkeypatch.setattr(jobs, 'as_given', as_given)
    advance()
    [row] = [r for r in jobs.load(root, job_id).get('program_errors') or [] if r.get('type') == 'KeyError']
    assert (row['role'], row['stage']) == ('reviewer', 'produce_packet') and split.is_slot(row['slot'].split('.')[0])
    assert jobs.load_run(root, job_id, row['run_id'])['set_aside'] == 'produce_packet'
    assert not jobs.load(root, job_id).get('parked')


def marks_in_source() -> set:
    """Every ``jobs.reading(...)`` of the package, as file and line (read from the source)."""
    out = set()
    for path in PACKAGE.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'reading' \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == 'jobs':
                out.add((path.relative_to(PACKAGE).as_posix(), node.lineno))
    return out


def test_every_mark_in_the_source_is_reached_by_a_failing_read(tmp_path, monkeypatch):
    """The flows are enumerated by mark: each ``jobs.reading`` of the source has a failing read pass it in some flow,
    so a mark no flow reaches (and so no test keeps) fails here."""
    left = sorted(set({**FLOWS, **MORE}) - set(REACHED))
    if left:  # run alone, or after a flow above failed (that test fails then): the flows are too slow to run here
        pytest.skip(f'the flows of the table above did not all run first: {left}')
    reached = set().union(*REACHED.values())
    source = marks_in_source()
    assert len(source) >= 25
    missing, unknown = sorted(source - reached), sorted(reached - source)
    assert not missing and not unknown, (missing, unknown)


def test_a_mark_reached_outside_the_guard_fails_under_the_tests(tmp_path):
    with pytest.raises(jobs.MarkOutsideGuard):
        with jobs.reading('outcome'):
            pass


def test_a_packet_a_host_asks_for_that_cannot_read_a_taken_answer_counts_and_lists_it(tmp_path, monkeypatch):
    """`sb job packet` reads the taken answers too (the review's packet shows the candidate): it goes through the
    guard as `sb job advance` does, so the answer is set aside, counted and listed, and no bare error reaches the
    host."""
    from secondbrain.executors.host import HostExecutor
    root, job_id, _ = lantern_job(tmp_path)
    host = HostExecutor.for_vault(root)
    [handed] = ingest.advance(root, job_id, producer=host, reviewer=host)['handoffs']
    jobs.submit(root, job_id, copy.deepcopy(first_candidate()), role='producer', run_id=handed['run_id'])

    def broken(*args, **kwargs):
        raise KeyError('a value the review packet did not expect')
    monkeypatch.setattr(ingest, 'review_packet', broken)
    with pytest.raises(ValueError, match='No reviewer step is due'):  # the candidate set aside: the producer's turn
        Service(root).job_packet(job_id, 'reviewer')
    state = jobs.load(root, job_id)
    assert [(r['type'], r['stage'], r['slot'], r['role']) for r in state['program_errors']] == [
        ('KeyError', 'review_packet', 'c01', 'producer')]
    assert jobs.load_run(root, job_id, handed['run_id'])['status'] == 'failed'
    assert ingest._refusals(state['chunks']['c01'], ingest.answer_role('c01', 'producer')) == 1
    assert [(i['stage'], i['slot']) for i in Service(root).owner_items() if i.get('kind') == 'program_error'] == [
        ('review_packet', 'c01')]


def broken_writes(monkeypatch, always):
    real, calls = jobs.apply_changes, []

    def apply_changes(*args, **kwargs):
        calls.append(1)
        if always or len(calls) == 1:
            raise RuntimeError('the disk failed while the frozen plan was written')
        return real(*args, **kwargs)
    monkeypatch.setattr(jobs, 'apply_changes', apply_changes)


@pytest.mark.parametrize('kind', ['ingest', 'curation'])
def test_a_write_that_fails_at_every_resume_parks_and_each_advance_returns(tmp_path, monkeypatch, kind):
    """A frozen plan's write resumed is no one answer's: its error parks the job; the same error again adds to the
    parking's count, and every advance returns."""
    root, job_id, advance = FLOWS[kind](tmp_path, monkeypatch)
    broken_writes(monkeypatch, always=True)
    outs = [advance() for _ in range(4)]  # none raises
    state = jobs.load(root, job_id)
    assert [o['status'] for o in outs] == ['applying'] * 4 and all(o.get('parked') for o in outs)
    assert (state['parked']['stage'], state['parked']['times']) == ('resume', 3)
    assert [(r['type'], r['stage']) for r in state['program_errors']] == [('RuntimeError', 'apply'),
                                                                          ('RuntimeError', 'resume')]
    assert {i['stage'] for i in Service(root).owner_items()
            if i.get('kind') == 'program_error' and i['job_id'] == job_id} == {'apply', 'resume'}


@pytest.mark.parametrize('kind', ['ingest', 'curation'])
def test_a_write_resumed_after_a_parking_clears_the_parking(tmp_path, monkeypatch, kind):
    root, job_id, advance = FLOWS[kind](tmp_path, monkeypatch)
    broken_writes(monkeypatch, always=False)
    assert advance().get('parked')
    done = advance()
    assert done['status'] in {'applied', 'complete'} and 'parked' not in done
    state = jobs.load(root, job_id)
    assert not state.get('parked') and not state.get('program_errors')
    assert not [i for i in Service(root).owner_items() if i['job_id'] == job_id]


def test_the_same_error_after_a_set_aside_parks_instead_of_setting_aside_the_next_answer(tmp_path, monkeypatch):
    """A repair round's producer packet reads the other chunks' cards; when that read fails every time, the review
    blamed for it is set aside once, and the same error with the next review parks the job (it was not the review's
    error), instead of spending the slot's budget on innocent reviews and failing the job."""
    from tests.ingest_helpers import first_review, repair_candidate, repair_review
    root, job_id, _ = lantern_job(tmp_path)
    asks = []

    def produce(task):
        asks.append(('producer', task.chunk))
        return copy.deepcopy(first_candidate() if '.r' not in task.chunk else repair_candidate())

    def review(task):
        asks.append(('reviewer', task.chunk))
        return copy.deepcopy(first_review() if '.r' not in task.chunk else repair_review())
    real = ingest._other_cards

    def other_cards(root_, state, packet, base, *named):
        if ((state.get('chunks') or {}).get('c01') or {}).get('review'):
            raise RuntimeError("reading the other chunks' cards broke")
        return real(root_, state, packet, base, *named)
    monkeypatch.setattr(ingest, '_other_cards', other_cards)
    outs = [ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce),
                           reviewer=FakeExecutor('claude-test', responder=review)) for _ in range(3)]
    assert [o['status'] for o in outs] == ['prepared'] * 3 and outs[-1]['parked']['stage'] == 'produce_packet'
    assert asks == [('producer', 'c01'), ('reviewer', 'c01'), ('reviewer', 'c01')]
    assert [(r['stage'], r['slot'], r['role']) for r in jobs.load(root, job_id)['program_errors']] == [
        ('produce_packet', 'c01', 'reviewer'), ('produce_packet', None, None)]

"""The context note: where a chunk sits in its source, for understanding only (design R-03, experiment H6)."""
from __future__ import annotations

import json

import pytest

from secondbrain import cli, config, jobs
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import chunking, ingest, notes, source_check
from tests.ingest_helpers import with_diagrams
from tests.test_invariants import FILLER, two_chunk_job, two_chunk_producer, two_chunk_reviewer

BOOK = ('# Part one\n\nIntro text.\n\n## Chapter A\n\nAlpha paragraph one.\n\nAlpha paragraph two.\n\n'
        '```\n# not a heading\n```\n\n### Section A.1\n\nDetail.\n\n#### Too deep\n\nDeeper.\n\n'
        '## Chapter B\n\nThe three kinds above differ by size.\n')


def spans(text, *cuts):
    edges = [0, *cuts, len(text)]
    return [{'chunk_id': f'c{i:02d}', 'start': a, 'end': b} for i, (a, b) in enumerate(zip(edges, edges[1:]), 1)]


# --- the program's parts ------------------------------------------------------------------------------------------
def test_the_outline_is_three_levels_of_headings_outside_code():
    assert [(h['level'], h['title']) for h in chunking.outline(BOOK)] == [
        (1, 'Part one'), (2, 'Chapter A'), (3, 'Section A.1'), (2, 'Chapter B')]


def test_the_heading_path_is_where_the_chunk_starts_outer_first_and_short():
    at = BOOK.index('Detail.')
    assert chunking.heading_path(BOOK, at) == 'Part one › Chapter A › Section A.1'
    assert chunking.heading_path(BOOK, BOOK.index('Deeper.')) == 'Part one › Chapter A › Section A.1 › Too deep'
    assert chunking.heading_path(BOOK, BOOK.index('The three')) == 'Part one › Chapter B'
    assert chunking.heading_path(BOOK, at, limit=25) == '… › Section A.1'
    assert chunking.heading_path('no headings', 3) == ''


def test_the_end_of_the_chunk_before_is_its_last_paragraphs_whole():
    end = BOOK.index('```')
    assert chunking.previous_tail(BOOK, 0, end) == 'Alpha paragraph one.\n\nAlpha paragraph two.'
    assert chunking.previous_tail(BOOK, 0, end, limit=25) == 'Alpha paragraph two.'
    long = 'First sentence here. ' * 40 + 'Last one.'
    tail = chunking.previous_tail(long, 0, len(long), limit=100)
    assert len(tail) <= 100 and tail.startswith('First sentence') and tail.endswith('Last one.')


def test_the_note_fits_its_budget_in_order_and_marks_the_chunk_in_the_outline():
    chunks = spans(BOOK, BOOK.index('## Chapter B'))
    note = chunking.context_note(BOOK, chunks, 'c02', 2000)
    assert note['use'] == chunking.NOTE_USE
    assert note['heading_path'] == 'Part one › Chapter B' and note['previous_tail'] == '#### Too deep\n\nDeeper.'
    assert note['outline'].splitlines()[-1] == '  - Chapter B' + chunking.MARKER
    assert chunking.note_chars(note) <= 2000
    for chars in (2000, 300, 60, 25):
        small = chunking.context_note(BOOK, chunks, 'c02', chars, summary='Chapter A explains three kinds.')
        assert chunking.note_chars(small) <= chars
    tight = chunking.context_note(BOOK, chunks, 'c02', 30, summary='x' * 50)
    assert tight['heading_path'] and tight['outline'] == ''  # the outline is packed last


def test_an_outline_too_long_keeps_the_lines_around_the_chunk():
    text = ''.join(f'## Section {i}\n\nText {i}.\n\n' for i in range(200))
    note = chunking.outline_text(text, text.index('Text 120.'), limit=300)
    lines = note.splitlines()
    assert len(note) <= 300 and lines[0] == lines[-1] == '…'
    assert '  - Section 120' + chunking.MARKER in lines and '  - Section 0' not in lines


def test_a_source_of_one_chunk_gets_no_note():
    assert chunking.context_note(BOOK, spans(BOOK), 'c01', 2000) is None


# --- in a job ----------------------------------------------------------------------------------------------------
def packets(tmp_path, name='vault', **fields):
    root, job_id = two_chunk_job(tmp_path, name=name, **fields)
    seen = []

    def producer(task):
        seen.append(task)
        if notes.is_slot(task.chunk):
            if task.chunk == notes.CONCEPTS_SLOT:
                return {'concepts': [{'name': 'lantern queue', 'chunks': ['c01']},
                                     {'name': 'lit markers', 'chunks': ['c03']}]}
            return {'summary': f'Before {task.chunk}: lantern queues keep arrival order.'}
        return two_chunk_producer(task)
    reviews = []
    review = two_chunk_reviewer([])

    def reviewer(task):
        reviews.append(task)
        return review(task)
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=with_diagrams(producer)),
                          reviewer=FakeExecutor('claude-test', responder=reviewer))
    return root, job_id, done, seen, reviews


def note_of(tasks, chunk):
    return next(t.packet.get('context_note') for t in tasks if t.chunk == chunk and 'chunk' in t.packet)


def test_producer_and_reviewer_get_the_same_program_note_and_the_merge_check_the_outline(tmp_path):
    root, job_id, done, seen, reviews = packets(tmp_path)
    assert done['status'] == 'applied', done.get('pending')
    note = note_of(seen, 'c03')
    assert note['heading_path'] == 'Lantern queues › Lit markers'
    assert FILLER.endswith(note['previous_tail']) and 400 < len(note['previous_tail']) <= 600  # cut at a sentence
    assert note['previous_tail'].startswith('Filler sentence') and 'summary' not in note
    assert '- Lit markers' + chunking.MARKER in note['outline']
    assert note_of([r for r in reviews if r.chunk == 'c03'], 'c03') == note
    assert note_of(seen, 'c01')['previous_tail'] == ''
    [check] = [r for r in reviews if source_check.is_slot(r.chunk)]
    assert check.packet['source']['outline'] == '- Lantern queues\n  - Notes\n  - Lit markers'
    assert not any(notes.is_slot(t.chunk) for t in seen)  # the program's note needs no model
    assert jobs.read_input(root, job_id)['settings']['context_note'] == 'program'


def test_a_quote_from_the_note_is_not_a_quote_of_the_chunk(tmp_path):
    root, job_id = two_chunk_job(tmp_path)
    tries = []

    def producer(task):
        answer = two_chunk_producer(task)
        if task.chunk == 'c03' and not tries:
            tries.append(task)
            borrowed = task.packet['context_note']['previous_tail'].split('. ')[0] + '.'
            answer['cards'][0] = {**answer['cards'][0], 'source_quotes': [borrowed]}
        return answer
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=with_diagrams(producer)),
                          reviewer=FakeExecutor('claude-test', responder=two_chunk_reviewer([])))
    assert done['status'] == 'applied'
    error = jobs.load(root, job_id)['chunks']['c03']['last_validation_error']
    assert 'cards[lit-marker-cost].source_quotes: not a verbatim slice' in error


def test_with_the_note_off_no_packet_has_one(tmp_path):
    root, job_id, done, seen, reviews = packets(tmp_path, context_note='off')
    assert done['status'] == 'applied'
    assert all('context_note' not in t.packet for t in seen + reviews)
    [check] = [r for r in reviews if source_check.is_slot(r.chunk)]
    assert 'outline' not in check.packet['source']


def test_the_summary_arm_asks_once_per_chunk_after_the_first_and_puts_it_in_the_note(tmp_path):
    root, job_id, done, seen, _ = packets(tmp_path, context_note='summary')
    assert done['status'] == 'applied', done.get('pending')
    asked = [t for t in seen if notes.is_slot(t.chunk)]
    assert [t.chunk for t in asked] == ['nt-c02', 'nt-c03']
    assert asked[1].packet['before'].endswith(FILLER + '\n\n') and asked[1].packet['limit_chars'] == 300
    assert note_of(seen, 'c03')['summary'] == 'Before nt-c03: lantern queues keep arrival order.'
    assert 'summary' not in note_of(seen, 'c01')
    assert chunking.note_chars(note_of(seen, 'c03')) <= 2000


def test_the_concepts_arm_asks_once_for_the_source(tmp_path):
    root, job_id, done, seen, _ = packets(tmp_path, context_note='concepts')
    assert done['status'] == 'applied', done.get('pending')
    assert [t.chunk for t in seen if notes.is_slot(t.chunk)] == ['nt-concepts']
    assert note_of(seen, 'c01')['concepts'] == '- lantern queue (c01)\n- lit markers (c03)'


def test_a_summary_that_keeps_failing_is_given_up_and_the_chunk_keeps_the_programs_note(tmp_path):
    root, job_id = two_chunk_job(tmp_path, context_note='summary')
    seen = []

    def producer(task):
        seen.append(task)
        if notes.is_slot(task.chunk):
            return {'summary': 'x' * 301}
        return two_chunk_producer(task)
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=with_diagrams(producer)),
                          reviewer=FakeExecutor('claude-test', responder=two_chunk_reviewer([])))
    assert done['status'] == 'applied'
    state = jobs.load(root, job_id)['chunks']
    assert 'at most 300 characters' in state['nt-c02']['last_validation_error'] and state['nt-c02']['skipped']
    note = note_of(seen, 'c02')
    assert 'summary' not in note and note['heading_path'] and note['previous_tail']


@pytest.mark.parametrize('payload, problem', [
    ({'concepts': []}, 'list the concepts'),
    ({'concepts': [{'name': 'x', 'chunks': ['c09']}]}, 'chunk ids of this source'),
    ({'concepts': [{'name': '', 'chunks': ['c01']}]}, 'a name of at most 80'),
])
def test_a_concept_list_out_of_form_is_refused(payload, problem):
    with pytest.raises(ValueError, match=problem):
        notes.validate_concepts(payload, ['c01', 'c02'])


def test_the_setting_is_checked():
    with pytest.raises(ValueError, match='context_note must be one of off, program, summary, concepts'):
        config.IngestConfig(context_note='both')
    with pytest.raises(ValueError, match='context_note_chars'):
        config.IngestConfig(context_note_chars=50)


def test_host_mode_hands_out_the_summary_before_the_chunk(tmp_path):
    from secondbrain.executors.host import HostExecutor
    root, job_id = two_chunk_job(tmp_path, context_note='summary')
    host = HostExecutor()
    waiting = ingest.advance(root, job_id, producer=host, reviewer=host)
    assert sorted(h['chunk'] for h in waiting['handoffs']) == ['c01', 'nt-c02', 'nt-c03']
    handoff = next(h for h in waiting['handoffs'] if h['chunk'] == 'nt-c02')
    assert handoff['step'] == 'note' and handoff['role'] == 'producer'
    assert ingest.packet_for(root, job_id, role='producer', chunk='nt-c02', executor=host)['run_id'] == handoff['run_id']
    path = tmp_path / 'summary.json'
    path.write_text(json.dumps({'summary': 'The first chunk defines lantern queues.'}), encoding='utf-8')
    assert cli.main(['--vault', str(root), 'job', 'submit', job_id, '--role', 'producer', '--run-id',
                     handoff['run_id'], '--file', str(path), '--model', 'gpt-test']) == 0
    again = ingest.advance(root, job_id, producer=host, reviewer=host)
    assert 'c02' in {h['chunk'] for h in again['handoffs']}

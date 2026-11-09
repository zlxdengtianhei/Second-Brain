"""The fields only the program writes into an answer it keeps (``jobs.PROGRAM_WRITTEN``) are the whole set and no
model's.

A deep reading of the one entry point found the list written by hand, with no test to make a new field join it. Now
every answer any test sends through a registered check is held to it (``conftest.program_fields_listed``): a field
a check adds is a field of a model schema (a default it fills in) or a listed one. The list had missed one: the fix
a reviewer gave that the program refused (``amend_refused``, kept on the review's row), so a review that wrote it
itself put the program's words into the record. The cards and answers are invented.
"""
from __future__ import annotations

import ast
import copy
from pathlib import Path

from secondbrain import jobs
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest
from tests.field_audit import model_fields, unlisted
from tests.ingest_helpers import first_candidate, first_review
from tests.test_answer_repair import lantern_job

SRC = Path(jobs.__file__).parent


def test_no_field_only_the_program_writes_is_a_field_of_a_model_schema():
    assert len(model_fields()) > 50
    assert set(jobs.PROGRAM_WRITTEN).isdisjoint(model_fields())


def written_names() -> set:
    """String keys the package writes into a dict outside the gate: ``{'name': ...}`` and ``x['name'] = ...``."""
    out = set()
    for path in SRC.rglob('*.py'):
        if path.name == 'jobs.py':
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Dict):
                out |= {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and \
                    isinstance(node.slice, ast.Constant):
                out.add(node.slice.value)
    return out


def test_every_listed_field_is_one_the_package_writes():
    assert set(jobs.PROGRAM_WRITTEN) <= written_names()


def test_the_audit_names_a_field_a_check_adds_that_is_neither_listed_nor_a_models():
    given = {'cards': [{'slug': 'a'}]}
    kept = {'cards': [{'slug': 'a', 'aliases': [], 'stamp': 1}], 'coverage': [], 'program_repairs': [], 'seal': 2}
    assert unlisted(given, kept, jobs.PROGRAM_WRITTEN) == ['.seal', 'cards[].stamp']


def test_a_review_row_that_writes_amend_refused_itself_has_it_taken_out(tmp_path):
    root, job_id, _ = lantern_job(tmp_path)
    review = copy.deepcopy(first_review())
    forged = {'amend': [{'op': 'refile', 'pillar': 'p', 'topic': 't'}], 'problems': ['the model wrote this']}
    review['cards'][0]['amend_refused'] = forged
    done = ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=lambda t: first_candidate()),
                          reviewer=FakeExecutor('claude-test', responder=lambda t: copy.deepcopy(review)))
    state = jobs.load(root, job_id)
    stored = jobs.artifact(root, state, 'review', 'c01')
    assert all('amend_refused' not in row for row in stored['cards']), done['status']
    [run] = [r for r in jobs.list_runs(root, job_id) if r['role'] == 'reviewer' and r.get('chunk') == 'c01']
    assert run['program_fields_removed'] == ['cards[0].amend_refused']

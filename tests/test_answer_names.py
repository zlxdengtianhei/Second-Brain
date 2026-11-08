"""An answer that gives an object where the program keys on a name is refused and asked again, not a crash.

A producer once sent ``"topic": {"name": ...}``; the validator put the card's place in a set before checking its
type and stopped the whole ``sb ingest`` with a TypeError (the same in 7e42c7b).
"""
from __future__ import annotations

import copy

import pytest

from secondbrain import jobs
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest
from tests.ingest_helpers import (SOURCE_NAME, build_ingest_vault, first_candidate, first_review, repair_candidate,
                                  repair_review, scripted)


def bad_candidate(path, value):
    out = copy.deepcopy(first_candidate())
    target = out
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return out


@pytest.mark.parametrize('path, value, problem', [
    (('cards', 0, 'topic'), {'name': 'Basics'}, 'cards[0].topic must be a string, not dict'),
    (('cards', 0, 'domain'), ['attention'], 'cards[0].domain must be a string, not list'),
    (('cards', 0, 'slug'), {'id': 'x'}, 'cards[0].slug must be a string, not dict'),
    (('cards', 0, 'relations', 0, 'target'), {'id': 'x'}, 'cards[0].relations[0].target must be a string'),
    (('new_topics', 0, 'name'), {'zh': 'x'}, 'new_topics[0].name must be a string'),
    (('coverage', 0, 'cards'), [{'slug': 'x'}], 'coverage[0].cards[0] must be a string, not dict'),
    (('cards',), {'lantern-queue': {}}, 'cards must be a list'),
])
def test_a_producer_answer_with_an_object_for_a_name_is_refused_and_asked_again(tmp_path, path, value, problem):
    root = build_ingest_vault(tmp_path / 'vault')
    produce, respond, seen = scripted([bad_candidate(path, value), first_candidate(), repair_candidate()],
                                      [first_review(), repair_review()])
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    done = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=respond))
    assert done['status'] == 'applied', done.get('pending')
    assert problem in jobs.load(root, job['job_id'])['chunks']['c01']['last_validation_error']
    assert len(seen['producer']) == 3  # the bad answer, then the good one, then the repair round


def test_a_review_with_an_object_for_a_slug_is_refused_and_asked_again(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    bad = copy.deepcopy(first_review())
    bad['cards'][1]['slug'] = {'slug': bad['cards'][1]['slug']}
    produce, respond, seen = scripted([first_candidate(), repair_candidate()],
                                      [bad, first_review(), repair_review()])
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    done = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=respond))
    assert done['status'] == 'applied', done.get('pending')
    error = jobs.load(root, job['job_id'])['chunks']['c01']['last_review_error']
    assert 'cards[1].slug must be a string, not dict' in error


def leaves(value, path=()):
    """Every string in ``value``, by its path."""
    if isinstance(value, str):
        yield path
    elif isinstance(value, dict):
        for key, sub in value.items():
            yield from leaves(sub, path + (key,))
    elif isinstance(value, list):
        for i, sub in enumerate(value):
            yield from leaves(sub, path + (i,))


def put(value, path, new):
    out = copy.deepcopy(value)
    target = out
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = new
    return out


def shown(path):
    return ''.join(f'[{p}]' if isinstance(p, int) else f'.{p}' for p in path).lstrip('.')


def test_an_object_in_any_string_field_of_an_answer_is_refused_by_name_never_a_crash(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    state, packet = jobs.load(root, job['job_id']), jobs.read_input(root, job['job_id'])
    good, review = first_candidate(), first_review()
    review['cards'][0]['conflicts'] = [{'with': 'kc_old', 'detail': 'a number differs'}]
    review['new_topics'] = [{'domain': 'attention', 'pillar': 'p', 'name': 'n', 'decision': 'accept', 'reason': 'ok'}]
    checked = 0
    for role, answer in (('producer', good), ('reviewer', review)):
        for path in leaves(answer):
            bad = put(answer, path, {'value': 'x'})
            with pytest.raises(ingest.IngestError) as refused:
                ingest.validate_submission(packet, bad, role=role, candidate=good, root=root, chunk='c01', state=state)
            assert f'{shown(path)} must be a string, not dict' in str(refused.value), (role, path)
            checked += 1
    assert checked > 40


@pytest.mark.parametrize('path', [('cards', 0, 'conflicts', 0, 'with'), ('new_topics', 0, 'decision')])
def test_a_review_with_an_object_for_a_conflict_or_a_topic_decision_is_asked_again(tmp_path, path):
    root = build_ingest_vault(tmp_path / 'vault')
    bad = copy.deepcopy(first_review())
    bad['cards'][0]['conflicts'] = [{'with': 'kc_old', 'detail': 'a number differs'}]
    bad['new_topics'] = [{'domain': 'attention', 'pillar': 'p', 'name': 'n', 'decision': 'accept', 'reason': 'ok'}]
    bad = put(bad, path, {'id': 'kc_old'})
    produce, respond, seen = scripted([first_candidate(), repair_candidate()], [bad, first_review(), repair_review()])
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    done = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=respond))
    assert done['status'] == 'applied', done.get('pending')
    assert f'{shown(path)} must be a string, not dict' in jobs.load(root, job['job_id'])['chunks']['c01']['last_review_error']

"""Any field of an ingest answer in the wrong JSON type is refused by name and asked again, never a crash.

A model answer once gave a merge-check link as a string; the validator called ``.get`` on it and the ingest
process stopped with an AttributeError (H6, the same in 7e42c7b). This is a shape fuzz: for every role's answer
(producer, reviewer, merge check, split producer, split reviewer) it takes each field of the role's output schema
(those in a valid answer, and the ones the answer leaves out), puts a value of every other JSON type there (a
string, an object, a list, a number, null), and runs the validation gate. Each must be refused, naming the field.
"""
from __future__ import annotations

import copy

import pytest

from secondbrain import jobs, schemas
from secondbrain.pipelines import ingest, source_check, split
from tests.helpers import EXISTING
from tests.ingest_helpers import SOURCE_NAME, build_ingest_vault, first_candidate, first_review, keep_all
from tests.test_invariants import ARRIVAL
from tests.test_patch_set import SPLIT_CARD, job, producer, reviewer, run

#: One value of each JSON type, by the schema type it fits.
VALUES = [('string', 'x'), ('object', {'k': 1}), ('array', ['x']), ('number', 5), ('null', None)]


def fits(kind: str, allowed: set) -> bool:
    return kind in allowed or (kind == 'number' and 'integer' in allowed)


def fields(answer, schema: dict, path=()):
    """``(path, schema)`` of every field the schema types: the answer's own, and the properties it leaves out."""
    yield path, schema
    if isinstance(answer, dict):
        for key, sub in (schema.get('properties') or {}).items():
            if key in answer:
                yield from fields(answer[key], sub, path + (key,))
            else:
                yield path + (key,), sub
    elif isinstance(answer, list) and isinstance(schema.get('items'), dict):
        for i, item in enumerate(answer[:2]):
            yield from fields(item, schema['items'], path + (i,))


def put(answer, path, value):
    if not path:
        return value
    out = copy.deepcopy(answer)
    target = out
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return out


def shown(path) -> str:
    return ''.join(f'[{p}]' if isinstance(p, int) else f'.{p}' for p in path).lstrip('.') or 'the answer'


def fuzz(answer, schema, validate) -> tuple[int, int, list]:
    """``(fields, cases, problems)``: every field of ``answer`` given every JSON type its schema does not allow."""
    validate(copy.deepcopy(answer))  # the answer itself is valid
    problems, count, cases = [], 0, 0
    for path, sub in fields(answer, schema):
        kinds = sub.get('type')
        allowed = {kinds} if isinstance(kinds, str) else set(kinds or [])
        if not allowed:
            continue
        count += 1
        for kind, value in VALUES:
            if fits(kind, allowed):
                continue
            cases += 1
            try:
                validate(put(answer, path, value))
            except ValueError as refused:  # IngestError too: refused, asked again
                if shown(path) not in str(refused):
                    problems.append((shown(path), kind, f'refused without naming the field: {str(refused)[:200]}'))
                continue
            except Exception as exc:  # noqa: BLE001 - what the test is for
                problems.append((shown(path), kind, f'raised {type(exc).__name__}: {exc}'))
                continue
            problems.append((shown(path), kind, 'accepted'))
    return count, cases, problems


# --- the answers, each in the state the gate sees it --------------------------------------------------------------
def chunk_roles(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    state, packet = jobs.load(root, prepared['job_id']), jobs.read_input(root, prepared['job_id'])
    good, review = first_candidate(), first_review()
    review['cards'][0]['conflicts'] = [{'with': EXISTING, 'detail': 'a number differs'}]
    review['cards'][0]['notes'] = [{'category': 'minor', 'text': 'the essence could be shorter'}]
    review['new_topics'] = [{**{k: t[k] for k in ('domain', 'pillar', 'name')}, 'decision': 'accept', 'reason': 'ok'}
                            for t in good.get('new_topics') or []]

    def gate(role, candidate=None):
        return lambda payload: ingest.validate_submission(packet, payload, role=role, candidate=candidate, root=root,
                                                          chunk='c01', state=state)
    return {'producer': (good, schemas.CARDS, gate('producer')),
            'reviewer': (review, schemas.REVIEW_CARDS, gate('reviewer', good))}


def merge_and_split_roles(tmp_path):
    root, job_id, _ = job(tmp_path, n_props=11)  # a patch past 12 propositions: a split slot too
    checks = []
    done = run(root, job_id, reviewer(checks), producer())
    assert done['status'] == 'applied', done.get('pending')
    state, packet = jobs.load(root, job_id), jobs.read_input(root, job_id)
    frozen = checks[0].packet
    merge = keep_all(frozen, **{'filler-note': {'links': [{'target': 'lit-marker-cost', 'type': 'similar_to',
                                                           'note': 'both on order'}]},
                                'arrival-order': {'overlap': 'extends', 'with': 'lit-marker-cost', 'reason': 'r',
                                                  'carry': {'propositions': list(ARRIVAL['propositions'])}}})
    card = {'card': {**SPLIT_CARD, 'slug': 'lantern-skip-ahead-b', 'title': 'Skipping ahead, once more'}}
    verdict = {'decision': 'accept', 'restates': False, 'reason': 'faithful',
               'notes': [{'category': 'minor', 'text': 'the title could be shorter'}]}

    def gate(role, chunk, candidate=None):
        return lambda payload: ingest.validate_submission(packet, payload, role=role, candidate=candidate, root=root,
                                                          chunk=chunk, state=state)
    return {'frozen': frozen, 'merge check': (merge, source_check.SCHEMA, gate('reviewer', 'xc01', frozen)),
            'split producer': (card, split.CARD_SCHEMA, gate('producer', 'sp01')),
            'split reviewer': (verdict, split.REVIEW_SCHEMA, gate('reviewer', 'sp01', card))}


@pytest.mark.parametrize('role', ['producer', 'reviewer', 'merge check', 'split producer', 'split reviewer'])
def test_every_field_in_the_wrong_type_is_refused_by_name_never_a_crash(tmp_path, role):
    roles = chunk_roles(tmp_path) if role in {'producer', 'reviewer'} else merge_and_split_roles(tmp_path)
    answer, schema, validate = roles[role]
    count, cases, problems = fuzz(answer, schema, validate)
    assert count >= 3 and cases >= 3 * count
    assert problems == []


def test_h6_a_merge_check_link_given_as_a_string_is_refused_not_a_crash(tmp_path):
    """The H6 answer: `links: ["arrival-order"]`. Refused at the gate, and by the merge check's own validator."""
    roles = merge_and_split_roles(tmp_path)
    answer, _, validate = roles['merge check']
    row = next(r for r in answer['cards'] if r['slug'] == 'filler-note')
    for value in (['arrival-order'], [5], [None], 'arrival-order'):
        row['links'] = value
        with pytest.raises(ValueError, match=r'links'):
            validate(answer)
        with pytest.raises(ValueError, match=r'links'):
            source_check.validate_answer(roles['frozen'], answer)


def test_a_slip_inside_a_validator_that_the_type_check_lets_through_is_a_refusal():
    """Third verdict, not blocking 4: the second layer of the guard. The answer has every type right, and the
    validator still meets a shape it did not expect (a KeyError here): refused by name, never a crash."""
    @schemas.shape_checked({'type': 'object', 'properties': {'rows': {'type': 'array'}}}, payload=0, what='answer')
    def validate(payload):
        return {row['slug']: row for row in payload['rows']}  # a row without "slug" slips
    assert validate({'rows': [{'slug': 'a'}]}) == {'a': {'slug': 'a'}}
    with pytest.raises(schemas.ShapeError, match=r"unexpected shape \(KeyError: 'slug'\)"):
        validate({'rows': [{'name': 'a'}]})
    with pytest.raises(schemas.ShapeError, match=r'rows must be a list, not str'):
        validate({'rows': 'a'})


def test_every_field_of_a_judgement_again_in_the_wrong_type_is_refused_by_name():
    """Third verdict, not blocking 4: the dispute re-judge answer was not in the fuzz."""
    frozen = {'card': {'slug': 'lantern-queue'}}
    answer = {'slug': 'lantern-queue', 'overlap': 'extends', 'with': ['card_exist'], 'reason': 'it adds markers'}
    count, cases, problems = fuzz(answer, ingest.REJUDGE_SCHEMA,
                                  lambda p: ingest._validate_rejudge(frozen, p, {'card_exist'}))
    assert count == 6 and cases >= 18 and problems == []  # the answer, its four fields, one item of with

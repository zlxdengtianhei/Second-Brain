"""Contract tests for the HTTP executors against hand-written recorded responses.

No network: every request goes to an ``httpx.MockTransport`` that checks the
request shape and answers with a fixture from ``tests/fixtures/http``.
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from secondbrain import cli, config, jobs
from secondbrain.executors.api import ApiExecutor, parse_json_text
from secondbrain.executors.base import Task
from secondbrain.executors.factory import build
from secondbrain.gates import model_family
from secondbrain.pipelines import ingest
from secondbrain.store import state_root
from tests.ingest_helpers import (DIMMING_IR, SOURCE_NAME, build_ingest_vault, diagram_answer, first_candidate,
                                  first_review, packet_in, repair_candidate, repair_review)

HTTP = Path(__file__).parent / 'fixtures' / 'http'
KEY = 'sk-test-not-a-real-key'
SCHEMA = {'type': 'object', 'required': ['ok'], 'properties': {'ok': {'type': 'boolean'}}}


def fixture(name):
    return json.loads((HTTP / name).read_text(encoding='utf-8'))


def openai_endpoint(**kw):
    return config.EndpointConfig(kind='openai', base_url='https://llm.example.test/v1', model='gpt-test',
                                 api_key_env='SB_TEST_PRODUCER_KEY', **kw)


def anthropic_endpoint(**kw):
    return config.EndpointConfig(kind='anthropic', base_url='https://anthropic.example.test',
                                 model='claude-test', api_key_env='SB_TEST_REVIEWER_KEY', **kw)


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv('SB_TEST_PRODUCER_KEY', KEY)
    monkeypatch.setenv('SB_TEST_REVIEWER_KEY', KEY + '-r')


def task(**kw):
    base = dict(job_id='job-1', run_id='r-1', role='producer', step='produce', prompt='Do the task.',
                packet={'chunk': {'text': 'hello'}}, packet_sha256='x', output_schema=SCHEMA)
    base.update(kw)
    return Task(**base)


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        status, body, headers = self.responses.pop(0)
        return httpx.Response(status, json=body, headers=headers or {})

    def body(self, i=0):
        return json.loads(self.requests[i].content)


def executor(endpoint, recorder, sleeps=None):
    return ApiExecutor(endpoint, transport=httpx.MockTransport(recorder),
                       sleep=(sleeps.append if sleeps is not None else (lambda s: None)))


def test_openai_request_and_response_contract():
    rec = Recorder((200, fixture('openai_chat_completion.json'), None))
    result = executor(openai_endpoint(max_output_tokens=900), rec).run(task())
    request = rec.requests[0]
    assert request.method == 'POST' and str(request.url) == 'https://llm.example.test/v1/chat/completions'
    assert request.headers['authorization'] == f'Bearer {KEY}'
    body = rec.body()
    assert body['model'] == 'gpt-test' and body['max_tokens'] == 900
    assert body['response_format']['type'] == 'json_schema'
    assert body['response_format']['json_schema']['schema'] == SCHEMA
    user = body['messages'][1]['content']
    assert body['messages'][0]['role'] == 'system' and user.startswith('Do the task.')
    assert '## Output JSON schema' in user and '<task_packet>' in user and '"hello"' in user
    assert result.status == 'completed' and result.output == {'ok': True}
    assert result.model_reported == 'gpt-test-2026-01-01' and result.attestation == 'provider_response'
    assert result.usage == {'input_tokens': 120, 'output_tokens': 8}


def test_openai_falls_back_when_json_schema_is_unsupported():
    rec = Recorder((400, fixture('openai_error_response_format.json'), None),
                   (200, fixture('openai_chat_completion.json'), None))
    result = executor(openai_endpoint(), rec).run(task())
    assert result.status == 'completed'
    assert rec.body(0)['response_format']['type'] == 'json_schema'
    assert rec.body(1)['response_format'] == {'type': 'json_object'}


def test_json_object_mode_is_used_when_configured():
    rec = Recorder((200, fixture('openai_chat_completion.json'), None))
    executor(openai_endpoint(structured_output='json_object'), rec).run(task())
    assert rec.body()['response_format'] == {'type': 'json_object'}


def test_rate_limit_and_server_errors_retry_with_backoff():
    sleeps = []
    rec = Recorder((429, {'error': {'message': 'slow down'}}, {'retry-after': '7'}),
                   (503, {'error': {'message': 'unavailable'}}, None),
                   (200, fixture('openai_chat_completion.json'), None))
    result = executor(openai_endpoint(), rec, sleeps).run(task())
    assert result.status == 'completed' and sleeps == [7, 2]
    sleeps = []
    rec = Recorder(*[(500, {'error': {'message': 'boom'}}, None)] * 3)
    failed = executor(openai_endpoint(max_retries=2), rec, sleeps).run(task())
    assert failed.status == 'failed' and failed.error['code'] == 'HTTP_ERROR'
    assert failed.error['retryable'] is True and failed.error['http_status'] == 500
    assert len(rec.requests) == 3 and sleeps == [1, 2]


def test_context_length_is_chunk_too_large():
    rec = Recorder((400, fixture('openai_error_context_length.json'), None))
    result = executor(openai_endpoint(), rec).run(task())
    assert result.error['code'] == 'CHUNK_TOO_LARGE' and len(rec.requests) == 1
    rec = Recorder((400, fixture('anthropic_error_prompt_too_long.json'), None))
    result = executor(anthropic_endpoint(), rec).run(task())
    assert result.error['code'] == 'CHUNK_TOO_LARGE'


def test_truncated_or_non_json_answers_are_invalid_output():
    cut = copy.deepcopy(fixture('openai_chat_completion.json'))
    cut['choices'][0]['finish_reason'] = 'length'
    result = executor(openai_endpoint(), Recorder((200, cut, None))).run(task())
    assert result.error['code'] == 'INVALID_OUTPUT' and 'max_output_tokens' in result.error['message']
    prose = copy.deepcopy(fixture('openai_chat_completion.json'))
    prose['choices'][0]['message']['content'] = 'I cannot answer in JSON, sorry.'
    result = executor(openai_endpoint(), Recorder((200, prose, None))).run(task())
    assert result.error['code'] == 'INVALID_OUTPUT' and result.model_reported == 'gpt-test-2026-01-01'
    cut = copy.deepcopy(fixture('anthropic_message.json'))
    cut['stop_reason'] = 'max_tokens'
    result = executor(anthropic_endpoint(), Recorder((200, cut, None))).run(task())
    assert result.error['code'] == 'INVALID_OUTPUT'


def test_auth_errors_and_missing_keys_are_config_errors(monkeypatch):
    rec = Recorder((401, {'error': {'message': 'bad key'}}, None))
    result = executor(openai_endpoint(), rec).run(task())
    assert result.error['code'] == 'CONFIG_ERROR' and len(rec.requests) == 1
    monkeypatch.delenv('SB_TEST_PRODUCER_KEY')
    rec = Recorder()
    result = executor(openai_endpoint(), rec).run(task())
    assert result.error['code'] == 'CONFIG_ERROR' and rec.requests == []
    assert KEY not in json.dumps(result.error)


def test_anthropic_request_and_response_contract():
    rec = Recorder((200, fixture('anthropic_message.json'), None))
    result = executor(anthropic_endpoint(max_output_tokens=700), rec).run(task(role='reviewer', step='review'))
    request = rec.requests[0]
    assert str(request.url) == 'https://anthropic.example.test/v1/messages'
    assert request.headers['x-api-key'] == KEY + '-r' and request.headers['anthropic-version'] == '2023-06-01'
    assert 'authorization' not in request.headers
    body = rec.body()
    assert body['model'] == 'claude-test' and body['max_tokens'] == 700 and body['system']
    assert body['tools'][0]['input_schema'] == SCHEMA
    assert body['tool_choice'] == {'type': 'tool', 'name': 'submit_result'}
    assert '<task_packet>' in body['messages'][0]['content']
    assert result.output == {'ok': True} and result.model_reported == 'claude-test-2026-01-01'
    assert result.usage == {'input_tokens': 120, 'output_tokens': 9}
    rec = Recorder((200, fixture('anthropic_message.json'), None))
    executor(replace(anthropic_endpoint(), base_url='https://anthropic.example.test/v1/'), rec).run(task())
    assert str(rec.requests[0].url) == 'https://anthropic.example.test/v1/messages'


def test_anthropic_overload_retries_and_text_answers_are_parsed():
    text_answer = copy.deepcopy(fixture('anthropic_message.json'))
    text_answer['content'] = [{'type': 'text', 'text': '```json\n{"ok": true}\n```'}]
    sleeps = []
    rec = Recorder((529, fixture('anthropic_error_overloaded.json'), None), (200, text_answer, None))
    result = executor(anthropic_endpoint(), rec, sleeps).run(task())
    assert result.output == {'ok': True} and sleeps == [1]


def test_parse_json_text():
    assert parse_json_text('{"a": 1}') == {'a': 1}
    assert parse_json_text('Here it is:\n{"a": {"b": 2}}\nDone.') == {'a': {'b': 2}}
    assert parse_json_text('[1, 2]') is None and parse_json_text('no json') is None


def wrap_openai(output, model='gpt-test-2026-01-01'):
    body = copy.deepcopy(fixture('openai_chat_completion.json'))
    body['model'] = model
    body['choices'][0]['message']['content'] = json.dumps(output)
    return body


def wrap_anthropic(output, model='claude-test-2026-01-01'):
    body = copy.deepcopy(fixture('anthropic_message.json'))
    body['model'] = model
    body['content'][0]['input'] = output
    return body


def test_ingest_through_both_adapters(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    produced = [first_candidate(), repair_candidate()]
    reviewed = [first_review(), repair_review()]
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.host == 'llm.example.test':
            task = packet_in(json.loads(request.content)['messages'][1]['content'])
            if 'cards' in task:  # the diagram step, after review
                return httpx.Response(200, json=wrap_openai(diagram_answer(task, {'marker-dimming': DIMMING_IR})))
            return httpx.Response(200, json=wrap_openai(produced.pop(0)))
        return httpx.Response(200, json=wrap_anthropic(reviewed.pop(0)))
    settings = replace(config.current(), producer=openai_endpoint(), reviewer=anthropic_endpoint())
    config.activate(settings)
    producer, reviewer = build(settings, 'api', transport=httpx.MockTransport(handler))
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, prepared['job_id'], producer=producer, reviewer=reviewer)
    assert result['status'] == 'applied' and result['new_card_count'] == 3
    assert len(seen) == 5 and not produced and not reviewed
    assert [d['ref'] for d in result['diagrams'] if d['status'] == 'draw'] == ['marker-dimming']
    runs = jobs.list_runs(root, prepared['job_id'])
    assert {r['attestation'] for r in runs} == {'provider_response'}
    families = config.review_policy().model_families
    by_role = {}
    for run in runs:
        assert run['model_reported'] and run['endpoint_host'] in {'llm.example.test', 'anthropic.example.test'}
        by_role.setdefault(run['role'], set()).add(model_family(run['model_reported'], families))
    assert by_role == {'producer': {'openai'}, 'reviewer': {'anthropic'}}
    # the key never lands in the processing state
    for path in state_root(root).rglob('*'):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()


def test_doctor_live_check_uses_one_tiny_request_per_endpoint():
    rec = Recorder((200, fixture('openai_chat_completion.json'), None),
                   (200, fixture('anthropic_message.json'), None))
    settings = replace(config.Settings(), producer=openai_endpoint(), reviewer=anthropic_endpoint())
    report = cli.live_check(settings, transport=httpx.MockTransport(rec))
    assert report['producer']['status'] == 'completed' and report['producer']['output'] == {'ok': True}
    assert report['reviewer']['model_reported'] == 'claude-test-2026-01-01'
    assert len(rec.requests) == 2

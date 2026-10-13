"""Request/response mapping for OpenAI-compatible ``/chat/completions``."""
from __future__ import annotations

from .api import parse_json_text


def request(endpoint, key: str, system: str, text: str, schema: dict, *, mode: str):
    url = endpoint.base_url.rstrip('/') + '/chat/completions'
    body = {'model': endpoint.model, 'max_tokens': endpoint.max_output_tokens,
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': text}]}
    if mode == 'json_schema':
        body['response_format'] = {'type': 'json_schema',
                                   'json_schema': {'name': 'result', 'schema': schema, 'strict': False}}
    elif mode == 'json_object':
        body['response_format'] = {'type': 'json_object'}
    return url, {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}, body


def unsupported_format(status: int, detail: str) -> bool:
    return status in (400, 422) and ('response_format' in detail or 'json_schema' in detail)


def parse(data: dict):
    choice = (data.get('choices') or [{}])[0]
    message = choice.get('message') or {}
    usage = data.get('usage') or {}
    usage = {'input_tokens': usage.get('prompt_tokens'), 'output_tokens': usage.get('completion_tokens')}
    truncated = choice.get('finish_reason') == 'length'
    return parse_json_text(message.get('content') or ''), data.get('model'), usage, truncated

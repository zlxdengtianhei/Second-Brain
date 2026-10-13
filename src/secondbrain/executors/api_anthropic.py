"""Request/response mapping for Anthropic ``/v1/messages`` (tool-forced JSON)."""
from __future__ import annotations

from .api import parse_json_text

TOOL = 'submit_result'
VERSION = '2023-06-01'


def request(endpoint, key: str, system: str, text: str, schema: dict, *, mode: str):
    base = endpoint.base_url.rstrip('/')
    url = base + ('/messages' if base.endswith('/v1') else '/v1/messages')
    body = {'model': endpoint.model, 'max_tokens': endpoint.max_output_tokens, 'system': system,
            'messages': [{'role': 'user', 'content': text}]}
    if mode != 'plain':
        body['tools'] = [{'name': TOOL, 'description': 'Submit the JSON result of the task.',
                          'input_schema': schema}]
        body['tool_choice'] = {'type': 'tool', 'name': TOOL}
    headers = {'x-api-key': key, 'anthropic-version': VERSION, 'content-type': 'application/json'}
    return url, headers, body


def unsupported_format(status: int, detail: str) -> bool:
    return status == 400 and 'tool' in detail and 'not supported' in detail


def parse(data: dict):
    usage = data.get('usage') or {}
    usage = {'input_tokens': usage.get('input_tokens'), 'output_tokens': usage.get('output_tokens')}
    truncated = data.get('stop_reason') == 'max_tokens'
    output = None
    for block in data.get('content') or []:
        if block.get('type') == 'tool_use' and block.get('name') == TOOL:
            output = block.get('input')
            break
    if output is None:
        text = ''.join(b.get('text', '') for b in data.get('content') or [] if b.get('type') == 'text')
        output = parse_json_text(text)
    return output, data.get('model'), usage, truncated

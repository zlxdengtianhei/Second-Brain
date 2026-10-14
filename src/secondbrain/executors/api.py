"""HTTP executor for OpenAI-compatible and Anthropic endpoints (needs httpx).

Each run is one request with the shared task text. The controller records
the requested model; the model the provider reports in its response is
stored as ``model_reported`` (``attestation = provider_response``). Keys are
resolved per request from the environment or a command and never stored.

Failure semantics: 429/5xx and network errors retry with exponential backoff
(``max_retries``), a context-length error is ``CHUNK_TOO_LARGE``, an
unparseable or truncated answer is ``INVALID_OUTPUT``, an auth error is
``CONFIG_ERROR``.
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlparse

from ..config import EndpointConfig
from .base import (CHUNK_TOO_LARGE, CONFIG_ERROR, HTTP_ERROR, INVALID_OUTPUT, TIMEOUT, Result, Task,
                   render_task_text)

SYSTEM = ('You are a careful knowledge editor. Follow the task instructions exactly and answer with '
          'a single JSON object that matches the given schema.')
_CONTEXT = re.compile(r'context[_ ]length|maximum context|context window|too many tokens|'
                      r'prompt is too long|input is too long|reduce the length', re.I)


def _error(code, message, retryable=False, status=None) -> dict:
    return {'code': code, 'message': message[:2000], 'retryable': retryable, 'http_status': status}


def parse_json_text(text: str) -> dict | None:
    """The JSON object in a model's text answer (code fences tolerated)."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    fence = re.match(r'^```(?:json)?\s*(.*?)\s*```$', stripped, re.S)
    if fence:
        stripped = fence.group(1)
    try:
        value = json.loads(stripped)
    except ValueError:
        start, end = stripped.find('{'), stripped.rfind('}')
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(stripped[start:end + 1])
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


class ApiExecutor:
    name = 'api'

    def __init__(self, endpoint: EndpointConfig, *, transport=None, sleep=time.sleep):
        if not endpoint.base_url or not endpoint.model:
            raise ValueError('executor endpoint needs base_url and model')
        self.endpoint = endpoint
        self.model = endpoint.model
        self.endpoint_host = urlparse(endpoint.base_url).hostname
        self._transport = transport
        self._sleep = sleep
        from . import api_anthropic, api_openai
        self._adapter = api_openai if endpoint.kind == 'openai' else api_anthropic
        self._mode = 'json_schema' if endpoint.structured_output in {'auto', 'json_schema'} else 'json_object'

    def _client(self):
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - depends on the install
            from ..labels import install_hint
            raise RuntimeError(f'The API executor needs httpx: {install_hint("api")}') from exc
        return httpx.Client(timeout=self.endpoint.timeout_s, transport=self._transport)

    def run(self, task: Task) -> Result:
        import httpx
        try:
            key = self.endpoint.api_key()
        except (ValueError, OSError) as exc:
            return Result(status='failed', attestation='provider_response',
                          error=_error(CONFIG_ERROR, f'API key unavailable: {exc}'))
        text = render_task_text(task.prompt, task.packet, task.output_schema)
        attempt = 0
        with self._client() as client:
            while True:
                url, headers, body = self._adapter.request(self.endpoint, key, SYSTEM, text,
                                                           task.output_schema, mode=self._mode)
                try:
                    response = client.post(url, headers=headers, json=body)
                except httpx.TimeoutException as exc:
                    error = _error(TIMEOUT, f'request timed out: {type(exc).__name__}', True)
                except httpx.TransportError as exc:
                    error = _error(HTTP_ERROR, f'network error: {type(exc).__name__}', True)
                else:
                    status = response.status_code
                    detail = response.text[:2000]
                    if status == 200:
                        return self._parse(response)
                    if self._adapter.unsupported_format(status, detail) and self._mode != 'plain':
                        self._mode = 'json_object' if self._mode == 'json_schema' else 'plain'
                        continue
                    if status in (401, 403):
                        return Result(status='failed', attestation='provider_response',
                                      error=_error(CONFIG_ERROR, f'authentication failed ({status})', False, status))
                    if status in (400, 413, 422) and _CONTEXT.search(detail):
                        return Result(status='failed', attestation='provider_response',
                                      error=_error(CHUNK_TOO_LARGE, detail, False, status))
                    retryable = status == 429 or status >= 500
                    error = _error(HTTP_ERROR, f'HTTP {status}: {detail}', retryable, status)
                    if not retryable:
                        return Result(status='failed', attestation='provider_response', error=error)
                    retry_after = response.headers.get('retry-after')
                    if retry_after and retry_after.isdigit():
                        error['retry_after'] = int(retry_after)
                if attempt >= self.endpoint.max_retries:
                    return Result(status='failed', attestation='provider_response', error=error)
                self._sleep(min(60, error.get('retry_after') or 2 ** attempt))
                attempt += 1

    def _parse(self, response) -> Result:
        try:
            data = response.json()
        except ValueError:
            return Result(status='failed', attestation='provider_response',
                          error=_error(INVALID_OUTPUT, 'response body is not JSON', True))
        output, model, usage, truncated = self._adapter.parse(data)
        if truncated:
            return Result(status='failed', model_reported=model, attestation='provider_response', usage=usage,
                          error=_error(INVALID_OUTPUT, 'the answer was cut off at max_output_tokens; '
                                       'raise it or lower ingest.chunk_chars', True))
        if not isinstance(output, dict):
            return Result(status='failed', model_reported=model, attestation='provider_response', usage=usage,
                          error=_error(INVALID_OUTPUT, 'the answer is not a JSON object', True))
        return Result(status='completed', output=output, model_reported=model,
                      attestation='provider_response', usage=usage)

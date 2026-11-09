"""HTTP executor for OpenAI-compatible and Anthropic endpoints (needs httpx).

Each run is one request with the shared task text. The controller records
the requested model; the model the provider reports in its response is
stored as ``model_reported`` (``attestation = provider_response``). Keys are
resolved per request from the environment or a command and never stored.

Failure semantics: 429/5xx, auth and network errors, timeouts and empty
response bodies retry with exponential backoff (``max_retries``) inside the
executor. A context-length error is ``CHUNK_TOO_LARGE``; a nonempty
unparseable or truncated model answer is ``INVALID_OUTPUT``. Exhausted auth
errors are ``CONFIG_ERROR``; empty bodies remain transport ``HTTP_ERROR``s.
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlparse

from ..config import EndpointConfig
from ..pipelines.answerfix import strip_fence
from .base import (CHUNK_TOO_LARGE, CONFIG_ERROR, HTTP_ERROR, INVALID_OUTPUT, TIMEOUT, Result, Task,
                   render_task_text)

SYSTEM = ('You are a careful knowledge editor. Follow the task instructions exactly and answer with '
          'a single JSON object that matches the given schema.')
_CONTEXT = re.compile(r'context[_ ]length|maximum context|context window|too many tokens|'
                      r'prompt is too long|input is too long|reduce the length', re.I)


def _error(code, message, retryable=False, status=None) -> dict:
    return {'code': code, 'message': message[:2000], 'retryable': retryable, 'http_status': status}


def _candidates(text: str) -> list[tuple[str, str | None]]:
    """The readings of an answer to try, in order, each with what was set aside: the text as it is (or without one
    Markdown code fence around the whole of it, ``fence``), then the part from its first ``{`` to its last ``}``,
    when there are words before or after it (``around``)."""
    stripped, how = text.strip(), None
    inner = strip_fence(stripped)  # one Markdown code fence around the whole answer
    if inner is not None:
        stripped, how = inner.strip(), 'fence'
    out = [(stripped, how)]
    start, end = stripped.find('{'), stripped.rfind('}')
    if 0 <= start < end and (start, end) != (0, len(stripped) - 1):
        out.append((stripped[start:end + 1], 'around'))
    return out


def escape_tex(text: str) -> str:
    """``text`` with the backslashes of TeX commands doubled (for JSON written with raw formulas).

    A backslash before two or more ASCII letters (``\\mid``, ``\\text``, ``\\frac``) or before a
    character JSON cannot escape (``\\{``, ``\\,``) is doubled; ``\\\\``, ``\\"``, ``\\/``,
    ``\\uXXXX`` and a one-letter escape such as ``\\n`` stay.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch != '\\' or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1]
        if nxt in '\\"/':
            out.append(text[i:i + 2])
            i += 2
        elif nxt == 'u' and re.fullmatch(r'[0-9a-fA-F]{4}', text[i + 2:i + 6]):
            out.append(text[i:i + 6])
            i += 6
        elif re.match(r'[A-Za-z]{2}', text[i + 1:i + 3]) or nxt not in 'bfnrt':
            out.append('\\\\')
            i += 1
        else:
            out.append(text[i:i + 2])
            i += 2
    return ''.join(out)


def read_answer(text: str, *, tex_backslashes: bool = False) -> tuple[dict | None, str | None]:
    """``(the JSON object in a model's text answer, or None; what was set aside to read it)``: the one reading of an
    answer, for an API executor and for `sb job submit` alike. The text is read as JSON as it is; else without one
    Markdown code fence around the whole of it (``fence``); else the part from its first ``{`` to its last ``}``,
    when words stand before or after it (``around``). Nothing else is loosened: a literal ``\\n`` written for a line
    break is no JSON, and a JSON value that is not an object is None.

    With ``tex_backslashes`` (`sb export` only, :attr:`Task.tex_backslashes`), when strict parsing
    fails the answer is read once more with the backslashes of TeX commands doubled
    (:func:`escape_tex`): models often copy ``\\mid`` or ``\\ge`` into a JSON string unescaped. That
    reading is lossy (``\\n`` before a letter becomes a backslash and an ``n``), so the other steps
    never use it. A strict parse that succeeds is never re-read.
    """
    if not isinstance(text, str):
        return None, None
    for candidate, how in _candidates(text):
        for attempt in (candidate, escape_tex(candidate)) if tex_backslashes else (candidate,):
            try:
                value = json.loads(attempt)
            except ValueError:
                continue
            return (value, how) if isinstance(value, dict) else (None, None)
    return None, None


def json_problem(text: str) -> str:
    """Why ``text`` is not a JSON object: the parser's message and the text around the error."""
    if not isinstance(text, str) or not text.strip():
        return 'the answer is empty'
    candidate = _candidates(text)[-1][0]
    try:
        value = json.loads(candidate)
    except ValueError as exc:
        pos = getattr(exc, 'pos', 0) or 0
        return f'{exc}; near: {candidate[max(0, pos - 60):pos + 40]!r}'
    return f'the answer is JSON but not an object ({type(value).__name__})'


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
                    if status == 200 and detail.strip():
                        return self._parse(response, task)
                    if status == 200 or (status == 204 and not detail.strip()):
                        # No model answer arrived: retry here, never spend the controller's invalid-answer budget.
                        error = _error(HTTP_ERROR, f'empty response body (HTTP {status})', True, status)
                    else:
                        if self._adapter.unsupported_format(status, detail) and self._mode != 'plain':
                            self._mode = 'json_object' if self._mode == 'json_schema' else 'plain'
                            continue
                        if status in (400, 413, 422) and _CONTEXT.search(detail):
                            return Result(status='failed', attestation='provider_response',
                                          error=_error(CHUNK_TOO_LARGE, detail, False, status))
                        if status in (401, 403):
                            # A gateway can deny a request transiently; only the last denial is a config failure.
                            error = _error(CONFIG_ERROR, f'authentication failed ({status})', True, status)
                        else:
                            retryable = status == 429 or status >= 500
                            error = _error(HTTP_ERROR, f'HTTP {status}: {detail}', retryable, status)
                            if not retryable:
                                return Result(status='failed', attestation='provider_response', error=error)
                        retry_after = response.headers.get('retry-after')
                        if retry_after and retry_after.isdigit():
                            error['retry_after'] = int(retry_after)
                if attempt >= self.endpoint.max_retries:
                    if error['code'] == CONFIG_ERROR:
                        error['retryable'] = False
                    return Result(status='failed', attestation='provider_response', error=error)
                self._sleep(min(60, error.get('retry_after') or 2 ** attempt))
                attempt += 1

    def _parse(self, response, task: Task) -> Result:
        try:
            data = response.json()
        except ValueError:
            return Result(status='failed', attestation='provider_response',
                          error=_error(INVALID_OUTPUT, 'response body is not JSON', True))
        try:
            return self._read(data, task)
        except Exception as exc:  # noqa: BLE001 - reading the answer failed: counted at the gate, kept for the owner
            return Result(status='failed', attestation='provider_response',
                          error={**_error(INVALID_OUTPUT, f'the program failed while reading the answer: '
                                          f'{type(exc).__name__}: {exc}', True),
                                 'program_error': {'type': type(exc).__name__, 'message': str(exc)[:500]}})

    def _read(self, data, task: Task) -> Result:
        output, model, usage, truncated, how = self._adapter.parse(data, tex_backslashes=task.tex_backslashes)
        detail = self._adapter.answer_text(data) if hasattr(self._adapter, 'answer_text') else None
        if truncated:
            return Result(status='failed', model_reported=model, attestation='provider_response', usage=usage,
                          error=_error(INVALID_OUTPUT, 'the answer was cut off at max_output_tokens; '
                                       'raise it or lower ingest.chunk_chars', True), text=detail)
        if not isinstance(output, dict):
            return Result(status='failed', model_reported=model, attestation='provider_response', usage=usage,
                          error=_error(INVALID_OUTPUT, 'the answer is not a JSON object: '
                                       + json_problem(detail) if detail is not None
                                       else 'the answer is not a JSON object', True), text=detail)
        return Result(status='completed', output=output, model_reported=model,
                      attestation='provider_response', usage=usage, unwrapped=how, text=detail)

"""The single seam between the pipelines and any model.

A pipeline builds a :class:`Task` (prompt + frozen packet + output schema) and
hands it to an :class:`Executor`. Executors never read or write the vault and
never approve their own output: the controller records the run, validates the
output deterministically, and only the gate in :mod:`secondbrain.gates` lets a
reviewed result through to a transaction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

Role = Literal['producer', 'reviewer']
Step = Literal['produce', 'review', 'repair']
Attestation = Literal['provider_response', 'self_reported', 'test']

#: Error codes an executor may return in ``Result.error['code']``.
CHUNK_TOO_LARGE = 'CHUNK_TOO_LARGE'
INVALID_OUTPUT = 'INVALID_OUTPUT'
HTTP_ERROR = 'HTTP_ERROR'
TIMEOUT = 'TIMEOUT'
CONFIG_ERROR = 'CONFIG_ERROR'


@dataclass(frozen=True)
class Task:
    job_id: str
    run_id: str
    role: Role
    step: Step
    prompt: str
    packet: dict
    #: Hash of the frozen job input this packet was derived from.
    packet_sha256: str
    output_schema: dict
    chunk: str | None = None


@dataclass
class Result:
    status: Literal['completed', 'awaiting_submission', 'failed']
    output: dict | None = None
    model_reported: str | None = None
    attestation: Attestation = 'test'
    usage: dict | None = None
    #: ``{code, message, retryable}`` when ``status == 'failed'``.
    error: dict | None = None
    #: Host executor: where the packet is and how to submit the answer.
    handoff: dict | None = field(default=None)


@runtime_checkable
class Executor(Protocol):
    #: ``'api'``, ``'host'`` or ``'fake'``; recorded in every run record.
    name: str
    #: Requested model (may be ``None`` for the host executor).
    model: str | None
    #: Host part of the endpoint URL, recorded for audit; never the key.
    endpoint_host: str | None

    def run(self, task: Task) -> Result: ...


class ExecutorFailure(RuntimeError):
    """An executor returned ``failed``; carries the structured error."""

    def __init__(self, error: dict):
        self.error = dict(error or {})
        super().__init__(f"{self.error.get('code', 'EXECUTOR_FAILED')}: {self.error.get('message', '')}")

    @property
    def code(self) -> str:
        return self.error.get('code', 'EXECUTOR_FAILED')


def render_task_text(prompt: str, packet: dict, output_schema: dict | None = None) -> str:
    """The exact text every executor delivers: instructions, schema, packet.

    API executors send it as the user message; the host executor writes it to
    the packet file a sub-agent reads. One rendering keeps both identical.
    """
    import json
    parts = [prompt.rstrip(), '']
    if output_schema:
        parts += ['## Output JSON schema', '', '```json',
                  json.dumps(output_schema, ensure_ascii=False, indent=2), '```', '']
    parts += ['## Task packet', '', '<task_packet>',
              json.dumps(packet, ensure_ascii=False, separators=(',', ':')), '</task_packet>', '']
    return '\n'.join(parts)

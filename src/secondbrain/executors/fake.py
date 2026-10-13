"""Deterministic executor for tests and offline demos: no network, no model."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Callable

from .base import Result, Task


class FakeExecutor:
    """Return pre-recorded outputs.

    ``responder`` is a callable ``task -> dict | Result``; or ``outputs`` maps a
    key to an output dict. Keys are tried from most to least specific:
    ``"<role>:<chunk>:<step>"``, ``"<role>:<chunk>"``, ``"<role>:<step>"``,
    ``"<role>"`` (``chunk`` is ``-`` for unchunked jobs). A list value is
    consumed in order, one entry per call.
    """

    name = 'fake'

    def __init__(self, model: str = 'fake-model', *, responder: Callable | None = None,
                 outputs: dict | None = None, attestation: str = 'test',
                 model_reported: str | None = None):
        if responder is None and outputs is None:
            raise ValueError('FakeExecutor needs a responder or outputs')
        self.model = model
        self.endpoint_host = None
        self.responder = responder
        self.outputs = copy.deepcopy(outputs) if outputs is not None else None
        self.attestation = attestation
        self.model_reported = model_reported or model
        self.calls: list[Task] = []

    @classmethod
    def from_dir(cls, folder: Path, **kwargs) -> 'FakeExecutor':
        """Load ``<key>.json`` fixture files (``:`` in keys written as ``__``)."""
        outputs = {}
        for path in sorted(Path(folder).glob('*.json')):
            outputs[path.stem.replace('__', ':')] = json.loads(path.read_text(encoding='utf-8'))
        return cls(outputs=outputs, **kwargs)

    def _lookup(self, task: Task):
        chunk = task.chunk or '-'
        for key in (f'{task.role}:{chunk}:{task.step}', f'{task.role}:{chunk}',
                    f'{task.role}:{task.step}', task.role):
            if key in self.outputs:
                value = self.outputs[key]
                if isinstance(value, list):
                    if not value:
                        raise LookupError(f'FakeExecutor ran out of outputs for {key}')
                    return value.pop(0)
                return copy.deepcopy(value)
        raise LookupError(f'FakeExecutor has no output for {task.role}/{chunk}/{task.step}')

    def run(self, task: Task) -> Result:
        self.calls.append(task)
        value = self.responder(task) if self.responder is not None else self._lookup(task)
        if isinstance(value, Result):
            return value
        return Result(status='completed', output=value, model_reported=self.model_reported,
                      attestation=self.attestation, usage={'input_tokens': 0, 'output_tokens': 0})

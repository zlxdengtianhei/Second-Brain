"""Host executor: hand the task to a sub-agent in a coding-agent harness.

It never calls a model. The controller has already written the task text to
the run's packet file; this executor returns ``awaiting_submission`` with
where to read it, where to write the JSON answer and the command that submits
it. The model identity of a host run is whatever the submitter declares, so
these runs are recorded as ``self_reported``.
"""
from __future__ import annotations

import shlex
from pathlib import Path

from .base import Result, Task


class HostExecutor:
    name = 'host'
    model = None
    endpoint_host = None

    def __init__(self, command: str = 'sb', vault: str | None = None):
        self.command = command
        self.vault = vault

    def run(self, task: Task) -> Result:
        if not task.packet_path:
            raise ValueError('host executor needs the packet file path')
        packet = Path(task.packet_path)
        output = packet.parent.parent / 'incoming' / f'{task.run_id}.json'
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        prefix = [self.command] + (['--vault', self.vault] if self.vault else [])
        submit = prefix + ['job', 'submit', task.job_id, '--role', task.role, '--run-id', task.run_id,
                           '--file', str(output), '--model', '<model id>']
        return Result(status='awaiting_submission', attestation='self_reported', handoff={
            'job_id': task.job_id, 'run_id': task.run_id, 'role': task.role, 'chunk': task.chunk,
            'packet_path': str(packet), 'output_path': str(output),
            'submit_cmd': ' '.join(shlex.quote(x) if x != '<model id>' else x for x in submit)})

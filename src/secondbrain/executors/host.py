"""Host executor: hand the task to a sub-agent in a coding-agent harness.

It never calls a model. The controller has already written the task text to
the run's packet file; this executor returns ``awaiting_submission`` with
where to read it, where to write the JSON answer and the command that submits
it. The model identity of a host run is whatever the submitter declares, so
these runs are recorded as ``self_reported``. The submit command names the
program by absolute path, the vault, the config file and (when set by
environment) the state directory, so a sub-agent that copies it reaches the
same job even when its shell has no `sb` on the PATH.
"""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from .base import Result, Task


def program() -> list[str]:
    """This installation's `sb` by absolute path (next to the running Python), else ``python -m secondbrain``."""
    script = Path(sys.executable).parent / 'sb'
    if script.is_file() and os.access(script, os.X_OK):
        return [str(script)]
    return [sys.executable, '-m', 'secondbrain']


class HostExecutor:
    name = 'host'
    model = None
    endpoint_host = None

    def __init__(self, command: str | list | None = None, vault: str | None = None, config: str | None = None,
                 state_dir: str | None = None):
        self.command = [command] if isinstance(command, str) else list(command or program())
        self.vault = vault
        self.config = config
        self.state_dir = state_dir

    @classmethod
    def for_vault(cls, root) -> 'HostExecutor':
        """A host executor whose submit commands reach this vault, config file and state directory."""
        from .. import config
        settings = config.current()
        return cls(vault=str(root), config=str(settings.source) if settings.source else None,
                   state_dir=os.environ.get('SECONDBRAIN_STATE_DIR') or None)

    def run(self, task: Task) -> Result:
        if not task.packet_path:
            raise ValueError('host executor needs the packet file path')
        packet = Path(task.packet_path)
        output = packet.parent.parent / 'incoming' / f'{task.run_id}.json'
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        prefix = self.command + (['--vault', self.vault] if self.vault else []) + \
            (['--config', self.config] if self.config else [])
        submit = prefix + ['job', 'submit', task.job_id, '--role', task.role, '--run-id', task.run_id,
                           '--file', str(output), '--model', '<model id>']
        return Result(status='awaiting_submission', attestation='self_reported', handoff={
            'job_id': task.job_id, 'run_id': task.run_id, 'role': task.role, 'chunk': task.chunk,
            'packet_path': str(packet), 'output_path': str(output),
            'submit_cmd': self._env() + ' '.join(shlex.quote(x) if x != '<model id>' else x for x in submit)})

    def _env(self) -> str:
        # a sub-agent may not inherit the coordinator's environment
        return f'SECONDBRAIN_STATE_DIR={shlex.quote(self.state_dir)} ' if self.state_dir else ''

"""Pluggable executors: API (OpenAI-compatible / Anthropic), host hand-off, fake."""
from .base import (CHUNK_TOO_LARGE, CONFIG_ERROR, HTTP_ERROR, INVALID_OUTPUT, TIMEOUT, render_task_text,
                   Executor, ExecutorFailure, Result, Task)
from .fake import FakeExecutor

__all__ = ['render_task_text', 'CHUNK_TOO_LARGE', 'CONFIG_ERROR', 'HTTP_ERROR', 'INVALID_OUTPUT', 'TIMEOUT',
           'Executor', 'ExecutorFailure', 'FakeExecutor', 'Result', 'Task']

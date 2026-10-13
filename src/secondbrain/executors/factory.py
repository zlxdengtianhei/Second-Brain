"""Build the producer/reviewer executors from settings."""
from __future__ import annotations

from ..config import Settings
from ..gates import model_family


def build(settings: Settings, mode: str | None = None, *, vault: str | None = None, transport=None):
    mode = mode or settings.executor_default
    if mode == 'host':
        from .host import HostExecutor
        executor = HostExecutor(vault=vault)
        return executor, executor
    if mode != 'api':
        raise ValueError(f'unknown executor mode {mode!r}')
    if settings.producer is None or settings.reviewer is None:
        raise ValueError('API mode needs [executor.producer] and [executor.reviewer] in the config file')
    check_families(settings)
    from .api import ApiExecutor
    return (ApiExecutor(settings.producer, transport=transport),
            ApiExecutor(settings.reviewer, transport=transport))


def check_families(settings: Settings) -> dict:
    """Fail before spending a call when the configured pair cannot pass the gate."""
    policy = settings.review
    a = model_family(settings.producer.model, policy.model_families)
    b = model_family(settings.reviewer.model, policy.model_families)
    if policy.require_different_family:
        if a is None or b is None:
            missing = settings.producer.model if a is None else settings.reviewer.model
            raise ValueError(f'Unknown model family for {missing!r}; add it to [model_families]')
        if a == b:
            raise ValueError(f'Producer and reviewer models are both {a!r}; configure different families '
                             'or set [review] require_different_family = false')
    return {'producer_family': a, 'reviewer_family': b}

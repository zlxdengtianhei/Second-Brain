"""Runtime settings: state directory and the independent-review policy.

Processing state (jobs, run records, transaction journals, caches, locks) never
lives inside the vault; it is kept under ``$SECONDBRAIN_STATE_DIR`` or
``$XDG_STATE_HOME/secondbrain``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: Substring -> model family. Used only for the independent-review check
#: (producer and reviewer should come from different model families). Users
#: extend or override it in the ``[model_families]`` table of the config file.
DEFAULT_MODEL_FAMILIES = {
    'claude': 'anthropic',
    'gpt': 'openai',
    'o1': 'openai',
    'o3': 'openai',
    'o4': 'openai',
    'gemini': 'google',
    'gemma': 'google',
    'glm': 'zhipu',
    'deepseek': 'deepseek',
    'qwen': 'qwen',
    'kimi': 'moonshot',
    'moonshot': 'moonshot',
    'grok': 'xai',
    'llama': 'meta',
    'mistral': 'mistral',
    'mixtral': 'mistral',
}


@dataclass(frozen=True)
class ReviewPolicy:
    """How the producer/reviewer pair must relate before anything is written.

    ``require_different_family`` applies to runs whose model identity comes
    from the provider response (API executors, test executors). Runs whose
    model is only self-reported (host executor: a sub-agent in a coding-agent
    harness) may share a family when ``allow_same_family_in_host_mode`` is
    true; the job then records ``same_family: true`` so reports can show it.
    """
    require_different_family: bool = True
    allow_same_family_in_host_mode: bool = True
    withhold_producer_reasons: bool = True
    max_repair_rounds: int = 1
    model_families: dict = field(default_factory=lambda: dict(DEFAULT_MODEL_FAMILIES))


def state_dir() -> Path:
    """The tool's processing-state home (outside every vault)."""
    explicit = os.environ.get('SECONDBRAIN_STATE_DIR')
    if explicit:
        return Path(explicit).expanduser().absolute()
    state_home = Path(os.environ.get('XDG_STATE_HOME') or (Path.home() / '.local/state'))
    return (state_home / 'secondbrain').absolute()


def validate_state_dir(root: Path, folder: Path) -> None:
    target = folder.resolve()
    protected = root.resolve()
    if target == protected or target.is_relative_to(protected):
        raise ValueError('The state directory must not live inside the vault; '
                         'set SECONDBRAIN_STATE_DIR to a directory outside it')


_POLICY: ReviewPolicy | None = None


def review_policy() -> ReviewPolicy:
    return _POLICY or ReviewPolicy()


def set_review_policy(policy: ReviewPolicy | None) -> None:
    """Install the process-wide review policy (``None`` restores defaults)."""
    global _POLICY
    _POLICY = policy

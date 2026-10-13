"""Settings: TOML configuration, state directory and the review policy.

The config file is TOML (``$SECONDBRAIN_CONFIG``, else
``$XDG_CONFIG_HOME/secondbrain/config.toml``); every key has a default, so a
missing file is valid. API keys never appear in the file: an endpoint names an
environment variable (``api_key_env``) or a command that prints the key
(``api_key_cmd``). Processing state never lives inside the vault; it is kept
under ``$SECONDBRAIN_STATE_DIR`` or ``$XDG_STATE_HOME/secondbrain``.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass, field, fields
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 is unsupported
    tomllib = None

from .layout import VaultLayout

#: Substring -> model family. Used only for the independent-review check
#: (producer and reviewer should come from different model families). Users
#: extend or override it in the ``[model_families]`` table.
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


@dataclass(frozen=True)
class EndpointConfig:
    kind: str = 'openai'          # openai (any OpenAI-compatible endpoint) | anthropic
    base_url: str = ''
    model: str = ''
    api_key_env: str | None = None
    api_key_cmd: str | None = None
    max_output_tokens: int = 32000
    timeout_s: float = 600
    max_retries: int = 3
    structured_output: str = 'auto'   # auto | json_schema | json_object

    def __post_init__(self):
        if self.kind not in {'openai', 'anthropic'}:
            raise ValueError(f'executor kind must be "openai" or "anthropic", got {self.kind!r}')
        if self.structured_output not in {'auto', 'json_schema', 'json_object'}:
            raise ValueError('structured_output must be auto, json_schema or json_object')

    def api_key(self) -> str:
        """Resolve the key from the environment or a command; never cached or logged."""
        if self.api_key_env:
            value = os.environ.get(self.api_key_env)
            if value:
                return value.strip()
            raise ValueError(f'Environment variable {self.api_key_env} is not set')
        if self.api_key_cmd:
            done = subprocess.run(shlex.split(self.api_key_cmd), capture_output=True, text=True, timeout=30)
            if done.returncode != 0 or not done.stdout.strip():
                raise ValueError(f'api_key_cmd failed with exit code {done.returncode}')
            return done.stdout.strip()
        raise ValueError('Endpoint has neither api_key_env nor api_key_cmd')


@dataclass(frozen=True)
class IngestConfig:
    chunk_chars: int = 12000
    after_ingest: str = 'move'        # move | keep
    new_topics_per_source: int = 2
    converter: str = 'none'           # none | markitdown
    id_prefix: str = 'kc_'
    max_source_bytes: int = 8 * 1024 * 1024
    neighbors_per_card: int = 3
    candidate_domains: int = 3

    def __post_init__(self):
        if self.after_ingest not in {'move', 'keep'}:
            raise ValueError('ingest.after_ingest must be "move" or "keep"')
        if self.converter not in {'none', 'markitdown'}:
            raise ValueError('ingest.converter must be "none" or "markitdown"')
        if not 1000 <= int(self.chunk_chars) <= 400000:
            raise ValueError('ingest.chunk_chars must be between 1000 and 400000')


@dataclass(frozen=True)
class Settings:
    vault: Path | None = None
    layout: VaultLayout = field(default_factory=VaultLayout)
    executor_default: str = 'api'     # api | host
    producer: EndpointConfig | None = None
    reviewer: EndpointConfig | None = None
    review: ReviewPolicy = field(default_factory=ReviewPolicy)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    card_language: str = 'en'         # en | zh (card section headings)
    taxonomy_extra_reviewers: int = 0
    git_commit: bool = False
    state_dir: Path | None = None
    source: Path | None = None        # the file these settings came from


def _section(data: dict, name: str) -> dict:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f'[{name}] must be a table')
    return value


def _dataclass(cls, values: dict, name: str):
    known = {f.name for f in fields(cls)}
    unknown = sorted(set(values) - known)
    if 'api_key' in values:
        raise ValueError(f'[{name}] must not contain api_key; use api_key_env or api_key_cmd')
    if unknown:
        raise ValueError(f'Unknown [{name}] keys: {unknown}')
    return cls(**values)


def parse(data: dict, *, source: Path | None = None) -> Settings:
    top = {'vault', 'layout', 'executor', 'review', 'model_families', 'ingest', 'cards',
           'taxonomy', 'git', 'state_dir'}
    unknown = sorted(set(data) - top)
    if unknown:
        raise ValueError(f'Unknown config sections or keys: {unknown}')
    executor = dict(_section(data, 'executor'))
    endpoints = {role: executor.pop(role, None) for role in ('producer', 'reviewer')}
    default = executor.pop('default', 'api')
    if executor:
        raise ValueError(f'Unknown [executor] keys: {sorted(executor)}')
    if default not in {'api', 'host'}:
        raise ValueError('[executor] default must be "api" or "host"')
    families = dict(DEFAULT_MODEL_FAMILIES)
    families.update({str(k): str(v) for k, v in _section(data, 'model_families').items()})
    review = _dataclass(ReviewPolicy, {**_section(data, 'review'), 'model_families': families}, 'review')
    cards = _section(data, 'cards')
    if set(cards) - {'language'}:
        raise ValueError(f'Unknown [cards] keys: {sorted(set(cards) - {"language"})}')
    language = cards.get('language', 'en')
    if language not in {'en', 'zh'}:
        raise ValueError('[cards] language must be "en" or "zh"')
    taxonomy = _section(data, 'taxonomy')
    git = _section(data, 'git')
    vault = data.get('vault')
    state = data.get('state_dir')
    return Settings(
        vault=Path(vault).expanduser().absolute() if vault else None,
        layout=VaultLayout.from_mapping(_section(data, 'layout')),
        executor_default=default,
        producer=_dataclass(EndpointConfig, endpoints['producer'], 'executor.producer') if endpoints['producer'] else None,
        reviewer=_dataclass(EndpointConfig, endpoints['reviewer'], 'executor.reviewer') if endpoints['reviewer'] else None,
        review=review,
        ingest=_dataclass(IngestConfig, _section(data, 'ingest'), 'ingest'),
        card_language=language,
        taxonomy_extra_reviewers=int(taxonomy.get('extra_reviewers', 0)),
        git_commit=bool(git.get('commit', False)),
        state_dir=Path(state).expanduser().absolute() if state else None,
        source=source,
    )


def config_path() -> Path:
    explicit = os.environ.get('SECONDBRAIN_CONFIG')
    if explicit:
        return Path(explicit).expanduser()
    home = Path(os.environ.get('XDG_CONFIG_HOME') or (Path.home() / '.config'))
    return home / 'secondbrain' / 'config.toml'


def load(path: Path | None = None) -> Settings:
    path = Path(path) if path is not None else config_path()
    if not path.exists():
        return Settings()
    with open(path, 'rb') as handle:
        data = tomllib.load(handle)
    return parse(data, source=path.absolute())


_CURRENT: Settings | None = None
_POLICY: ReviewPolicy | None = None


def current() -> Settings:
    """The active settings (loaded once from the config file)."""
    global _CURRENT
    if _CURRENT is None:
        _CURRENT = load()
    return _CURRENT


def activate(settings: Settings | None) -> Settings | None:
    """Make ``settings`` the process-wide configuration (``None`` resets)."""
    global _CURRENT, _POLICY
    _CURRENT = settings
    _POLICY = None
    if settings is not None and settings.vault is not None:
        from . import layout
        layout.register(settings.vault, settings.layout)
    return settings


def state_dir() -> Path:
    """The tool's processing-state home (outside every vault)."""
    explicit = os.environ.get('SECONDBRAIN_STATE_DIR')
    if explicit:
        return Path(explicit).expanduser().absolute()
    if _CURRENT is not None and _CURRENT.state_dir is not None:
        return _CURRENT.state_dir
    state_home = Path(os.environ.get('XDG_STATE_HOME') or (Path.home() / '.local/state'))
    return (state_home / 'secondbrain').absolute()


def validate_state_dir(root: Path, folder: Path) -> None:
    target = folder.resolve()
    protected = root.resolve()
    if target == protected or target.is_relative_to(protected):
        raise ValueError('The state directory must not live inside the vault; '
                         'set SECONDBRAIN_STATE_DIR to a directory outside it')


def review_policy() -> ReviewPolicy:
    if _POLICY is not None:
        return _POLICY
    return current().review


def set_review_policy(policy: ReviewPolicy | None) -> None:
    """Override the review policy for this process (``None`` restores config)."""
    global _POLICY
    _POLICY = policy


EXAMPLE = '''\
# SecondBrain configuration (TOML). Every key is optional.
vault = "~/Obsidian/MyVault"

[layout]            # folder names inside the vault; defaults shown
knowledge = "10_Knowledge"
mocs = "20_MOCs"
taxonomy = "20_MOCs/_pillars"
sources = "40_Sources"
inbox = "00_Inbox"
reviews = "50_Reviews"
data = "_data"

[executor]
default = "api"     # api | host

[executor.producer]
kind = "openai"     # openai (any OpenAI-compatible endpoint) | anthropic
base_url = "https://api.example.com/v1"
model = "producer-model-name"
api_key_env = "SB_PRODUCER_API_KEY"
max_output_tokens = 32000
timeout_s = 600

[executor.reviewer]  # use a model of a different family than the producer
kind = "anthropic"
base_url = "https://api.anthropic.com"
model = "reviewer-model-name"
api_key_env = "SB_REVIEWER_API_KEY"

[review]
require_different_family = true
allow_same_family_in_host_mode = true
withhold_producer_reasons = true
max_repair_rounds = 1

[model_families]    # substring -> family, merged over the built-in table
# my-local-model = "local"

[ingest]
chunk_chars = 12000
after_ingest = "move"        # move | keep
new_topics_per_source = 2
converter = "none"           # none | markitdown
id_prefix = "kc_"

[cards]
language = "en"              # en | zh (section headings of rendered cards)

[taxonomy]
extra_reviewers = 0

[git]
commit = false
'''

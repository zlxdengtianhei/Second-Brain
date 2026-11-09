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
from dataclasses import dataclass, field, fields, replace
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
    #: Content repair rounds per chunk (and per split card), and format retries per slot: an answer that failed
    #: the program's checks asked for again. The two are counted apart.
    max_repair_rounds: int = 2
    max_invalid_retries: int = 2
    #: A repair round's reviewer judges whether each earlier finding is fixed, and sends a card back otherwise only
    #: for a blocking problem; other problems go to ``notes``.
    focused_review: bool = True
    #: The reviewer may give mechanical fixes with an accept (``amend``: relations, refiling under an existing
    #: topic, dropping a review card), applied by the program without a repair round.
    amend: bool = True
    model_families: dict = field(default_factory=lambda: dict(DEFAULT_MODEL_FAMILIES))

    def __post_init__(self):
        for name in ('max_repair_rounds', 'max_invalid_retries'):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 10:
                raise ValueError(f'review.{name} must be a whole number from 0 to 10')
        if not isinstance(self.focused_review, bool) or not isinstance(self.amend, bool):
            raise ValueError('review.focused_review and review.amend must be true or false')


@dataclass(frozen=True)
class EndpointConfig:
    kind: str = 'openai'          # openai (any OpenAI-compatible endpoint) | anthropic
    base_url: str = ''
    model: str = ''
    api_key_env: str | None = None
    api_key_cmd: str | None = None
    max_output_tokens: int = 32000
    timeout_s: float = 600
    max_retries: int = 2
    structured_output: str = 'auto'   # auto | json_schema | json_object

    def __post_init__(self):
        if self.kind not in {'openai', 'anthropic'}:
            raise ValueError(f'executor kind must be "openai" or "anthropic", got {self.kind!r}')
        if self.structured_output not in {'auto', 'json_schema', 'json_object'}:
            raise ValueError('structured_output must be auto, json_schema or json_object')
        if not isinstance(self.max_retries, int) or isinstance(self.max_retries, bool) or \
                not 0 <= self.max_retries <= 10:
            raise ValueError('executor max_retries must be a whole number from 0 to 10')

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


#: The arms of the context note (see secondbrain.pipelines.notes).
CONTEXT_NOTE_MODES = ('off', 'program', 'summary', 'concepts')


@dataclass(frozen=True)
class IngestConfig:
    chunk_chars: int = 12000
    after_ingest: str = 'move'        # move | keep
    new_topics_per_source: int = 2
    converter: str = 'none'           # none | markitdown
    id_prefix: str = 'kc_'
    max_source_bytes: int = 8 * 1024 * 1024
    candidate_domains: int = 3
    #: Candidate cards (secondbrain.retrieval): first-route cards per query, the same for every role (15: on
    #: vaults of 197 to 8,738 cards no role found more with more; "auto": each role's count before 2026-11),
    #: the characters of retrieved card views a packet may hold, and the second route's start points (off:
    #: linked cards found no more repeats).
    candidates_k: object = 15
    candidates_chars: int = 40000
    candidates_hop: int = 0
    #: Shelves (every card of a topic, by title, at most 25; packed last): in the producer packet, those of the
    #: topics its search hits are filed under; in the reviewer packet, that of each proposed card's topic.
    candidates_shelf: bool = True
    #: The reviewer packet holds every existing card the producer named (relations, coverage rows, conflicts,
    #: overlap hints), whole; and the view of the cards the search found: "brief" (decision D-17: an Opus
    #: reviewer found as much as with "full", reading less) or "full". Set "full" when a Flash model reviews:
    #: in H4 a DeepSeek reviewer found about 6 points fewer injected errors with "brief".
    review_named: bool = True
    review_words_view: str = 'brief'
    #: A card that extends an existing card goes into it (its propositions, review cards, source and relations),
    #: or is rewritten as a narrower card when that card would grow too long. Off: it is written next to the
    #: existing card, needs-review, as before.
    merge_into_existing: bool = True
    #: The note on where each chunk sits in its source (secondbrain.pipelines.notes): off, program (the
    #: program's, from the source's structure), summary or concepts (plus a cheap model call); its length.
    context_note: str = 'program'
    context_note_chars: int = 2000

    def __post_init__(self):
        if self.after_ingest not in {'move', 'keep'}:
            raise ValueError('ingest.after_ingest must be "move" or "keep"')
        if self.converter not in {'none', 'markitdown'}:
            raise ValueError('ingest.converter must be "none" or "markitdown"')
        if not 1000 <= int(self.chunk_chars) <= 400000:
            raise ValueError('ingest.chunk_chars must be between 1000 and 400000')
        if self.candidates_k != 'auto' and not (isinstance(self.candidates_k, int) and 1 <= self.candidates_k <= 100):
            raise ValueError('ingest.candidates_k must be "auto" or a whole number from 1 to 100')
        if not (isinstance(self.candidates_chars, int) and 1000 <= self.candidates_chars <= 1_000_000):
            raise ValueError('ingest.candidates_chars must be a whole number from 1000 to 1000000')
        if not (isinstance(self.candidates_hop, int) and 0 <= self.candidates_hop <= 10):
            raise ValueError('ingest.candidates_hop must be a whole number from 0 to 10')
        if not isinstance(self.candidates_shelf, bool) or not isinstance(self.review_named, bool):
            raise ValueError('ingest.candidates_shelf and ingest.review_named must be true or false')
        if not isinstance(self.merge_into_existing, bool):
            raise ValueError('ingest.merge_into_existing must be true or false')
        if self.review_words_view not in {'full', 'brief'}:
            raise ValueError('ingest.review_words_view must be "full" or "brief"')
        if self.context_note not in CONTEXT_NOTE_MODES:
            raise ValueError(f'ingest.context_note must be one of {", ".join(CONTEXT_NOTE_MODES)}')
        if not (isinstance(self.context_note_chars, int) and 200 <= self.context_note_chars <= 20000):
            raise ValueError('ingest.context_note_chars must be a whole number from 200 to 20000')


#: How `sb export` writes: unit after unit, each writer seeing the end of the unit before (the default; in the
#: export experiment no worse than writing all at once and the safer default), or every unit at once.
EXPORT_MODES = ('sequential', 'parallel')


@dataclass(frozen=True)
class ExportConfig:
    mode: str = 'sequential'
    #: The models that write and review articles; unset, `sb export` uses [executor.producer] and
    #: [executor.reviewer]. The owner picks them ([export.writer], [export.reviewer]).
    writer: 'EndpointConfig | None' = None
    reviewer: 'EndpointConfig | None' = None

    def __post_init__(self):
        if self.mode not in EXPORT_MODES:
            raise ValueError(f'export.mode must be one of {", ".join(EXPORT_MODES)}')


@dataclass(frozen=True)
class Settings:
    vault: Path | None = None
    layout: VaultLayout = field(default_factory=VaultLayout)
    executor_default: str = 'api'     # api | host
    producer: EndpointConfig | None = None
    reviewer: EndpointConfig | None = None
    review: ReviewPolicy = field(default_factory=ReviewPolicy)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    card_language: str = 'en'         # en | zh (card headings and the text of generated pages)
    card_language_set: bool = False   # the config file sets [cards] language (else the vault's is used)
    card_diagrams: bool = True        # run the diagram step after review (secondbrain.diagrams)
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
           'taxonomy', 'git', 'state_dir', 'export'}
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
    if set(cards) - {'language', 'diagrams'}:
        raise ValueError(f'Unknown [cards] keys: {sorted(set(cards) - {"language", "diagrams"})}')
    language = cards.get('language', 'en')
    if language not in {'en', 'zh'}:
        raise ValueError('[cards] language must be "en" or "zh"')
    diagrams = cards.get('diagrams', True)
    if not isinstance(diagrams, bool):
        raise ValueError('[cards] diagrams must be true or false')
    taxonomy = _section(data, 'taxonomy')
    git = _section(data, 'git')
    export = dict(_section(data, 'export'))
    models = {role: export.pop(role, None) for role in ('writer', 'reviewer')}
    export = _dataclass(ExportConfig, {**export, **{role: _dataclass(EndpointConfig, value, f'export.{role}')
                                                    for role, value in models.items() if value}}, 'export')
    vault = data.get('vault')
    state = data.get('state_dir')
    return Settings(
        vault=Path(vault).expanduser().absolute() if vault else None,
        layout=VaultLayout.from_mapping(_section(data, 'layout')),
        executor_default=default,
        producer=_dataclass(EndpointConfig, endpoints['producer'], 'executor.producer') if endpoints['producer'] else None,
        reviewer=_dataclass(EndpointConfig, endpoints['reviewer'], 'executor.reviewer') if endpoints['reviewer'] else None,
        review=review,
        ingest=_dataclass(IngestConfig, _ingest_keys(_section(data, 'ingest')), 'ingest'),
        export=export,
        card_language=language,
        card_language_set='language' in cards,
        card_diagrams=diagrams,
        taxonomy_extra_reviewers=int(taxonomy.get('extra_reviewers', 0)),
        git_commit=bool(git.get('commit', False)),
        state_dir=Path(state).expanduser().absolute() if state else None,
        source=source,
    )


#: ``[ingest]`` keys of earlier versions that are still read, and ignored, for one version.
RETIRED_INGEST_KEYS = ('neighbors_per_card',)


def _ingest_keys(values: dict) -> dict:
    """``[ingest]`` without retired keys (``neighbors_per_card`` was never read; see ``candidates_k``)."""
    return {k: v for k, v in values.items() if k not in RETIRED_INGEST_KEYS}


def config_path() -> Path:
    explicit = os.environ.get('SECONDBRAIN_CONFIG')
    if explicit:
        return Path(explicit).expanduser()
    home = Path(os.environ.get('XDG_CONFIG_HOME') or (Path.home() / '.config'))
    return home / 'secondbrain' / 'config.toml'


def load(path: Path | None = None) -> Settings:
    return _read(path)


def _read(path: Path | None = None) -> Settings:
    path = Path(path) if path is not None else config_path()
    if not path.exists():
        return Settings()
    with open(path, 'rb') as handle:
        data = tomllib.load(handle)
    return parse(data, source=path.absolute())


_CURRENT: Settings | None = None
_POLICY: ReviewPolicy | None = None
#: The frozen settings of the job step that is running (:func:`job_settings`), else None.
_JOB: Settings | None = None


def current() -> Settings:
    """The active settings (loaded once from the config file)."""
    global _CURRENT
    if _CURRENT is None:
        _CURRENT = _read()
    return _CURRENT


@dataclass(frozen=True)
class Runtime:
    """What a job step may still read from the live configuration: how to run, never what to decide.

    The endpoints and keys of the executors (switching a key must not stall a
    job), the config file a host hand-off names in its submit command, git
    commits (made after the write, outside its transaction) and where job
    records are kept.
    """
    producer: EndpointConfig | None
    reviewer: EndpointConfig | None
    executor_default: str
    source: Path | None
    git_commit: bool
    state_dir: Path | None


def runtime() -> Runtime:
    """The live settings a job step may read (see :class:`Runtime`); every other setting is frozen with the job."""
    global _CURRENT
    if _CURRENT is None:
        _CURRENT = _read()
    live = _CURRENT
    return Runtime(producer=live.producer, reviewer=live.reviewer, executor_default=live.executor_default,
                   source=live.source, git_commit=live.git_commit, state_dir=live.state_dir)


def in_job() -> bool:
    """True inside :func:`job_settings`: a job step is running on frozen settings."""
    return _JOB is not None


def active() -> Settings:
    """The settings to read: the running job step's frozen ones (:func:`job_settings`), else the configured ones."""
    return _JOB if _JOB is not None else current()


class job_settings:
    """``with config.job_settings(frozen):`` -- the steps inside read ``frozen`` through :func:`active`.

    Made from a job's frozen input (``secondbrain.jobs.frozen_settings``); a job
    step reads its vault layout, card language and review policy only there.
    """

    def __init__(self, frozen: Settings | None):
        self.frozen, self.before = frozen, None

    def __enter__(self):
        global _JOB
        self.before, _JOB = _JOB, (self.frozen if self.frozen is not None else _JOB)
        return self.frozen

    def __exit__(self, *exc):
        global _JOB
        _JOB = self.before
        return False


def activate(settings: Settings | None) -> Settings | None:
    """Make ``settings`` the process-wide configuration (``None`` resets)."""
    global _CURRENT, _POLICY
    _CURRENT = settings
    _POLICY = None
    if settings is not None and settings.vault is not None:
        from . import layout
        layout.register(settings.vault, settings.layout)
    return settings


def use_language(language: str) -> None:
    """Make ``language`` the active generated-text language (keeps the review-policy override)."""
    global _CURRENT
    _CURRENT = replace(current(), card_language=language)


def state_dir() -> Path:
    """The tool's processing-state home (outside every vault)."""
    explicit = os.environ.get('SECONDBRAIN_STATE_DIR')
    if explicit:
        return Path(explicit).expanduser().absolute()
    configured = runtime().state_dir
    if configured is not None:
        return configured
    state_home = Path(os.environ.get('XDG_STATE_HOME') or (Path.home() / '.local/state'))
    return (state_home / 'secondbrain').absolute()


def validate_state_dir(root: Path, folder: Path) -> None:
    target = folder.resolve()
    protected = root.resolve()
    if target == protected or target.is_relative_to(protected):
        raise ValueError('The state directory must not live inside the vault; '
                         'set SECONDBRAIN_STATE_DIR to a directory outside it')


def active_review() -> ReviewPolicy:
    """The review rules to apply: the running job step's frozen ones, else :func:`review_policy` (with an override)."""
    if _JOB is not None:
        return _JOB.review
    return _POLICY if _POLICY is not None else current().review


def review_policy() -> ReviewPolicy:
    """The review rules: inside a job step (:func:`job_settings`), the ones the job froze, so a step never reads
    the live configuration by mistake; elsewhere the configured ones, or the override of :func:`set_review_policy`."""
    if _JOB is not None:
        return _JOB.review
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
articles = "60_Articles"  # where `sb export` writes articles
entry_docs = []     # top-level notes whose generated blocks `sb docs` refreshes, e.g. ["Guide.md"]

[executor]
default = "api"     # api | host

[executor.producer]
kind = "openai"     # openai (any OpenAI-compatible endpoint) | anthropic
base_url = "https://api.example.com/v1"
model = "producer-model-name"
api_key_env = "SB_PRODUCER_API_KEY"
max_output_tokens = 32000
timeout_s = 600
max_retries = 2               # transport retries inside the executor; these do not use max_invalid_retries

[executor.reviewer]  # use a model of a different family than the producer
kind = "anthropic"
base_url = "https://api.anthropic.com"
model = "reviewer-model-name"
api_key_env = "SB_REVIEWER_API_KEY"

[review]
require_different_family = true
allow_same_family_in_host_mode = true
withhold_producer_reasons = true
max_repair_rounds = 2              # content repair rounds per chunk (D-18: every chunk of H8 settled in 1)
max_invalid_retries = 2            # an answer that failed the program's checks, asked for again (per slot)
focused_review = true              # a repair round's reviewer checks the earlier findings; other problems block only if serious
amend = true                       # the reviewer may fix relations, the topic or a review card itself (D-18)

[model_families]    # substring -> family, merged over the built-in table
# my-local-model = "local"

[ingest]
chunk_chars = 12000
after_ingest = "move"        # move | keep
new_topics_per_source = 2
converter = "none"           # none | markitdown
id_prefix = "kc_"
candidates_k = 15            # existing cards per query, every role, any vault size (D-10; "auto": the old counts)
candidates_chars = 40000     # characters of retrieved card views per packet (named cards do not count; D-17)
candidates_hop = 0           # second route: the cards the first N hits link (0: off; D-10)
                             # the search covers the whole vault, not only related domains (D-10)
candidates_shelf = true      # shelves by title: the producer's hits' topics, the reviewer's card's topic
review_named = true          # the reviewer sees, whole, every existing card the producer named
review_words_view = "brief"  # brief | full: the cards the search found, to the reviewer (full with a Flash reviewer:
                             # in H4 a DeepSeek reviewer found about 6 points fewer errors with brief)
merge_into_existing = true   # a card that only adds to an existing card goes into it (false: written next to it)
context_note = "program"     # off | program | summary | concepts: the note on where a chunk sits in its source (D-16)
context_note_chars = 2000    # the note's length

[cards]
# language = "en"            # en | zh (card headings; text of maps, HOME and review pages).
                             # Unset: the vault's language (`sb language`); English for a new vault.
diagrams = true              # one extra model call per 10 new cards; Mermaid is generated by code

[taxonomy]
extra_reviewers = 0

[git]
commit = false

[export]
mode = "sequential"          # sequential | parallel: write unit after unit, or every unit at once
# The models that write and review articles; unset, [executor.producer] and [executor.reviewer] are used.
# [export.writer]
# kind = "openai"
# base_url = "https://api.example.com/v1"
# model = "writer-model"
# api_key_env = "SB_WRITER_API_KEY"
# [export.reviewer]           # a model of a different family than the writer
# kind = "anthropic"
# base_url = "https://api.anthropic.com"
# model = "reviewer-model"
# api_key_env = "SB_REVIEWER_API_KEY"
'''

"""Vault side of the review registry: card metadata in, reports and log lines out.

:mod:`secondbrain.review_registry` never parses the vault. This module builds
its inputs from a vault snapshot (card metadata, taxonomy order, card text)
and decides where its files live: logs in ``<data>/review/`` inside the vault,
the replay cache in the tool's state directory. The CLI and the MCP server
both call these functions.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import cards as cardfmt
from . import config
from . import layout as layouts
from .store import state_root
from .vault import read_snapshot

__all__ = ['cache_dir', 'card_metas', 'import_sr', 'log', 'now_local', 'parse_item', 'queue', 'review_dir',
           'status', 'taxonomy']


def _registry():
    try:
        from . import review_registry
    except ImportError as exc:  # fsrs is an optional dependency
        raise ValueError('Review commands need the review extra: pip install "secondbrain[review]"') from exc
    return review_registry


def review_dir(root: Path) -> Path:
    return Path(root) / layouts.of(root).review_log_dir


def cache_dir(root: Path) -> Path | None:
    """Replay cache: ``<state dir>/vaults/<vault hash>/review-cache/``, never inside the vault."""
    try:
        return state_root(root).parent / 'review-cache'
    except (OSError, ValueError):
        return None


def default_section() -> str:
    return cardfmt.HEADINGS[config.current().card_language]['flashcards']


def card_metas(snapshot) -> list:
    rr = _registry()
    out = []
    for card in snapshot.cards.values():
        fm = card.fm
        relations = tuple((r['type'], r['target']) for r in fm.get('relations') or []
                          if isinstance(r, dict) and isinstance(r.get('type'), str)
                          and isinstance(r.get('target'), str))
        out.append(rr.CardMeta(id=card.id, title=card.title or '', domain=card.domain or '',
                               kind=str(fm.get('kind') or ''), status=str(fm.get('status') or ''),
                               path=card.path, relations=relations, pillar=str(fm.get('pillar') or ''),
                               topic=str(fm.get('topic') or '')))
    return out


def taxonomy(snapshot):
    lay = snapshot.layout or layouts.DEFAULT
    docs = [(lay.taxonomy_path(moc), data) for moc, data in sorted(snapshot.taxonomies.items())]
    return _registry().build_taxonomy(docs)


def now_local(value: str | None = None) -> datetime:
    """``value`` (ISO 8601 with offset) or the current local time, timezone-aware."""
    if value is None:
        return datetime.now().astimezone()
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('--now needs a UTC offset, for example 2026-10-13T09:00:00+02:00')
    return parsed


def status(root: Path, now: datetime) -> dict:
    snapshot = read_snapshot(root)
    return _registry().status_report(review_dir(root), card_metas(snapshot), now, cache_dir=cache_dir(root))


def queue(root: Path, now: datetime, *, new_per_day: int | None = None, domains=(), limit: int | None = None,
          questions: bool = False, section: str | None = None) -> dict:
    rr = _registry()
    snapshot = read_snapshot(root)

    def read_card(meta):
        return snapshot.cards[meta.id].body
    return rr.queue_report(review_dir(root), card_metas(snapshot), now,
                           new_per_day=rr.DEFAULT_NEW_PER_DAY if new_per_day is None else new_per_day,
                           domains=tuple(domains or ()), read_card=read_card if questions else None,
                           limit=limit, section=section or default_section(), taxonomy=taxonomy(snapshot),
                           cache_dir=cache_dir(root))


def parse_item(text: str) -> dict:
    """``<question text or h:id>=<rating 1-4>[:<hit|partial|miss>]`` -> event item."""
    rr = _registry()
    question, sep, rest = text.rpartition('=')
    if not sep or not question.strip():
        raise ValueError(f'--item must look like "question=3" or "h:1a2b3c4d=3:hit", got {text!r}')
    rating_text, _, verdict = rest.partition(':')
    if not rating_text.isdigit() or not 1 <= int(rating_text) <= 4:
        raise ValueError(f'item rating must be 1-4, got {rating_text!r}')
    question = question.strip()
    item = {'q': question if question.startswith('h:') else rr.question_id(question), 'rating': int(rating_text)}
    if verdict:
        if verdict not in {'hit', 'partial', 'miss'}:
            raise ValueError(f'item verdict must be hit, partial or miss, got {verdict!r}')
        item['verdict'] = verdict
    return item


def log(root: Path, card: str, now: datetime, *, rating: int | None = None, items=(), grader: str | None = None,
        device: str | None = None, fmt: str = 'jsonl') -> dict:
    rr = _registry()
    snapshot = read_snapshot(root)
    if card not in snapshot.cards:
        raise ValueError(f'No card with id {card!r}')
    if rating is None and not items:
        raise ValueError('give --rating or at least one --item')
    return rr.log_review(review_dir(root), card, device=device or rr.default_device_id(), now=now,
                         mode='agent', rating=rating, items=list(items) or None, grader=grader, fmt=fmt,
                         cache_dir=cache_dir(root))


def import_sr(root: Path, now: datetime, *, device: str | None = None, fmt: str = 'jsonl',
              dry_run: bool = False) -> dict:
    """One-time import of Spaced Repetition schedules found in card files (cards are not changed)."""
    rr = _registry()
    snapshot = read_snapshot(root)
    metas = {m.id: m for m in card_metas(snapshot)}
    rows = [(metas[c.id], c.raw.decode('utf-8', errors='replace'), c.fm) for c in snapshot.cards.values()]
    return rr.import_sr(review_dir(root), rows, device=device or rr.default_device_id(), now=now, fmt=fmt,
                        dry_run=dry_run, cache_dir=cache_dir(root))

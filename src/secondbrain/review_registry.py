"""Review registry: append-only review event logs, FSRS replay, status and queue.

Specification: ``docs/review-registry.md``. The TypeScript twin lives in
``obsidian-plugin/src/registry/``; both must pass ``tests/fixtures/review/``.

This module depends only on the standard library and ``fsrs`` (py-fsrs). It
never parses the vault: callers pass card metadata (``CardMeta``) and card
text where needed.
"""

from __future__ import annotations

import heapq
import json
import math
import os
import re
import socket
import hashlib
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from fsrs import Card, Rating, Scheduler, State

SCHEMA_VERSION = 1
# Revision of the parsing and replay rules. Increment it here and in the plugin
# (obsidian-plugin/src/registry/events.ts) whenever a change makes any log parse or
# replay differently; state caches written under another revision are ignored.
# 2: timestamps and SR dates accept ASCII digits only.
RULES_VERSION = 2

FSRS_PARAMETERS: tuple[float, ...] = (
    0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722,
    0.1666, 0.796, 1.4835, 0.0614, 0.2629, 1.6483, 0.6014, 1.8729, 0.5425,
    0.0912, 0.0658, 0.1542,
)
DESIRED_RETENTION = 0.9
LEARNING_STEPS_MIN: tuple[int, ...] = (1, 10)
RELEARNING_STEPS_MIN: tuple[int, ...] = (10,)
MAXIMUM_INTERVAL = 36500
DAY_START_HOUR = 4
MASTERED_STABILITY_DAYS = 21.0
DEFAULT_NEW_PER_DAY = 10
DEFAULT_REVIEW_SECTION = "复习卡"

MODES_REVIEW = ("flip", "qa", "agent")
MODE_IMPORT = "import"
RATING_NAMES = {1: "again", 2: "hard", 3: "good", 4: "easy"}

_DEVICE_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")  # use fullmatch: "$" would accept a trailing newline
_LOG_NAME_RE = re.compile(r"events-.+\.(jsonl|md)")
_MD_HEADER = "---\nformat: secondbrain-review-log\n---\n"
_EPOCH_DATE = date(1970, 1, 1)
_UTC = timezone.utc

# Relation type -> True when the target comes first, False when the card comes first.
# part_of/has_part are left out: on a real 8,615-card vault they added 300-400 cycles in
# either direction (see docs/review-registry.md section 6).
PREREQ_TARGET_FIRST: Mapping[str, bool] = {
    "depends_on": True,
    "follows": True,
    "required_by": False,
    "precedes": False,
}


# --------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Event:
    ts: datetime  # aware, truncated to milliseconds, in the writer's offset
    device: str
    card: str
    mode: str
    rating: int | None  # None only for import events
    raw: Mapping[str, Any]
    source: str = ""
    line: int = 0
    import_seed: Mapping[str, Any] | None = None

    @property
    def is_import(self) -> bool:
        return self.mode == MODE_IMPORT

    @property
    def instant_ms(self) -> int:
        return _to_ms(self.ts)


@dataclass(frozen=True)
class Issue:
    source: str
    line: int
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "line": self.line, "reason": self.reason}


@dataclass(frozen=True)
class CardMeta:
    """What the registry needs to know about a card. Callers build these."""

    id: str
    title: str = ""
    domain: str = ""
    kind: str = ""
    status: str = ""
    path: str = ""
    relations: tuple[tuple[str, str], ...] = ()
    rank: float = 0
    pillar: str = ""
    topic: str = ""


@dataclass
class CardState:
    card: str
    state: str  # learning | review | relearning
    stability: float
    difficulty: float
    due: datetime  # UTC
    last_review: datetime  # UTC
    reps: int = 0
    lapses: int = 0
    step: int = 0
    first_review: datetime | None = None
    last_day: int | None = None
    events: int = 0
    last_event: Event | None = None
    _sched_day: int = 0  # learning-day index used as the scheduler's "last review"

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "stability": self.stability,
            "difficulty": self.difficulty,
            "due": format_utc(self.due),
            "last_review": format_utc(self.last_review),
            "reps": self.reps,
            "lapses": self.lapses,
        }


@dataclass
class ReplayResult:
    states: dict[str, CardState]
    issues: list[Issue]
    review_days: set[int] = field(default_factory=set)  # learning days with >=1 review
    reviews_by_day: dict[int, int] = field(default_factory=dict)
    first_day: dict[str, int] = field(default_factory=dict)  # card -> first review day
    loaded_from: str = "events"  # "events" (full replay) or "cache" (checkpoint + new lines)


class EventError(ValueError):
    """An event object does not follow the format."""


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _to_ms(dt: datetime) -> int:
    return (dt - _EPOCH_UTC) // timedelta(milliseconds=1)


# Patterns are used with fullmatch (a "$" anchor would also accept a trailing newline).
# re.ASCII: "\d" must not match full-width or other non-ASCII digits (JavaScript's does not).
_TS_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?([Zz]|[+-]\d{2}:\d{2})", re.ASCII)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


def parse_ts(value: Any) -> datetime:
    """Parse an RFC 3339 timestamp with an offset; truncate to milliseconds."""
    m = _TS_RE.fullmatch(value) if isinstance(value, str) else None
    if m is None:
        raise EventError(f"ts is not RFC 3339 with an offset: {value!r}")
    frac = (m.group(7) or "").ljust(3, "0")[:3]
    off = m.group(8)
    if off not in ("Z", "z") and (int(off[1:3]) > 23 or int(off[4:6]) > 59):
        raise EventError(f"ts has an invalid UTC offset: {value!r}")
    try:
        tz = _UTC if off in ("Z", "z") else timezone(
            (1 if off[0] == "+" else -1) * timedelta(hours=int(off[1:3]), minutes=int(off[4:6])))
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)),
                        int(m.group(5)), int(m.group(6)), int(frac) * 1000, tzinfo=tz)
    except ValueError as exc:
        raise EventError(f"ts is not a valid time: {value!r}") from exc


def format_utc(dt: datetime) -> str:
    u = dt.astimezone(_UTC)
    return u.strftime("%Y-%m-%dT%H:%M:%S.") + f"{u.microsecond // 1000:03d}Z"


def format_local(dt: datetime) -> str:
    """Local RFC 3339 with milliseconds, as writers emit it."""
    base = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"
    off = dt.utcoffset() or timedelta(0)
    minutes = int(off.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"{base}{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def learning_day(dt: datetime, day_start_hour: int = DAY_START_HOUR) -> int:
    """Learning-day index of an aware datetime, in its own offset."""
    if dt.tzinfo is None:
        raise ValueError("learning_day needs an aware datetime")
    local = dt.replace(tzinfo=None) - timedelta(hours=day_start_hour)
    return (local.date() - _EPOCH_DATE).days


def day_start(day: int, tz: Any, day_start_hour: int = DAY_START_HOUR) -> datetime:
    """Aware datetime at which learning day ``day`` starts in offset ``tz``."""
    d = _EPOCH_DATE + timedelta(days=day)
    return datetime(d.year, d.month, d.day, day_start_hour, tzinfo=tz)


def day_to_date(day: int) -> date:
    return _EPOCH_DATE + timedelta(days=day)


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


# --------------------------------------------------------------------------
# Questions
# --------------------------------------------------------------------------
def normalize_question(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower()
    return " ".join(t.split())


def question_id(text: str) -> str:
    """``h:`` + FNV-1a 32-bit hash (8 hex) of the normalized question."""
    h = 0x811C9DC5
    for b in normalize_question(text).encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"h:{h:08x}"


_SR_COMMENT_RE = re.compile(r"\s*<!--SR:.*?-->")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


@dataclass(frozen=True)
class ReviewItem:
    q: str
    question: str
    answer: str


def extract_review_items(markdown: str, section: str = DEFAULT_REVIEW_SECTION) -> list[ReviewItem]:
    """Return the ``question::answer`` lines of the card's review section."""
    items: list[ReviewItem] = []
    in_section = False
    level = 0
    in_fence = False
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            if in_section and len(m.group(1)) <= level:
                in_section = False
            if not in_section and m.group(2).strip() == section:
                in_section = True
                level = len(m.group(1))
            continue
        if not in_section or "::" not in line:
            continue
        text = _SR_COMMENT_RE.sub("", line).strip()
        if text.startswith(("- ", "* ")):
            text = text[2:].strip()
        q, _, a = text.partition("::")
        q, a = q.strip(), a.lstrip(":").strip()
        if q and a:
            items.append(ReviewItem(q=question_id(q), question=q, answer=a))
    return items


# --------------------------------------------------------------------------
# Parsing and validation
# --------------------------------------------------------------------------
def _rating(value: Any, where: str) -> int:
    # JSON 3.0 and 3 are the same number in JavaScript; accept both here too.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or value != int(value) or not 1 <= value <= 4:
        raise EventError(f"{where} must be an integer 1-4")
    return int(value)


def _str_field(obj: Mapping[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EventError(f"{key} must be a non-empty string")
    return value


def _parse_import(seed: Any) -> dict[str, Any]:
    if not isinstance(seed, Mapping):
        raise EventError("import must be an object")
    origin = seed.get("from")
    if origin not in ("sr-sm2", "sr-fsrs"):
        raise EventError("import.from must be sr-sm2 or sr-fsrs")
    interval = seed.get("interval")
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) \
            or not math.isfinite(interval) or interval < 0:
        raise EventError("import.interval must be a number >= 0")
    for key in ("due", "last_review"):
        value = seed.get(key)
        if key == "last_review" and (value is None or value == ""):
            continue
        if not isinstance(value, str) or not (_DATE_RE.fullmatch(value) or _TS_RE.fullmatch(value)):
            raise EventError(f"import.{key} must be a YYYY-MM-DD date or an RFC 3339 timestamp")
        if _DATE_RE.fullmatch(value):
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise EventError(f"import.{key} is not a valid date") from exc
        else:
            parse_ts(value)
    out = dict(seed)
    if origin == "sr-sm2":
        ease = seed.get("ease")
        if isinstance(ease, bool) or not isinstance(ease, (int, float)) or not math.isfinite(ease) \
                or ease <= 0:
            raise EventError("import.ease must be a positive number")
    else:
        for key in ("stability", "difficulty"):
            v = seed.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
                raise EventError(f"import.{key} must be a positive number")
    return out


def parse_event(obj: Any, source: str = "", line: int = 0) -> Event:
    """Validate one decoded JSON object and return an ``Event``."""
    if not isinstance(obj, Mapping):
        raise EventError("event must be a JSON object")
    v = obj.get("v")
    if v != SCHEMA_VERSION or isinstance(v, bool):
        raise EventError(f"unsupported version: {v!r}")
    ts = parse_ts(obj.get("ts"))
    device = _str_field(obj, "device")
    if not _DEVICE_RE.fullmatch(device):
        raise EventError(f"device id is not allowed: {device!r}")
    card = _str_field(obj, "card")
    mode = _str_field(obj, "mode")
    seed = None
    rating: int | None = None
    if mode == MODE_IMPORT:
        seed = _parse_import(obj.get("import"))
    else:
        items = obj.get("items")
        item_ratings: list[int] = []
        if items is not None:
            if not isinstance(items, list) or not items:
                raise EventError("items must be a non-empty array")
            for i, item in enumerate(items):
                if not isinstance(item, Mapping):
                    raise EventError(f"items[{i}] must be an object")
                q = item.get("q")
                if not isinstance(q, str) or not q:
                    raise EventError(f"items[{i}].q must be a non-empty string")
                item_ratings.append(_rating(item.get("rating"), f"items[{i}].rating"))
        if "rating" in obj:
            rating = _rating(obj.get("rating"), "rating")
        elif item_ratings:
            rating = min(item_ratings)
        else:
            raise EventError("rating is required when items are absent")
    return Event(ts=ts, device=device, card=card, mode=mode, rating=rating,
                 raw=obj, source=source, line=line, import_seed=seed)


def parse_log_text(name: str, text: str, first_line: int = 1) -> tuple[list[Event], list[Issue]]:
    """Parse the content of one log file (``.jsonl`` or ``.md``), or of its tail from ``first_line``."""
    is_md = name.endswith(".md")
    text = text.lstrip("\ufeff")
    events: list[Event] = []
    issues: list[Issue] = []
    for no, raw in enumerate(text.split("\n"), start=1):
        line = raw.strip()
        if not line:
            continue
        if is_md and not line.startswith("{"):
            continue
        try:
            obj = json.loads(line, parse_constant=_reject_constant)
        except Exception as exc:  # JSONDecodeError, NaN constants, nesting too deep
            issues.append(Issue(name, no + first_line - 1, f"invalid JSON: {getattr(exc, 'msg', exc)}"))
            continue
        try:
            events.append(parse_event(obj, name, no + first_line - 1))
        except Exception as exc:  # one bad line must never stop the replay of the others
            issues.append(Issue(name, no + first_line - 1, str(exc) if isinstance(exc, EventError)
                                else f"invalid event: {exc}"))
    return events, issues


def _reject_constant(name: str) -> Any:
    raise ValueError(f"invalid JSON constant {name}")


def log_files(review_dir: Path) -> list[Path]:
    if not review_dir.is_dir():
        return []
    return sorted(p for p in review_dir.iterdir() if p.is_file() and _LOG_NAME_RE.fullmatch(p.name))


def read_logs(review_dir: Path) -> tuple[list[Event], list[Issue]]:
    events: list[Event] = []
    issues: list[Issue] = []
    for path in log_files(review_dir):
        # Bytes, not read_text: universal newlines would split on a lone "\r" unlike the TS reader.
        ev, iss = parse_log_text(path.name, path.read_bytes().decode("utf-8", errors="replace"))
        events.extend(ev)
        issues.extend(iss)
    return events, issues


def order_events(events: Iterable[Event]) -> list[Event]:
    """Remove duplicates (same device, card, instant; first read wins) and sort."""
    seen: set[tuple[str, str, int]] = set()
    unique: list[Event] = []
    for ev in events:
        key = (ev.device, ev.card, ev.instant_ms)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ev)
    return sorted(unique, key=lambda e: (e.instant_ms, e.device, e.card, e.source, e.line))


# --------------------------------------------------------------------------
# Scheduler (policy of docs/review-registry.md section 3; memory from py-fsrs)
# --------------------------------------------------------------------------
_ORIGIN = datetime(2000, 1, 1, tzinfo=_UTC)


class _Memory:
    """FSRS-6 memory updates and intervals, taken from py-fsrs."""

    def __init__(self) -> None:
        self._s = Scheduler(
            parameters=FSRS_PARAMETERS,
            desired_retention=DESIRED_RETENTION,
            learning_steps=tuple(timedelta(minutes=m) for m in LEARNING_STEPS_MIN),
            relearning_steps=tuple(timedelta(minutes=m) for m in RELEARNING_STEPS_MIN),
            maximum_interval=MAXIMUM_INTERVAL,
            enable_fuzzing=False,
        )

    def initial(self, rating: int) -> tuple[float, float, int | None]:
        card, _ = self._s.review_card(Card(card_id=0), Rating(rating), review_datetime=_ORIGIN)
        interval = (card.due - _ORIGIN).days if card.state == State.Review else None
        return card.stability, card.difficulty, interval

    def update(self, s: float, d: float, elapsed: int, rating: int) -> tuple[float, float, int | None]:
        """Memory state after a review, and interval(new S) unless rating is 1."""
        now = _ORIGIN + timedelta(days=elapsed)
        card = Card(card_id=0, state=State.Review, step=None, stability=s, difficulty=d,
                    due=now, last_review=_ORIGIN)
        new, _ = self._s.review_card(card, Rating(rating), review_datetime=now)
        interval = None if rating == 1 else (new.due - now).days
        return new.stability, new.difficulty, interval


def _hard_minutes(steps: Sequence[int]) -> int:
    if len(steps) == 1:
        return _round_half_up(steps[0] * 1.5)
    return _round_half_up((steps[0] + steps[1]) / 2)


def _step_minutes(state: str, step: int, rating: int) -> tuple[int, int] | None:
    """(minutes, next_step) for a learning step, or None when the card graduates."""
    steps = RELEARNING_STEPS_MIN if state in ("relearning", "review") else LEARNING_STEPS_MIN
    if not steps or step >= len(steps):
        return None
    if state == "review":
        return (steps[max(0, step)], 0) if rating == 1 else None
    if rating == 1:
        return steps[0], 0
    if rating == 2:
        return _hard_minutes(steps), step
    if rating == 3 and step + 1 < len(steps):
        return steps[step + 1], step + 1
    return None


class Replayer:
    """Applies events to card states. One instance can replay many cards."""

    def __init__(self, day_start_hour: int = DAY_START_HOUR) -> None:
        self.day_start_hour = day_start_hour
        self._mem = _Memory()

    def _seed(self, ev: Event) -> CardState:
        seed = ev.import_seed or {}
        tz = ev.ts.tzinfo
        due = _seed_time(seed["due"], tz, self.day_start_hour)
        interval = float(seed["interval"])
        if seed["from"] == "sr-sm2":
            stability = max(interval, 0.1)
            ease = float(seed["ease"])
            difficulty = round(min(10.0, max(1.0, 10 - 9 * (ease - 130) / 220)), 4)
            last = due - timedelta(days=interval)
        else:
            stability = float(seed["stability"])
            difficulty = float(seed["difficulty"])
            lr = seed.get("last_review")
            last = _seed_time(lr, tz, self.day_start_hour) if isinstance(lr, str) and lr else due - timedelta(days=interval)
        last_local = last.astimezone(tz)
        return CardState(card=ev.card, state="review", stability=stability, difficulty=difficulty,
                         due=due.astimezone(_UTC), last_review=last.astimezone(_UTC), reps=0, lapses=0,
                         step=0, _sched_day=learning_day(last_local, self.day_start_hour))

    def apply(self, prev: CardState | None, ev: Event) -> CardState:
        if ev.is_import:
            st = self._seed(ev)
            if prev is not None:
                st.first_review, st.last_day = prev.first_review, prev.last_day
                st.events = prev.events
            st.events += 1
            st.last_event = ev
            return st
        assert ev.rating is not None
        rating = ev.rating
        day = learning_day(ev.ts, self.day_start_hour)
        t = ev.ts.astimezone(_UTC)
        if prev is None:
            s, d, interval = self._mem.initial(rating)
            st = CardState(card=ev.card, state="learning", stability=s, difficulty=d, due=t,
                           last_review=t, step=0)
            self._after_learning(st, "new", 0, rating, t, interval)
            st.first_review = t
        else:
            st = replace(prev)
            # The scheduler never moves backwards: an event whose learning day is
            # earlier than the card's last one (another offset) counts as that day.
            sched_day = max(day, prev._sched_day)
            elapsed = sched_day - prev._sched_day
            if prev.state in ("learning", "relearning"):
                s, d, interval = self._mem.update(prev.stability, prev.difficulty, elapsed, rating)
                st.stability, st.difficulty = s, d
                self._after_learning(st, prev.state, prev.step, rating, t, interval)
            else:
                self._after_review(st, prev, elapsed, rating, t)
            if st.first_review is None:
                st.first_review = t
        st.reps += 1
        st.last_review = t
        st.last_day = day
        st._sched_day = day if prev is None else max(day, prev._sched_day)
        st.events += 1
        st.last_event = ev
        return st

    def _after_learning(self, st: CardState, state: str, step: int, rating: int,
                        t: datetime, interval: int | None) -> None:
        info = _step_minutes("learning" if state == "new" else state, step, rating)
        if info is not None and 0 < info[0] < 1440:
            st.state = "learning" if state == "new" else state
            st.step = info[1]
            st.due = t + timedelta(minutes=info[0])
            return
        if interval is None:  # graduating: need interval(S) for this rating
            raise AssertionError("graduation without an interval")
        st.state, st.step = "review", 0
        st.due = t + timedelta(days=interval)

    def _after_review(self, st: CardState, prev: CardState, elapsed: int, rating: int, t: datetime) -> None:
        s, d = prev.stability, prev.difficulty
        if rating == 1:
            st.stability, st.difficulty, _ = self._mem.update(s, d, elapsed, 1)
            minutes, next_step = _step_minutes("review", prev.step, 1) or (0, 0)
            st.lapses += 1
            if 0 < minutes < 1440:
                st.state, st.step = "relearning", next_step
                st.due = t + timedelta(minutes=minutes)
            else:  # no relearning steps configured
                _, _, iv = self._mem.update(s, d, elapsed, 3)
                st.state, st.step = "review", 0
                st.due = t + timedelta(days=iv or 1)
            return
        s2, d2, i2 = self._mem.update(s, d, elapsed, 2)
        s3, d3, i3 = self._mem.update(s, d, elapsed, 3)
        assert i2 is not None and i3 is not None
        hard = min(i2, i3)
        good = max(i3, hard + 1)
        if rating == 2:
            st.stability, st.difficulty, days = s2, d2, hard
        elif rating == 3:
            st.stability, st.difficulty, days = s3, d3, good
        else:
            s4, d4, i4 = self._mem.update(s, d, elapsed, 4)
            assert i4 is not None
            st.stability, st.difficulty, days = s4, d4, max(i4, good + 1)
        st.state, st.step = "review", 0
        st.due = t + timedelta(days=days)


def _seed_time(value: str, tz: Any, day_start_hour: int) -> datetime:
    if _DATE_RE.fullmatch(value):
        d = date.fromisoformat(value)
        return datetime(d.year, d.month, d.day, day_start_hour, tzinfo=tz)
    return parse_ts(value)


def _apply_ordered(result: ReplayResult, ordered: Iterable[Event], day_start_hour: int) -> None:
    rp = Replayer(day_start_hour)
    states = result.states
    for ev in ordered:
        states[ev.card] = rp.apply(states.get(ev.card), ev)
        if not ev.is_import:
            day = learning_day(ev.ts, day_start_hour)
            result.review_days.add(day)
            result.reviews_by_day[day] = result.reviews_by_day.get(day, 0) + 1
            result.first_day.setdefault(ev.card, day)


def replay(events: Iterable[Event], *, day_start_hour: int = DAY_START_HOUR) -> ReplayResult:
    """Replay events into per-card states (docs section 4)."""
    result = ReplayResult(states={}, issues=[])
    _apply_ordered(result, order_events(events), day_start_hour)
    return result


def load(review_dir: Path, *, day_start_hour: int = DAY_START_HOUR,
         cache_dir: Path | None = None) -> ReplayResult:
    """Replay all logs. With ``cache_dir``, start from this machine's checkpoint (docs section 4.1)."""
    if cache_dir is None:
        return _load_full(review_dir, day_start_hour)[0]
    path = cache_path(review_dir, cache_dir)
    cached = _read_cache(path, day_start_hour)
    loaded = _load_incremental(review_dir, cached, day_start_hour) if cached else None
    result, files, watermark, clean = loaded or _load_full(review_dir, day_start_hour)
    if clean:
        _write_cache(path, result, files, watermark, day_start_hour)
    return result


# --------------------------------------------------------------------------
# State cache (docs section 4.1): a checkpoint of the replay plus how far each log was read
# --------------------------------------------------------------------------
CACHE_FORMAT = 1


def cache_path(review_dir: Path, cache_dir: Path) -> Path:
    key = hashlib.sha256(str(review_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"review-state-{key}.json"


def _cache_meta(day_start_hour: int) -> dict[str, Any]:
    try:
        from importlib.metadata import version
        lib = version("fsrs")
    except Exception:  # pragma: no cover - metadata missing in odd installs
        lib = "unknown"
    return {"cache": CACHE_FORMAT, "schema": SCHEMA_VERSION, "rules": RULES_VERSION,
            "day_start": day_start_hour, "fsrs": lib,
            "params": list(FSRS_PARAMETERS), "retention": DESIRED_RETENTION,
            "steps": list(LEARNING_STEPS_MIN), "relearning": list(RELEARNING_STEPS_MIN),
            "max_interval": MAXIMUM_INTERVAL}


def _key(ev: Event) -> list[Any]:
    return [ev.instant_ms, ev.device, ev.card, ev.source, ev.line]


def _load_full(review_dir: Path, day_start_hour: int):
    events: list[Event] = []
    issues: list[Issue] = []
    files: dict[str, dict[str, Any]] = {}
    clean = True
    for path in log_files(review_dir):
        data = path.read_bytes()
        ev, iss = parse_log_text(path.name, data.decode("utf-8", errors="replace"))
        events.extend(ev)
        issues.extend(iss)
        files[path.name] = {"bytes": len(data), "lines": data.count(b"\n"),
                            "sha256": hashlib.sha256(data).hexdigest()}
        clean = clean and (not data or data.endswith(b"\n"))
    ordered = order_events(events)
    result = ReplayResult(states={}, issues=issues)
    _apply_ordered(result, ordered, day_start_hour)
    return result, files, (_key(ordered[-1]) if ordered else None), clean


def _load_incremental(review_dir: Path, cached: tuple[ReplayResult, dict[str, Any], Any],
                      day_start_hour: int):
    """Read only what was appended since the checkpoint. None when a full replay is needed."""
    result, old_files, watermark = cached
    paths = log_files(review_dir)
    if any(name not in {p.name for p in paths} for name in old_files):
        return None  # a log disappeared
    new_events: list[Event] = []
    new_issues: list[Issue] = []
    files: dict[str, dict[str, Any]] = {}
    for p in paths:
        old = old_files.get(p.name)
        data = p.read_bytes()  # hashing is cheap; parsing and replaying the old lines is what we skip
        start, line0 = (old["bytes"], old["lines"]) if old else (0, 0)
        if old and (len(data) < start or hashlib.sha256(data[:start]).hexdigest() != old["sha256"]):
            return None  # the part already read was changed or truncated
        rest = data[start:]
        if rest and not rest.endswith(b"\n"):
            return None  # a line is still being written or was cut off
        if rest:
            ev, iss = parse_log_text(p.name, rest.decode("utf-8", errors="replace"), first_line=line0 + 1)
            new_events.extend(ev)
            new_issues.extend(iss)
        files[p.name] = {"bytes": len(data), "lines": line0 + rest.count(b"\n"),
                         "sha256": hashlib.sha256(data).hexdigest()}
    ordered = order_events(new_events)
    if ordered and watermark is not None and ordered[0].instant_ms <= watermark[0]:
        return None  # an event older than the checkpoint arrived (another device synced late)
    _apply_ordered(result, ordered, day_start_hour)
    result.issues = sorted(result.issues + new_issues, key=lambda i: (i.source, i.line))
    result.loaded_from = "cache"
    return result, files, (_key(ordered[-1]) if ordered else watermark), True


def _state_to_json(st: CardState) -> dict[str, Any]:
    return {"state": st.state, "stability": st.stability, "difficulty": st.difficulty,
            "due": format_utc(st.due), "last_review": format_utc(st.last_review), "reps": st.reps,
            "lapses": st.lapses, "step": st.step,
            "first_review": format_utc(st.first_review) if st.first_review else None,
            "last_day": st.last_day, "events": st.events, "sched_day": st._sched_day}


def _state_from_json(card: str, d: Mapping[str, Any]) -> CardState:
    return CardState(card=card, state=d["state"], stability=d["stability"], difficulty=d["difficulty"],
                     due=parse_ts(d["due"]), last_review=parse_ts(d["last_review"]), reps=d["reps"],
                     lapses=d["lapses"], step=d["step"],
                     first_review=parse_ts(d["first_review"]) if d["first_review"] else None,
                     last_day=d["last_day"], events=d["events"], _sched_day=d["sched_day"])


def _write_cache(path: Path, result: ReplayResult, files: dict[str, Any], watermark: Any,
                 day_start_hour: int) -> None:
    data = {"meta": _cache_meta(day_start_hour), "files": files, "watermark": watermark,
            "states": {c: _state_to_json(st) for c, st in result.states.items()},
            "review_days": sorted(result.review_days),
            "reviews_by_day": {str(d): n for d, n in result.reviews_by_day.items()},
            "first_day": result.first_day, "issues": [i.as_dict() for i in result.issues]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # the cache only saves time; the logs stay the record


def _read_cache(path: Path, day_start_hour: int):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("meta") != _cache_meta(day_start_hour):
            return None
        result = ReplayResult(
            states={c: _state_from_json(c, d) for c, d in data["states"].items()},
            issues=[Issue(i["source"], i["line"], i["reason"]) for i in data["issues"]],
            review_days=set(data["review_days"]),
            reviews_by_day={int(d): n for d, n in data["reviews_by_day"].items()},
            first_day=dict(data["first_day"]))
        return result, dict(data["files"]), data["watermark"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


# --------------------------------------------------------------------------
# Status (docs section 5)
# --------------------------------------------------------------------------
STUDY_STATES = ("unlearned", "learning", "review", "mastered")


def study_state(st: CardState | None, mastered_days: float = MASTERED_STABILITY_DAYS) -> str:
    if st is None:
        return "unlearned"
    if st.state in ("learning", "relearning"):
        return "learning"
    return "mastered" if st.stability >= mastered_days else "review"


def is_due(st: CardState | None, now: datetime, day_start_hour: int = DAY_START_HOUR) -> bool:
    if st is None:
        return False
    tz = now.tzinfo
    return learning_day(st.due.astimezone(tz), day_start_hour) <= learning_day(now, day_start_hour)


def _empty_counts() -> dict[str, int]:
    return {"total": 0, "unlearned": 0, "learning": 0, "review": 0, "mastered": 0,
            "learned": 0, "due": 0, "reviewed_today": 0, "due_not_reviewed_today": 0}


def summarize(cards: Iterable[CardMeta], result: ReplayResult, now: datetime, *,
              day_start_hour: int = DAY_START_HOUR,
              mastered_days: float = MASTERED_STABILITY_DAYS) -> dict[str, Any]:
    """Counts for the whole vault and per domain."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    today = learning_day(now, day_start_hour)
    totals = _empty_counts()
    domains: dict[str, dict[str, int]] = {}
    known: set[str] = set()
    for meta in cards:
        known.add(meta.id)
        st = result.states.get(meta.id)
        row = domains.setdefault(meta.domain or "", _empty_counts())
        s = study_state(st, mastered_days)
        due = is_due(st, now, day_start_hour)
        reviewed = st is not None and st.last_day == today
        for bucket in (totals, row):
            bucket["total"] += 1
            bucket[s] += 1
            if s != "unlearned":
                bucket["learned"] += 1
            if due:
                bucket["due"] += 1
                if not reviewed:
                    bucket["due_not_reviewed_today"] += 1
            if reviewed:
                bucket["reviewed_today"] += 1
    new_today = sum(1 for card, day in result.first_day.items() if day == today and card in known)
    return {
        "now": format_local(now),
        "today": day_to_date(today).isoformat(),
        "totals": totals,
        "domains": dict(sorted(domains.items())),
        "new_today": new_today,
        "reviews_today": result.reviews_by_day.get(today, 0),
        "streak": streak(result.review_days, today),
        "orphans": sorted(c for c in result.states if c not in known),
        "issues": len(result.issues),
    }


def streak(review_days: set[int], today: int) -> int:
    day = today if today in review_days else today - 1
    n = 0
    while day in review_days:
        n += 1
        day -= 1
    return n


# --------------------------------------------------------------------------
# Queue (docs section 6)
# --------------------------------------------------------------------------
def learning_order(cards: Sequence[CardMeta]) -> tuple[list[CardMeta], list[str]]:
    """Prerequisite topological order; returns (ordered, cycle_breaks)."""
    by_id = {c.id: c for c in cards}
    key = {c.id: (c.rank, c.id) for c in cards}
    indeg = {c.id: 0 for c in cards}
    adj: dict[str, set[str]] = {}
    for c in cards:
        for rtype, target in c.relations:
            first = PREREQ_TARGET_FIRST.get(rtype)
            if first is None or target == c.id or target not in by_id:
                continue
            src, dst = (target, c.id) if first else (c.id, target)
            out = adj.setdefault(src, set())
            if dst not in out:
                out.add(dst)
                indeg[dst] += 1
    ready = [key[cid] for cid, n in indeg.items() if n == 0]
    heapq.heapify(ready)
    done: set[str] = set()
    ordered: list[CardMeta] = []
    breaks: list[str] = []
    remaining = sorted(key.values())
    pos = 0
    while len(done) < len(by_id):
        if ready:
            _, cid = heapq.heappop(ready)
            if cid in done:
                continue
        else:
            while remaining[pos][1] in done:
                pos += 1
            cid = remaining[pos][1]
            breaks.append(cid)
        done.add(cid)
        ordered.append(by_id[cid])
        for nxt in adj.get(cid, ()):
            if nxt in done:
                continue
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                heapq.heappush(ready, key[nxt])
    return ordered, breaks


@dataclass(frozen=True)
class PillarInfo:
    name: str
    map_title: str
    why: str
    order: float
    topics: tuple[str, ...]


def build_taxonomy(docs: Iterable[tuple[str, Any]]) -> dict[str, PillarInfo]:
    """Pillars from parsed taxonomy YAML documents ``(path, data)``. A pillar name defined twice keeps the first."""
    pillars: dict[str, PillarInfo] = {}
    for path, data in docs:
        if not isinstance(data, Mapping):
            continue
        title = data.get("title")
        if not isinstance(title, str) or not title:
            title = re.sub(r"\.ya?ml$", "", path.rsplit("/", 1)[-1])
        raw = data.get("pillars")
        for i, rec in enumerate(raw if isinstance(raw, list) else []):
            if not isinstance(rec, Mapping):
                continue
            name = rec.get("name").strip() if isinstance(rec.get("name"), str) else ""
            if not name or name in pillars:
                continue
            order = rec.get("order")
            topics = rec.get("topics")
            why = rec.get("why")
            pillars[name] = PillarInfo(
                name=name, map_title=title, why=why.strip() if isinstance(why, str) else "",
                order=order if isinstance(order, (int, float)) and not isinstance(order, bool) else i + 1,
                topics=tuple(t for t in topics if isinstance(t, str)) if isinstance(topics, list) else ())
    return pillars


def rank_for(meta: CardMeta, pillars: Mapping[str, PillarInfo], domain_order: Mapping[str, int]) -> float:
    """Tie-break rank for new cards: study-domain position, pillar order, topic position (same as the plugin)."""
    d = domain_order.get(meta.domain, len(domain_order))
    p = pillars.get(meta.pillar)
    pillar_pos = min(p.order, 998) if p else 999
    t = p.topics.index(meta.topic) if p and meta.topic in p.topics else -1
    topic_pos = min(t, 998) if t >= 0 else 999
    return d * 1_000_000 + pillar_pos * 1_000 + topic_pos


def rank_cards(cards: Iterable[CardMeta], pillars: Mapping[str, PillarInfo],
               study_domains: Sequence[str] = ()) -> list[CardMeta]:
    """Cards with ``rank`` set. Domain order: the study domains as given, else all domains sorted."""
    metas = list(cards)
    names = list(study_domains) if study_domains else sorted({m.domain for m in metas})
    order = {d: i for i, d in enumerate(names)}
    return [replace(m, rank=rank_for(m, pillars, order)) for m in metas]


def eligible_new(meta: CardMeta, domains: Sequence[str]) -> bool:
    if meta.kind == "record" or meta.status == "needs-review":
        return False
    return not domains or meta.domain in domains


def build_queue(cards: Iterable[CardMeta], result: ReplayResult, now: datetime, *,
                new_per_day: int = DEFAULT_NEW_PER_DAY, domains: Sequence[str] = (),
                day_start_hour: int = DAY_START_HOUR) -> dict[str, Any]:
    """Due cards first, then new cards in prerequisite order."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    today = learning_day(now, day_start_hour)
    metas = list(cards)
    due: list[tuple[datetime, str, CardMeta]] = []
    candidates: list[CardMeta] = []
    for meta in metas:
        st = result.states.get(meta.id)
        if st is None:
            if eligible_new(meta, domains):
                candidates.append(meta)
        elif is_due(st, now, day_start_hour):
            due.append((st.due, meta.id, meta))
    due.sort(key=lambda x: (x[0], x[1]))
    known = {m.id for m in metas}
    new_today = sum(1 for card, day in result.first_day.items() if day == today and card in known)
    quota = max(0, new_per_day - new_today)
    ordered, breaks = learning_order(candidates)
    return {
        "today": day_to_date(today).isoformat(),
        "due": [m for _, _, m in due],
        "new": ordered[:quota],
        "new_quota": quota,
        "new_today": new_today,
        "new_available": len(candidates),
        "cycle_breaks": breaks,
    }


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------
def default_device_id(prefix: str = "cli") -> str:
    digest = hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{digest}"


def make_event(card: str, *, device: str, now: datetime, mode: str = "agent",
               rating: int | None = None, items: Sequence[Mapping[str, Any]] | None = None,
               grader: str | None = None, ms: int | None = None,
               answer: str | None = None, feedback: str | None = None) -> dict[str, Any]:
    """Build and validate a review event. ``rating`` defaults to the minimum item rating."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    obj: dict[str, Any] = {"v": SCHEMA_VERSION, "ts": format_local(now), "device": device,
                           "card": card, "mode": mode}
    clean_items = [dict(i) for i in items] if items else []
    if rating is None and clean_items:
        rating = min(int(i["rating"]) for i in clean_items)
    if rating is not None:
        obj["rating"] = rating
    if clean_items:
        obj["items"] = clean_items
    for key, value in (("grader", grader), ("answer", answer), ("feedback", feedback), ("ms", ms)):
        if value is not None:
            obj[key] = value
    parse_event(obj)
    return obj


def log_path(review_dir: Path, device: str, fmt: str = "jsonl") -> Path:
    if fmt not in ("jsonl", "md"):
        raise ValueError("fmt must be jsonl or md")
    if not _DEVICE_RE.fullmatch(device):
        raise ValueError(f"device id is not allowed: {device!r}")
    return review_dir / f"events-{device}.{fmt}"


def append_event(review_dir: Path, event: Mapping[str, Any], *, fmt: str = "jsonl") -> Path:
    """Validate and append one event line to this device's log (docs section 1.3)."""
    return append_events(review_dir, [event], fmt=fmt)


def append_events(review_dir: Path, events: Sequence[Mapping[str, Any]], *, fmt: str = "jsonl") -> Path:
    """Validate all events, then append them to one device's log with a single write."""
    if not events:
        raise ValueError("no events to append")
    for event in events:
        parse_event(event)
    devices = {str(e["device"]) for e in events}
    if len(devices) != 1:
        raise ValueError("all events must come from one device")
    path = log_path(review_dir, devices.pop(), fmt)
    review_dir.mkdir(parents=True, exist_ok=True)
    line = "".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n" for e in events)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        size = os.fstat(fd).st_size
        prefix = ""
        if size == 0 and fmt == "md":
            prefix = _MD_HEADER
        elif size > 0 and os.pread(fd, 1, size - 1) != b"\n":
            prefix = "\n"
        data = (prefix + line).encode("utf-8")
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


# --------------------------------------------------------------------------
# Spaced Repetition plugin import (docs section 2.2)
# --------------------------------------------------------------------------
_SR_BLOCK_RE = re.compile(r"<!--SR:(.*?)-->", re.S)
_SM2_RE = re.compile(r"!(\d{4}-\d{2}-\d{2}),(\d+),(\d+)", re.ASCII)


def sr_schedule(text: str, frontmatter: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Merge the Spaced Repetition schedules found in a card into one import seed."""
    sm2: list[tuple[str, int, int]] = []
    fsrs_rows: list[dict[str, Any]] = []
    for block in _SR_BLOCK_RE.findall(text):
        for seg in block.split("!")[1:]:
            parts = seg.split(",")
            if parts[0] == "fsrs" and len(parts) >= 10:
                if not all(p.isascii() for p in parts[1:10]):
                    continue  # float() would read non-ASCII digits; the plugin does not
                try:
                    fsrs_rows.append({"due": parts[1], "interval": int(float(parts[2])),
                                      "stability": float(parts[3]), "difficulty": float(parts[4]),
                                      "last_review": "" if parts[9].strip() == "-" else parts[9].strip()})
                except ValueError:
                    continue
                continue
            m = _SM2_RE.match("!" + seg)
            if m:
                sm2.append((m.group(1), int(m.group(2)), int(m.group(3))))
    fm = frontmatter or {}
    due, ivl, ease = fm.get("sr-due"), fm.get("sr-interval"), fm.get("sr-ease")
    if due and ivl is not None and ease is not None:
        try:
            sm2.append((str(due)[:10], int(ivl), int(ease)))
        except (TypeError, ValueError):
            pass
    if fsrs_rows:
        rows = sorted(fsrs_rows, key=lambda r: (r["due"], r["interval"]))
        first = rows[0]
        seed = {"from": "sr-fsrs", "due": first["due"], "interval": min(r["interval"] for r in rows),
                "stability": min(r["stability"] for r in rows),
                "difficulty": max(r["difficulty"] for r in rows)}
        if first["last_review"]:
            seed["last_review"] = first["last_review"]
        return seed
    if sm2:
        return {"from": "sr-sm2", "due": min(r[0] for r in sm2),
                "interval": max(1, min(r[1] for r in sm2)), "ease": min(r[2] for r in sm2)}
    return None


def plan_sr_import(cards: Iterable[tuple[CardMeta, str, Mapping[str, Any] | None]],
                   result: ReplayResult) -> tuple[list[tuple[CardMeta, dict[str, Any]]], int]:
    """Cards with Spaced Repetition data and no events yet, plus the count skipped for having events."""
    found: list[tuple[CardMeta, dict[str, Any]]] = []
    skipped = 0
    for meta, text, frontmatter in cards:
        seed = sr_schedule(text, frontmatter)
        if seed is None:
            continue
        if meta.id in result.states:
            skipped += 1
        else:
            found.append((meta, seed))
    return found, skipped


def import_sr(review_dir: Path, cards: Iterable[tuple[CardMeta, str, Mapping[str, Any] | None]], *,
              device: str, now: datetime, fmt: str = "jsonl", dry_run: bool = False,
              cache_dir: Path | None = None) -> dict[str, Any]:
    """One-time import of Spaced Repetition schedules as ``import`` events. Card files are not changed."""
    found, skipped = plan_sr_import(cards, load(review_dir, cache_dir=cache_dir))
    events = [make_import_event(m.id, seed, device=device, now=now) for m, seed in found]
    path = append_events(review_dir, events, fmt=fmt) if events and not dry_run else None
    return {"imported": 0 if dry_run else len(events), "found": len(events), "skipped": skipped,
            "written": str(path) if path else None}


def make_import_event(card: str, seed: Mapping[str, Any], *, device: str, now: datetime) -> dict[str, Any]:
    obj = {"v": SCHEMA_VERSION, "ts": format_local(now), "device": device, "card": card,
           "mode": MODE_IMPORT, "import": dict(seed)}
    parse_event(obj)
    return obj


# --------------------------------------------------------------------------
# Helpers for a future `sb review status | queue | log` command (not wired yet)
# --------------------------------------------------------------------------
def status_report(review_dir: Path, cards: Iterable[CardMeta], now: datetime, *,
                  cache_dir: Path | None = None) -> dict[str, Any]:
    result = load(review_dir, cache_dir=cache_dir)
    report = summarize(cards, result, now)
    report["issue_list"] = [i.as_dict() for i in result.issues[:50]]
    return report


def queue_report(review_dir: Path, cards: Iterable[CardMeta], now: datetime, *,
                 new_per_day: int = DEFAULT_NEW_PER_DAY, domains: Sequence[str] = (),
                 read_card: Callable[[CardMeta], str] | None = None,
                 limit: int | None = None, section: str = DEFAULT_REVIEW_SECTION,
                 taxonomy: Mapping[str, PillarInfo] | None = None,
                 cache_dir: Path | None = None) -> dict[str, Any]:
    """Queue as JSON-ready data. With ``read_card`` each entry carries its questions.

    Pass ``taxonomy`` (from ``build_taxonomy``) so new cards come in the same order as in the
    Obsidian plugin: study domain, then pillar order, then topic position.
    """
    result = load(review_dir, cache_dir=cache_dir)
    metas = rank_cards(cards, taxonomy, domains) if taxonomy is not None else list(cards)
    q = build_queue(metas, result, now, new_per_day=new_per_day, domains=domains)

    def entry(meta: CardMeta, kind: str) -> dict[str, Any]:
        st = result.states.get(meta.id)
        row: dict[str, Any] = {"id": meta.id, "title": meta.title, "domain": meta.domain,
                               "path": meta.path, "kind": kind}
        if st is not None:
            row["due"] = format_utc(st.due)
            row["state"] = st.state
        if read_card is not None:
            row["questions"] = [{"q": i.q, "question": i.question, "answer": i.answer}
                                for i in extract_review_items(read_card(meta), section)]
        return row

    rows = [entry(m, "due") for m in q["due"]] + [entry(m, "new") for m in q["new"]]
    if limit is not None:
        rows = rows[:limit]
    return {"today": q["today"], "due_count": len(q["due"]), "new_count": len(q["new"]),
            "new_quota": q["new_quota"], "new_available": q["new_available"],
            "cycle_breaks": len(q["cycle_breaks"]), "cards": rows}


def log_review(review_dir: Path, card: str, *, device: str, now: datetime, mode: str = "agent",
               rating: int | None = None, items: Sequence[Mapping[str, Any]] | None = None,
               grader: str | None = None, fmt: str = "jsonl",
               cache_dir: Path | None = None) -> dict[str, Any]:
    """Append one review and return the card's new state."""
    event = make_event(card, device=device, now=now, mode=mode, rating=rating, items=items, grader=grader)
    path = append_event(review_dir, event, fmt=fmt)
    result = load(review_dir, cache_dir=cache_dir)
    st = result.states.get(card)
    return {"written": str(path), "event": event, "state": st.as_dict() if st else None}

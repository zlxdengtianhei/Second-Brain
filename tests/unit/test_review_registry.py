"""Tests for secondbrain.review_registry against the shared vectors in tests/fixtures/review/."""

from __future__ import annotations

import json
import math
import multiprocessing
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from secondbrain import review_registry as rr

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "review"
VECTORS_PATH = FIXTURES / "vectors.json"
VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
QUEUE = json.loads((FIXTURES / "queue.json").read_text(encoding="utf-8"))
REL_TOL = 1e-6


def _file_text(content) -> str:
    if isinstance(content, str):
        return content
    lines = [c if isinstance(c, str) else json.dumps(c, ensure_ascii=False) for c in content]
    return "\n".join(lines) + "\n"


def _write_files(root: Path, files: dict) -> Path:
    review_dir = root / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (review_dir / name).write_text(_file_text(content), encoding="utf-8")
    return review_dir


def _replay_vector(tmp_path: Path, vector: dict) -> rr.ReplayResult:
    return rr.load(_write_files(tmp_path, vector["files"]))


def _check_fields(actual: dict, expected: dict, label: str) -> None:
    for key, want in expected.items():
        if key == "same_as":
            continue
        got = actual[key]
        if isinstance(want, float) or key in ("stability", "difficulty"):
            assert math.isclose(got, want, rel_tol=REL_TOL), f"{label}.{key}: {got} != {want}"
        else:
            assert got == want, f"{label}.{key}: {got!r} != {want!r}"


# --------------------------------------------------------------------------
# Replay vectors
# --------------------------------------------------------------------------
@pytest.mark.parametrize("vector", VECTORS["vectors"], ids=lambda v: v["name"])
def test_vector_expectations(tmp_path: Path, vector: dict) -> None:
    result = _replay_vector(tmp_path, vector)
    assert len(result.issues) == vector["expect"]["issues"], [i.as_dict() for i in result.issues]
    for card, want in vector["expect"]["cards"].items():
        assert card in result.states, f"{card} missing"
        actual = result.states[card].as_dict()
        _check_fields(actual, want, card)
        if "same_as" in want:
            other = result.states[want["same_as"]].as_dict()
            _check_fields(actual, other, f"{card}~{want['same_as']}")


@pytest.mark.parametrize("vector", VECTORS["vectors"], ids=lambda v: v["name"])
def test_vector_snapshot(tmp_path: Path, vector: dict) -> None:
    result = _replay_vector(tmp_path, vector)
    snapshot = {c: s.as_dict() for c, s in sorted(result.states.items())}
    if os.environ.get("SB_UPDATE_VECTORS") == "1":
        pytest.skip("snapshot is being regenerated")
    assert "snapshot" in vector, "run with SB_UPDATE_VECTORS=1 to write snapshots"
    assert sorted(snapshot) == sorted(vector["snapshot"]["cards"])
    for card, want in vector["snapshot"]["cards"].items():
        _check_fields(snapshot[card], want, card)


def test_update_snapshots(tmp_path: Path) -> None:
    """Writes snapshots when SB_UPDATE_VECTORS=1; otherwise does nothing."""
    if os.environ.get("SB_UPDATE_VECTORS") != "1":
        return
    data = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
    for i, vector in enumerate(data["vectors"]):
        result = _replay_vector(tmp_path / str(i), vector)
        vector["snapshot"] = {"cards": {c: s.as_dict() for c, s in sorted(result.states.items())}}
    VECTORS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_replay_is_independent_of_file_order(tmp_path: Path) -> None:
    vector = next(v for v in VECTORS["vectors"] if v["name"] == "multi-device-interleaved")
    a = _replay_vector(tmp_path / "a", vector)
    reversed_files = dict(reversed(list(vector["files"].items())))
    events, _ = [], None
    for name, content in reversed_files.items():
        evs, _ = rr.parse_log_text(name, _file_text(content))
        events.extend(reversed(evs))
    b = rr.replay(events)
    assert {k: v.as_dict() for k, v in a.states.items()} == {k: v.as_dict() for k, v in b.states.items()}


# --------------------------------------------------------------------------
# Question ids, review items, Spaced Repetition import
# --------------------------------------------------------------------------
@pytest.mark.parametrize("case", VECTORS["question_ids"], ids=lambda c: c["text"] or "empty")
def test_question_id(case: dict) -> None:
    if "q" in case:
        assert rr.question_id(case["text"]) == case["q"]
    else:
        assert rr.question_id(case["text"]) == rr.question_id(case["same_as"])


@pytest.mark.parametrize("case", VECTORS["review_items"], ids=lambda c: c["file"])
def test_extract_review_items(case: dict) -> None:
    text = (FIXTURES / case["file"]).read_text(encoding="utf-8")
    items = rr.extract_review_items(text)
    assert [[i.question, i.answer] for i in items] == case["items"]
    assert all(i.q == rr.question_id(i.question) for i in items)


@pytest.mark.parametrize("case", VECTORS["sr_import"], ids=lambda c: c["name"])
def test_sr_schedule(case: dict) -> None:
    assert rr.sr_schedule(case["text"], case["frontmatter"]) == case["seed"]


def test_import_event_round_trip(tmp_path: Path) -> None:
    seed = rr.sr_schedule("Q::A <!--SR:!2026-03-20,5,250-->")
    now = datetime(2026, 3, 15, 10, tzinfo=timezone(timedelta(hours=1)))
    ev = rr.make_import_event("ex_kappa", seed, device="obs-a", now=now)
    rr.append_event(tmp_path, ev)
    st = rr.load(tmp_path).states["ex_kappa"]
    assert (st.state, st.reps, st.stability) == ("review", 0, 5.0)
    assert rr.format_utc(st.due) == "2026-03-20T03:00:00.000Z"


# --------------------------------------------------------------------------
# Status and queue
# --------------------------------------------------------------------------
def _queue_cards() -> list[rr.CardMeta]:
    return [
        rr.CardMeta(id=c["id"], title=c["title"], domain=c["domain"], kind=c.get("kind", ""),
                    status=c.get("status", ""), rank=c.get("rank", 0),
                    relations=tuple((r[0], r[1]) for r in c.get("relations", [])))
        for c in QUEUE["cards"]
    ]


def _queue_now() -> datetime:
    return rr.parse_ts(QUEUE["now"])


def test_summary(tmp_path: Path) -> None:
    result = rr.load(_write_files(tmp_path, QUEUE["files"]))
    summary = rr.summarize(_queue_cards(), result, _queue_now())
    want = QUEUE["summary"]
    for key in ("today", "totals", "domains", "new_today", "reviews_today", "streak", "orphans"):
        assert summary[key] == want[key], key


@pytest.mark.parametrize("case", QUEUE["queues"], ids=lambda c: c["name"])
def test_queue(tmp_path: Path, case: dict) -> None:
    result = rr.load(_write_files(tmp_path, QUEUE["files"]))
    q = rr.build_queue(_queue_cards(), result, _queue_now(), new_per_day=case["new_per_day"],
                       domains=case["domains"])
    assert [m.id for m in q["due"]] == case["due"]
    assert [m.id for m in q["new"]] == case["new"]
    for key in ("new_quota", "new_available", "cycle_breaks"):
        assert q[key] == case[key], key


def test_learning_order_keeps_every_card_on_long_cycles() -> None:
    cards = [rr.CardMeta(id=f"c{i:03d}", relations=(("depends_on", f"c{(i + 1) % 50:03d}"),))
             for i in range(50)]
    ordered, breaks = rr.learning_order(cards)
    assert sorted(c.id for c in ordered) == sorted(c.id for c in cards)
    assert breaks == ["c000"]
    # c000 depends on c001, so after breaking at c000 the chain unwinds from c049 down.
    assert [c.id for c in ordered[:3]] == ["c000", "c049", "c048"]


def test_status_requires_aware_now(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        rr.summarize([], rr.ReplayResult(states={}, issues=[]), datetime(2026, 3, 10, 12))


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------
NOW = datetime(2026, 3, 2, 9, 0, 0, 123456, tzinfo=timezone(timedelta(hours=1)))


def test_make_event_uses_min_item_rating_and_validates() -> None:
    ev = rr.make_event("ex_a", device="cli-1", now=NOW, mode="agent",
                       items=[{"q": "h:1", "rating": 3}, {"q": "h:2", "rating": 2}], grader="llm:m")
    assert ev["rating"] == 2 and ev["ts"] == "2026-03-02T09:00:00.123+01:00"
    with pytest.raises(rr.EventError):
        rr.make_event("ex_a", device="cli-1", now=NOW, rating=5)
    with pytest.raises(rr.EventError):
        rr.make_event("ex_a", device="bad device", now=NOW, rating=3)
    with pytest.raises(rr.EventError):
        rr.make_event("ex_a", device="cli-1", now=NOW)


def test_append_creates_file_and_repairs_torn_line(tmp_path: Path) -> None:
    ev = rr.make_event("ex_a", device="cli-1", now=NOW, rating=3)
    path = rr.append_event(tmp_path, ev)
    assert path.name == "events-cli-1.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"v":1,"ts":"2026-03-02T09:0')  # a write cut off by a crash
    ev2 = rr.make_event("ex_a", device="cli-1", now=NOW + timedelta(minutes=10), rating=3)
    rr.append_event(tmp_path, ev2)
    events, issues = rr.read_logs(tmp_path)
    assert [e.rating for e in events] == [3, 3]
    assert len(issues) == 1 and issues[0].line == 2


def test_append_md_format_writes_header_once(tmp_path: Path) -> None:
    for minutes in (0, 10):
        rr.append_event(tmp_path, rr.make_event("ex_a", device="obs-m", now=NOW + timedelta(minutes=minutes),
                                                 rating=3), fmt="md")
    text = (tmp_path / "events-obs-m.md").read_text(encoding="utf-8")
    assert text.startswith("---\nformat: secondbrain-review-log\n---\n") and text.count("---") == 2
    events, issues = rr.read_logs(tmp_path)
    assert len(events) == 2 and not issues


def test_append_refuses_invalid_event(tmp_path: Path) -> None:
    with pytest.raises(rr.EventError):
        rr.append_event(tmp_path, {"v": 1, "ts": "2026-03-02T09:00:00+01:00", "device": "cli-1",
                                   "card": "ex_a", "mode": "flip", "rating": 0})
    assert not list(tmp_path.iterdir())


def _append_many(args: tuple[str, int]) -> None:
    directory, worker = args
    base = datetime(2026, 3, 2, 9, tzinfo=timezone.utc)
    for i in range(200):
        ev = rr.make_event(f"ex_{worker}_{i}", device="cli-shared", now=base + timedelta(seconds=i),
                           rating=3, feedback="x" * 300)
        rr.append_event(Path(directory), ev)


def test_concurrent_appends_do_not_interleave(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(4) as pool:
        pool.map(_append_many, [(str(tmp_path), w) for w in range(4)])
    events, issues = rr.read_logs(tmp_path)
    assert not issues
    assert len(events) == 800


# --------------------------------------------------------------------------
# CLI helpers (sb review status | queue | log)
# --------------------------------------------------------------------------
def test_cli_helpers_end_to_end(tmp_path: Path) -> None:
    review_dir = _write_files(tmp_path, QUEUE["files"])
    cards = _queue_cards()
    texts = {"ex_binary_search": (FIXTURES / "cards" / "binary-search.md").read_text(encoding="utf-8")}
    now = _queue_now()

    status = rr.status_report(review_dir, cards, now)
    assert status["totals"]["due"] == 2 and status["issue_list"] == []

    queue = rr.queue_report(review_dir, cards, now, new_per_day=10,
                            read_card=lambda m: texts.get(m.id, ""))
    assert [c["id"] for c in queue["cards"][:2]] == ["ex_array", "ex_tcp"]
    bs = next(c for c in queue["cards"] if c["id"] == "ex_binary_search")
    assert len(bs["questions"]) == 4 and bs["kind"] == "new"

    items = [{"q": q["q"], "rating": r} for q, r in zip(bs["questions"], (3, 3, 2, 3))]
    out = rr.log_review(review_dir, "ex_binary_search", device="cli-test", now=now, items=items,
                        grader="llm:example")
    assert out["event"]["rating"] == 2 and out["state"]["state"] == "learning"
    after = rr.status_report(review_dir, cards, now)
    assert after["new_today"] == 2 and after["totals"]["unlearned"] == 9


def test_default_device_id_shape() -> None:
    device = rr.default_device_id()
    assert device.startswith("cli-") and len(device) == 12

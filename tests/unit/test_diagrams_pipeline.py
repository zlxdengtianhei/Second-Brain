"""Pregate, fallback and process: draw, one rewrite, then skip."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from secondbrain.diagrams.ir import Card, Issue
from secondbrain.diagrams.pipeline import (
    Policy, Rewrite, Skip, build_packet, fallback, pregate, process, read_results,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "diagrams"
CARDS = {c["ref"]: Card.from_dict(c) for c in json.loads((FIX / "cards.json").read_text(encoding="utf-8"))}
ENTRIES = {x["ref"]: x for x in json.loads((FIX / "irs.json").read_text(encoding="utf-8"))}
SEARCH = CARDS["algorithms/binary-search"]
NEG = CARDS["operating-systems/process-vs-thread"]


def test_fallback_rewrites_once_then_skips():
    errs = [Issue("V5", "plain chain"), Issue("V4", "«x» shares no wording", "nodes[0].label")]
    first = fallback(errs, attempt=0)
    assert first == Rewrite(attempt=1, errors=("V5 plain chain", "V4 [nodes[0].label] «x» shares no wording"))
    second = fallback(errs, attempt=1)
    assert isinstance(second, Skip) and second.reason.startswith("no valid IR after 2 attempt(s): V5 plain chain")
    assert isinstance(fallback(errs, attempt=0, max_rewrites=0), Skip)
    with pytest.raises(ValueError):
        fallback([], attempt=0)


def test_fallback_reason_is_bounded():
    reason = fallback([f"V3 e{i}" for i in range(9)], attempt=1).reason
    assert "and 6 more" in reason


def test_valid_ir_draws_with_marker():
    out = process(SEARCH, ENTRIES["algorithms/binary-search"]["ir"])
    assert out.status == "draw" and out.attempt == 0
    assert out.mermaid.splitlines()[:2] == [out.mermaid.splitlines()[0], "flowchart TB"]
    assert out.mermaid.startswith("%% diagram v1 props=E,P1,P2,P3 sha=")


def test_model_skip_is_accepted_with_its_reason():
    out = process(SEARCH, {"decision": "skip", "reason": "linear process of 3 steps"})
    assert out.to_dict() == {"ref": "algorithms/binary-search", "status": "skip", "attempt": 0,
                             "reason": "linear process of 3 steps"}


def test_negative_control_rewrite_then_skip():
    entry = ENTRIES["operating-systems/process-vs-thread"]
    first = process(NEG, entry["ir"], attempt=0, policy=Policy(skip_kinds=()))
    assert first.status == "rewrite"
    codes = {e.split()[0] for e in first.errors}
    assert {"V4", "V5"} <= codes
    sent = first.rewrite["input"]["cards"][0]
    assert sent["previous"] == entry["ir"] and sent["errors"] == first.errors
    assert first.rewrite["attempt"] == 1 and "Card diagram" in first.rewrite["prompt"]
    second = process(NEG, entry["rewrite"], attempt=1)
    assert second.status == "skip" and second.reason.startswith("contrast card")
    stubborn = process(NEG, entry["ir"], attempt=1)
    assert stubborn.status == "skip" and stubborn.reason.startswith("no valid IR after 2 attempt(s)")


def test_missing_answer_counts_as_v1():
    out = process(SEARCH, None)
    assert out.status == "rewrite" and out.errors == ["V1 no IR returned for this card"]


def test_pregate_by_kind_and_domain():
    assert pregate(NEG).startswith("contrast card")
    assert pregate(SEARCH) is None
    assert pregate(SEARCH, Policy(skip_domains=("algorithms",))).startswith("domain algorithms")
    assert pregate(Card(title="t", essence="e", propositions=())) is not None


def test_packet_holds_only_cards_that_need_the_model():
    packet, skipped = build_packet(list(CARDS.values()))
    sent = [c["ref"] for c in packet["input"]["cards"]]
    assert sent == ["networking/etag-revalidation", "operating-systems/segmented-paging", "algorithms/binary-search"]
    assert [s.ref for s in skipped] == ["operating-systems/process-vs-thread"]
    first = packet["input"]["cards"][0]
    assert first["propositions"]["P2"].startswith("缓存过期后") and first["E"].startswith("缓存过期后")
    assert build_packet([NEG])[0] is None


def test_read_results_by_ref_by_position_and_garbage():
    refs = ["a", "b"]
    assert read_results({"results": [{"ref": "b", "ir": 2}, {"ref": "a", "ir": 1}]}, refs) == {"a": 1, "b": 2}
    assert read_results(json.dumps({"results": [{"ir": 1}]}), refs) == {"a": 1, "b": None}
    assert read_results("not json", refs) == {"a": None, "b": None}
    assert read_results({"answer": []}, refs) == {"a": None, "b": None}


def test_rewrite_round_trip_on_a_fixable_error():
    bad = copy.deepcopy(ENTRIES["algorithms/binary-search"]["ir"])
    bad["nodes"][2]["label"] = "左指针右移"
    first = process(SEARCH, bad)
    assert first.status == "rewrite" and first.errors[0].startswith("V4 [nodes[2].label]")
    fixed = process(SEARCH, ENTRIES["algorithms/binary-search"]["ir"], attempt=first.rewrite["attempt"])
    assert fixed.status == "draw" and fixed.attempt == 1


def test_read_results_ignores_non_string_refs():
    assert read_results({"results": [{"ref": ["x"], "ir": {}}, {"ref": "y", "ir": 1}]}, ["x", "y"]) == \
        {"x": None, "y": 1}


def test_malformed_answer_costs_one_card_not_the_batch(tmp_path, capsys):
    from secondbrain.diagrams.__main__ import main
    cards = [c.to_dict() for c in CARDS.values() if c.kind != "contrast"]
    (tmp_path / "cards.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cards),
                                          encoding="utf-8")
    main(["packets", str(tmp_path / "cards.jsonl"), str(tmp_path / "p")])
    capsys.readouterr()
    refs = [c["ref"] for c in cards]
    broken = copy.deepcopy(ENTRIES["algorithms/binary-search"]["ir"])
    broken["edges"][0]["from"] = ["mid"]
    broken["groups"] = [{"label": "g", "members": [{"key": "mid"}]}]
    answer = {"results": [
        {"ref": refs[0], "ir": {"decision": "skip", "reason": "linear"}},
        {"ref": refs[1], "ir": ENTRIES[refs[1]]["ir"]},
        {"ref": refs[2], "ir": broken},
        {"ref": ["junk"], "ir": {}},
    ]}
    (tmp_path / "p" / "packet-001.answer.json").write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    assert main(["apply", str(tmp_path / "cards.jsonl"), str(tmp_path / "p" / "packet-001.answer.json"),
                 "--out", str(tmp_path / "o")]) == 0
    rows = [json.loads(x) for x in (tmp_path / "o" / "outcomes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["ref"], r["status"]) for r in rows] == [(refs[0], "skip"), (refs[1], "draw"), (refs[2], "rewrite")]
    assert all(e.startswith("V1") for e in rows[2]["errors"])


def test_unexpected_checker_error_becomes_a_v1_issue(monkeypatch):
    from secondbrain.diagrams import pipeline

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(pipeline, "emit", boom)
    out = process(SEARCH, ENTRIES["algorithms/binary-search"]["ir"])
    assert out.status == "rewrite" and out.errors == ["V1 IR could not be checked: RuntimeError: bug"]


def test_render_feedback_turns_back_wide_overlapping_or_broken_diagrams():
    from secondbrain.diagrams.pipeline import render_feedback
    ir_data = ENTRIES["algorithms/binary-search"]["ir"]
    drawn = process(SEARCH, ir_data)
    assert render_feedback(drawn, {"ok": True, "width": 600, "label_overlaps": 0}, SEARCH, ir_data) is drawn
    assert render_feedback(drawn, None, SEARCH, ir_data) is drawn
    wide = render_feedback(drawn, {"ok": True, "width": 812}, SEARCH, ir_data)
    assert wide.status == "rewrite" and wide.errors[0].startswith("V14 renders 812 px")
    assert wide.rewrite["input"]["cards"][0]["previous"] == ir_data
    crowded = render_feedback(drawn, {"ok": True, "width": 500, "label_overlaps": 2}, SEARCH, ir_data)
    assert crowded.errors == ["V15 2 pair(s) of edge labels overlap after layout: shorten or drop edge labels "
                              "that sit side by side"]
    broken = render_feedback(drawn, {"ok": False, "error": "Parse error"}, SEARCH, ir_data)
    assert broken.errors[0].startswith("V9")
    later = process(SEARCH, ir_data, attempt=1)
    assert render_feedback(later, {"ok": True, "width": 900}, SEARCH, ir_data).status == "skip"
    skipped = process(SEARCH, {"decision": "skip", "reason": "x"})
    assert render_feedback(skipped, {"ok": True, "width": 900}, SEARCH, ir_data) is skipped


def test_payload_tells_the_model_about_the_old_diagram():
    from secondbrain.diagrams.pipeline import card_payload
    assert "old_diagram" not in card_payload(SEARCH)
    with_old = Card.from_dict({**SEARCH.to_dict(), "old_diagram": "broken"})
    assert card_payload(with_old)["old_diagram"] == "broken"
    assert Card.from_dict({**SEARCH.to_dict(), "old_diagram": "maybe"}).old_diagram is None

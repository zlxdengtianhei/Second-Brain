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
    assert out.mermaid.startswith("%% diagram v1 props=P1,P2,P3 sha=")


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

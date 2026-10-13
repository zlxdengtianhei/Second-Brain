"""Old-diagram audit: verdict rules and packets."""
from __future__ import annotations

import pytest

from secondbrain.diagrams import oldcheck

CARD = {"ref": "x.md", "title": "t", "kind": "mechanism", "essence": "e", "propositions": ["p1", "p2"],
        "old_mermaid": "flowchart LR\n  a --> b"}


@pytest.mark.parametrize("answer, verdict, category", [
    ({"verdict": "remove", "category": "restates", "reason": "chain"}, "remove", "restates"),
    ({"verdict": "remove", "category": "misleading", "reason": "wrong"}, "remove", "misleading"),
    ({"verdict": "keep", "category": "useful", "reason": "loop"}, "keep", "useful"),
    ({"verdict": "remove", "category": "useful"}, "keep", "unsure"),
    ({"verdict": "keep", "category": "restates"}, "keep", "unsure"),
    ({"verdict": "drop", "category": "restates"}, "keep", "unsure"),
    ({}, "keep", "unsure"),
    (None, "keep", "unsure"),
    ("remove", "keep", "unsure"),
])
def test_judge_removes_only_on_a_clear_verdict(answer, verdict, category):
    out = oldcheck.judge("x.md", answer)
    assert (out["ref"], out["status"], out["verdict"], out["category"]) == ("x.md", "audit", verdict, category)


def test_packet_and_answers():
    packet = oldcheck.build_audit_packet([CARD])
    entry = packet["input"]["cards"][0]
    assert entry == {"ref": "x.md", "title": "t", "kind": "mechanism", "E": "e",
                     "propositions": {"P1": "p1", "P2": "p2"}, "old_mermaid": "flowchart LR\n  a --> b"}
    assert "keep or remove" in packet["prompt"] and "When unsure, keep" in packet["prompt"]
    assert oldcheck.build_audit_packet([]) is None
    assert oldcheck.read_verdicts('{"results":[{"ref":"x.md","verdict":"keep"},{"ref":["y"]}]}', ["x.md", "y"]) == \
        {"x.md": {"ref": "x.md", "verdict": "keep"}, "y": None}
    assert oldcheck.read_verdicts("not json", ["x.md"]) == {"x.md": None}

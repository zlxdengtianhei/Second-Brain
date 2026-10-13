"""Staleness marker: hash of the card text, written as the diagram's first line."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from secondbrain.diagrams.ir import Card
from secondbrain.diagrams.pipeline import process
from secondbrain.diagrams.staleness import check_staleness, content_hash, read_marker

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "diagrams"
CARDS = {c["ref"]: Card.from_dict(c) for c in json.loads((FIX / "cards.json").read_text(encoding="utf-8"))}
IRS = {x["ref"]: x["ir"] for x in json.loads((FIX / "irs.json").read_text(encoding="utf-8"))}
CARD = CARDS["operating-systems/segmented-paging"]


def drawn(card: Card = CARD) -> str:
    out = process(card, IRS["operating-systems/segmented-paging"])
    assert out.status == "draw", out.errors
    return out.mermaid


def test_marker_is_the_first_line_and_lists_cited_propositions():
    first = drawn().splitlines()[0]
    assert first == f"%% diagram v1 props=P1,P2,P3,P4 sha={content_hash(CARD)}"
    m = read_marker(drawn())
    assert m.version == 1 and m.props == ("P1", "P2", "P3", "P4") and m.sha == content_hash(CARD)


def test_hash_ignores_whitespace_but_not_wording_order_or_additions():
    base = content_hash(CARD)
    spaced = dataclasses.replace(CARD, propositions=tuple("  " + p.replace("，", "， ") + "\n" for p in CARD.propositions))
    assert content_hash(dataclasses.replace(CARD, essence=" " + CARD.essence + " ")) == base
    assert content_hash(spaced) != base  # an inserted space inside CJK text is a wording change
    assert content_hash(dataclasses.replace(CARD, propositions=CARD.propositions[::-1])) != base
    assert content_hash(dataclasses.replace(CARD, propositions=CARD.propositions + ("新命题",))) != base
    assert content_hash(dataclasses.replace(CARD, essence="改写的本质")) != base


def test_fresh_until_the_card_text_changes():
    mm = drawn()
    assert check_staleness(mm, CARD).status == "fresh"
    updated = dataclasses.replace(CARD, propositions=CARD.propositions + ("快表命中时只需访问内存 1 次。",))
    s = check_staleness(mm, updated)
    assert s.status == "stale" and s.needs_redraw and "changed" in s.reason


def test_stale_when_a_cited_proposition_is_gone():
    shorter = dataclasses.replace(CARD, propositions=CARD.propositions[:3])
    s = check_staleness(drawn(), shorter)
    assert s.status == "stale" and "P4" in s.reason


def test_legacy_diagram_without_marker_is_unmarked():
    s = check_staleness("flowchart LR\n  a --> b", CARD)
    assert s.status == "unmarked" and s.needs_redraw
    assert read_marker("%% some other comment\nflowchart TB") is None


def test_other_method_version_is_stale():
    mm = drawn().replace("%% diagram v1 ", "%% diagram v0 ", 1)
    assert check_staleness(mm, CARD).status == "stale"


def test_every_proposition_is_in_the_hash():
    mm = drawn()
    for i in range(len(CARD.propositions)):
        props = list(CARD.propositions)
        props[i] = props[i].replace("。", "，并且有改动。")
        assert check_staleness(mm, dataclasses.replace(CARD, propositions=tuple(props))).status == "stale", i

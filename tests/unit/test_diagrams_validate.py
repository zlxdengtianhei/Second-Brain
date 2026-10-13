"""V1-V8 and W0-W2: one passing and at least one failing case per check."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from secondbrain.diagrams.ir import IR_JSON_SCHEMA, Card, parse_ir
from secondbrain.diagrams.validate import Limits, validate

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "diagrams"
CARDS = {c["ref"]: Card.from_dict(c) for c in json.loads((FIX / "cards.json").read_text(encoding="utf-8"))}
IRS = {x["ref"]: x for x in json.loads((FIX / "irs.json").read_text(encoding="utf-8"))}

SEARCH = CARDS["algorithms/binary-search"]
PAGING = CARDS["operating-systems/segmented-paging"]
ETAG = CARDS["networking/etag-revalidation"]


def ir(ref: str) -> dict:
    return copy.deepcopy(IRS[ref]["ir"])


def codes(data, card, limits=Limits()) -> list[str]:
    return [i.code for i in validate(data, card, limits).errors]


@pytest.mark.parametrize("ref", ["algorithms/binary-search", "operating-systems/segmented-paging",
                                 "networking/etag-revalidation"])
def test_fixture_irs_pass_every_check(ref):
    report = validate(ir(ref), CARDS[ref])
    assert report.ok, [str(i) for i in report.errors]
    assert report.warnings == []


def test_skip_needs_only_a_reason():
    assert validate({"decision": "skip", "reason": "contrast card"}, SEARCH).ok
    assert codes({"decision": "skip"}, SEARCH) == ["V1"]
    assert codes({"decision": "skip", "reason": "   "}, SEARCH) == ["V1"]


# V1 ---------------------------------------------------------------------------

@pytest.mark.parametrize("mutate, where", [
    (lambda d: d.update(decision="maybe"), "decision"),
    (lambda d: d.update(form="mindmap"), "form"),
    (lambda d: d.pop("question"), "question"),
    (lambda d: d.update(nodes=[]), "nodes"),
    (lambda d: d["nodes"].append(dict(d["nodes"][0])), "nodes[7].key"),
    (lambda d: d["nodes"][0].update(role="state"), "nodes[0].role"),
    (lambda d: d["nodes"][0].update(label=" \n "), "nodes[0].label"),
    (lambda d: d["nodes"][0].update(p="P1"), "nodes[0].p"),
    (lambda d: d["edges"][0].update(to="nowhere"), "edges[0].to"),
    (lambda d: d.update(messages=[{"from": "mid", "to": "cmp", "text": "x", "p": ["P1"]}]), "messages"),
    (lambda d: d.update(groups=[{"label": "g", "members": ["ghost"]}]), "groups[0].members"),
    (lambda d: d.update(groups=[{"label": "g", "members": ["mid"]}, {"label": "h", "members": ["mid"]}]),
     "groups[1].members"),
    (lambda d: d["edges"].extend([{"from": "mid", "to": "mid"}, {"from": "mid", "to": "mid"}]), "edges[9]"),
])
def test_v1_rejects_malformed_ir(mutate, where):
    data = ir("algorithms/binary-search")
    mutate(data)
    report = validate(data, SEARCH)
    assert report.ir is None
    assert {i.code for i in report.errors} == {"V1"}, [str(i) for i in report.errors]
    assert report.errors[0].where == where


def test_v1_rejects_non_object_and_groups_outside_flow():
    assert codes([], SEARCH) == ["V1"]
    data = ir("networking/etag-revalidation")
    data["groups"] = [{"label": "客户端", "members": ["b", "c"]}]
    assert codes(data, ETAG) == ["V1"]


def test_unknown_keys_are_warnings_not_errors():
    data = ir("algorithms/binary-search")
    data["direction"] = "LR"
    data["nodes"][0]["color"] = "red"
    report = validate(data, SEARCH)
    assert report.ok
    assert [i.code for i in report.warnings] == ["W0", "W0"]


# V2 ---------------------------------------------------------------------------

def test_v2_size_bounds_per_form():
    data = ir("algorithms/binary-search")
    data["nodes"] += [{"key": f"x{i}", "label": "取区间中点 mid", "p": ["P1"]} for i in range(3)]
    assert "V2" in codes(data, SEARCH)  # 10 nodes > 9
    small = {"decision": "draw", "form": "sequence", "question": "q",
             "nodes": [{"key": "b", "label": "浏览器", "p": ["P1"]}],
             "messages": [{"from": "b", "to": "b", "text": "首次请求", "p": ["P1"]}] * 3}
    assert "V2" in codes(small, ETAG)  # 1 participant < 2
    assert "V2" not in codes(small, ETAG, Limits(size={**Limits().size, "sequence": (1, 5)}))


# V3 ---------------------------------------------------------------------------

@pytest.mark.parametrize("refs", [[], ["P9"], ["X1"], ["p1"]])
def test_v3_every_label_cites_an_existing_proposition(refs):
    data = ir("algorithms/binary-search")
    data["nodes"][0]["p"] = refs
    report = validate(data, SEARCH)
    assert [i.where for i in report.errors if i.code == "V3"] == ["nodes[0].label"]


def test_v3_essence_is_citable_and_bare_edges_need_no_citation():
    data = ir("algorithms/binary-search")
    data["nodes"][0] = {"key": "mid", "label": "有序区间减半", "p": ["E"]}
    assert "V3" not in codes(data, SEARCH)
    assert "p" not in data["edges"][0]


# V4 ---------------------------------------------------------------------------

def test_v4_label_must_reuse_the_cited_wording():
    data = ir("algorithms/binary-search")
    data["nodes"][2]["label"] = "左指针右移"
    report = validate(data, SEARCH)
    assert [(i.code, i.where) for i in report.errors] == [("V4", "nodes[2].label")]


def test_v4_exempt_labels_and_threshold_parameter():
    data = ir("algorithms/binary-search")
    assert data["edges"][-1]["label"] == "否"
    assert validate(data, SEARCH).ok
    data["nodes"][2]["label"] = "lo = mid + 1 后继续"  # 3 of 6 tokens grounded
    assert "V4" not in codes(data, SEARCH)
    assert "V4" in codes(data, SEARCH, Limits(grounding_min=0.8))


# V5 ---------------------------------------------------------------------------

def _chain(n: int) -> dict:
    labels = ["取区间中点 mid", "比较 a[mid] 与目标值", "lo = mid + 1", "返回 mid", "返回 -1 表示未找到"]
    nodes = [{"key": f"k{i}", "label": labels[i], "p": ["P1" if i < 2 else "P2" if i < 4 else "P3"]}
             for i in range(n)]
    edges = [{"from": f"k{i}", "to": f"k{i + 1}"} for i in range(n - 1)]
    return {"decision": "draw", "form": "flow", "question": "q", "nodes": nodes, "edges": edges}


def test_v5_plain_chain_has_no_gain():
    assert codes(_chain(5), SEARCH) == ["V5"]


def test_v5_merge_or_loop_or_labelled_branch_is_gain():
    merge = _chain(5)
    merge["edges"].append({"from": "k0", "to": "k4"})
    assert codes(merge, SEARCH) == []
    loop = _chain(5)
    loop["edges"].append({"from": "k4", "to": "k0"})
    assert codes(loop, SEARCH) == []
    branch = _chain(5)
    branch["nodes"][1]["role"] = "decision"
    branch["edges"].append({"from": "k1", "to": "k4", "label": "否", "p": ["P2"]})
    branch["edges"][1]["label"] = "是"
    branch["edges"][1]["p"] = ["P2"]
    assert codes(branch, SEARCH) == []


def test_v5_sequence_needs_three_messages_and_notes_do_not_count():
    data = ir("networking/etag-revalidation")
    data["messages"] = data["messages"][:2] + data["messages"][-1:]
    assert codes(data, ETAG) == ["V5"]


def test_v5_structure_needs_relations_between_parts():
    data = {"decision": "draw", "form": "structure", "question": "q",
            "nodes": [{"key": k, "label": label, "p": ["P1"]} for k, label in
                      [("a", "段号"), ("b", "段内页号"), ("c", "页内偏移"), ("d", "逻辑地址")]],
            "edges": [{"from": "a", "to": "d", "label": "组成逻辑地址", "p": ["P1"]},
                      {"from": "b", "to": "d", "label": "组成逻辑地址", "p": ["P1"]}]}
    assert "V5" in codes(data, PAGING)
    data["edges"].append({"from": "c", "to": "d", "label": "组成逻辑地址", "p": ["P1"]})
    assert "V5" not in codes(data, PAGING)


# V6 ---------------------------------------------------------------------------

def test_v6_label_widths_count_latin_as_half():
    data = ir("algorithms/binary-search")
    data["nodes"][0]["label"] = "取区间中点 mid " + "mid " * 8  # 8 + 4.5*0.5... = 26 wide
    assert "V6" in codes(data, SEARCH)
    data = ir("algorithms/binary-search")
    data["edges"][1]["label"] = "小于目标值" * 3  # 15 > 14
    assert "V6" in codes(data, SEARCH)


def test_v6_decision_labels_have_a_tighter_limit():
    data = ir("algorithms/binary-search")
    data["nodes"][1]["label"] = "比较 a[mid] 与目标值的大小"  # 13 > 12
    assert codes(data, SEARCH) == ["V6"]
    assert codes(data, SEARCH, Limits(decision_label_max=None)) == []


def test_v6_message_limit():
    data = ir("networking/etag-revalidation")
    data["messages"][3]["text"] = "缓存过期后浏览器发送带 If-None-Match 的条件请求"
    assert codes(data, ETAG) == ["V6"]


# V7 ---------------------------------------------------------------------------

def test_v7_decision_branches_need_conditions():
    data = ir("algorithms/binary-search")
    del data["edges"][1]["label"]
    assert "V7" in codes(data, SEARCH)


def test_v7_structure_links_need_labels():
    data = {"decision": "draw", "form": "structure", "question": "q",
            "nodes": [{"key": k, "label": label, "p": ["P1"]} for k, label in
                      [("a", "段号"), ("b", "段内页号"), ("c", "页内偏移"), ("d", "逻辑地址")]],
            "edges": [{"from": x, "to": "d", "label": "组成逻辑地址", "p": ["P1"]} for x in "abc"]}
    data["nodes"][3]["p"] = ["P1", "P4"]
    assert codes(data, PAGING) == []
    del data["edges"][0]["label"]
    assert codes(data, PAGING) == ["V7"]


# V8 ---------------------------------------------------------------------------

def test_v8_multi_proposition_card_needs_two_cited():
    data = _chain(4)
    for n in data["nodes"]:
        n["label"], n["p"] = "取区间中点 mid", ["P1"]
    data["edges"].append({"from": "k3", "to": "k0"})
    assert codes(data, SEARCH) == ["V8"]
    one_prop = Card(title="t", essence="e", propositions=("取区间中点 mid",))
    assert codes(data, one_prop) == []


# Warnings ---------------------------------------------------------------------

def test_w1_node_named_after_a_related_card():
    data = ir("algorithms/binary-search")
    card = Card.from_dict({**SEARCH.to_dict(), "related_titles": ["取区间中点 mid"]})
    report = validate(data, card)
    assert report.ok and [i.code for i in report.warnings] == ["W1"]


def test_w2_edge_label_is_a_relation_type():
    data = ir("operating-systems/segmented-paging")
    data["edges"][4]["label"] = "depends_on"
    data["edges"][4]["p"] = ["P4"]
    report = validate(data, PAGING)
    assert "W2" in [i.code for i in report.warnings]


def test_schema_lists_every_form_and_role():
    assert IR_JSON_SCHEMA["properties"]["form"]["enum"] == ["flow", "sequence", "state", "structure"]
    assert "decision" in IR_JSON_SCHEMA["properties"]["nodes"]["items"]["properties"]["role"]["enum"]
    ir_obj, issues = parse_ir(ir("networking/etag-revalidation"))
    assert ir_obj is not None and ir_obj.form == "sequence" and not issues

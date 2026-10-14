"""V1-V8 and W0-W2: one passing and at least one failing case per check."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from secondbrain.diagrams.ir import IR_JSON_SCHEMA, Card, parse_ir
from secondbrain.diagrams.text import grounding_tokens, source_tokens, tokens
from secondbrain.diagrams.rejudge import strong_enough as marker_strong_enough
from secondbrain.diagrams.staleness import marker
from secondbrain.diagrams.validate import Limits, says_repeat, strong_enough, validate

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "diagrams"
CARDS = {c["ref"]: Card.from_dict(c) for c in json.loads((FIX / "cards.json").read_text(encoding="utf-8"))}
IRS = {x["ref"]: x for x in json.loads((FIX / "irs.json").read_text(encoding="utf-8"))}

SEARCH = CARDS["algorithms/binary-search"]
PAGING = CARDS["operating-systems/segmented-paging"]
ETAG = CARDS["networking/etag-revalidation"]


def ir(ref: str) -> dict:
    return copy.deepcopy(IRS[ref]["ir"])


# The checks added after the second pilot (V12 cite E, V5 back edge, V14 width)
# have their own tests below; the older tests switch them off to stay focused.
LEGACY = Limits(require_essence=False, require_back_edge=False, max_width=None)


def codes(data, card, limits=LEGACY) -> list[str]:
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
    (lambda d: d["edges"].append({"from": "mid", "to": "mid"}), "edges[8]"),  # no self-loop in a flow (J8)
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
    nodes = [{"key": k, "label": label, "p": ["P1"]} for k, label in
             [("a", "段号"), ("b", "段内页号"), ("c", "页内偏移"), ("d", "逻辑地址")]]
    data = {"decision": "draw", "form": "structure", "question": "q", "nodes": nodes,
            "edges": [{"from": "a", "to": "b", "label": "组成逻辑地址", "p": ["P1"]},
                      {"from": "c", "to": "d", "label": "组成逻辑地址", "p": ["P4"]}]}
    assert "V5" in codes(data, PAGING)  # two separate links: a list says the same
    data["edges"].append({"from": "b", "to": "c", "label": "组成逻辑地址", "p": ["P1"]})
    assert "V5" in codes(data, PAGING)  # a plain chain citing 2 sources: one sentence says it
    data["nodes"][3]["p"] = ["P1", "P2"]
    assert "V5" not in codes(data, PAGING)  # three links over three sources: a chain of parts


def test_merge_is_gain_from_three_nodes_in_flow_and_structure():
    card = Card(title="t", essence="需求拉动与成本推动都会推高物价。",
                propositions=("总需求增长快于供给时，需求拉动物价上涨。", "成本上升时，成本推动物价上涨。"))
    nodes = [{"key": "d", "label": "总需求增长快于供给", "p": ["P1"]},
             {"key": "c", "label": "成本上升", "p": ["P2"]},
             {"key": "p", "label": "物价上涨", "p": ["E", "P1"]}]
    edges = [{"from": "d", "to": "p", "label": "需求拉动", "p": ["P1"]},
             {"from": "c", "to": "p", "label": "成本推动", "p": ["P2"]}]
    for form in ("structure", "flow"):
        data = {"decision": "draw", "form": form, "question": "q", "nodes": nodes, "edges": edges}
        assert codes(data, card) == [], form
    chain = {"decision": "draw", "form": "structure", "question": "q", "nodes": nodes,
             "edges": [edges[0], {"from": "p", "to": "c", "label": "成本推动", "p": ["P2"]}]}
    assert {"V2", "V5"} <= set(codes(chain, card))


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


# Tokenisation and robustness ----------------------------------------------------

def _first_node_v4(label: str, prop: str) -> list[str]:
    card = Card(title="t", essence="e", propositions=(prop, "第二条命题"))
    data = {"decision": "draw", "form": "structure", "question": "q",
            "nodes": [{"key": "a", "label": label, "p": ["P1"]}] +
                     [{"key": k, "label": "第二条命题", "p": ["P2"]} for k in "bcd"],
            "edges": [{"from": "a", "to": x, "label": "第二条命题", "p": ["P2"]} for x in "bcd"]}
    return [str(i) for i in validate(data, card).errors if i.code == "V4"]


@pytest.mark.parametrize("label, prop", [
    ("Request", "完整交互过程（Discover-Offer-Request-ACK）由客户端发起"),
    ("ACK", "完整交互过程（Discover-Offer-Request-ACK）由客户端发起"),
    ("Thought", "通过 Thought-Action-Observation 循环完成任务"),
    ("Observation", "通过 Thought-Action-Observation 循环完成任务"),
    ("Plan-and-Execute", "Plan-and-Execute 先规划再执行"),
    ("LLM-as-Judge", "用 LLM-as-Judge 给答案打分"),
    ("JSON-RPC 消息", "MCP 用 JSON-RPC 传递消息"),
    ("S_i", "第 i 个子集记为 S_i"),
])
def test_v4_words_copied_from_a_compound_are_grounded(label, prop):
    assert _first_node_v4(label, prop) == []


def test_v4_pins_cjk_bigrams():
    assert tokens("段页式") == grounding_tokens("段页式") == {"段页", "页式"}
    assert grounding_tokens("页") == {"页"}
    # with single characters as tokens, "页段" would look grounded in "段页式"
    assert _first_node_v4("页段", "段页式变换") != []


def test_v4_label_without_any_token_is_reported():
    errors = _first_node_v4("→", "段页式变换")
    assert len(errors) == 1 and "shares 0% wording" in errors[0]  # symbols only: every symbol must occur
    assert _first_node_v4("   ", "段页式变换") == []  # blank labels are V1's business


def test_v5_single_exit_decision_is_not_a_branch():
    data = _chain(5)
    data["nodes"][1]["role"] = "decision"
    data["edges"][1].update(label="是", p=["P2"])
    assert "V5" in codes(data, SEARCH)


def test_v5_repeated_edge_is_not_a_merge():
    data = _chain(4)
    data["edges"].append(dict(data["edges"][0]))
    assert "V5" in codes(data, SEARCH)


@pytest.mark.parametrize("mutate", [
    lambda d: d["edges"][0].update({"from": ["mid"]}),
    lambda d: d["edges"][0].update(to={"key": "cmp"}),
    lambda d: d.update(groups=[{"label": "g", "members": [{"key": "mid"}]}]),
    lambda d: d["nodes"][0].update(role=["step"]),
    lambda d: d["nodes"][0].update(key=["mid"]),
])
def test_v1_rejects_non_scalar_fields_without_crashing(mutate):
    data = ir("algorithms/binary-search")
    mutate(data)
    report = validate(data, SEARCH)
    assert report.ir is None and report.errors and {i.code for i in report.errors} == {"V1"}


def test_v1_rejects_non_scalar_message_fields():
    data = ir("networking/etag-revalidation")
    data["messages"][0]["from"] = ["b"]
    data["messages"][1]["style"] = ["reply"]
    report = validate(data, ETAG)
    assert [i.where for i in report.errors] == ["messages[0].from", "messages[1].style"]


# Revision after the pilot ---------------------------------------------------------

def test_v8_counts_the_essence_as_a_source():
    data = _chain(4)
    for n in data["nodes"]:
        n["label"], n["p"] = "取区间中点 mid", ["P1"]
    data["edges"].append({"from": "k3", "to": "k0"})
    assert codes(data, SEARCH) == ["V8"]
    data["nodes"][0].update(label="有序区间减半", p=["E"])
    assert codes(data, SEARCH) == []


def test_v7_decision_needs_two_stated_outcomes():
    data = ir("algorithms/binary-search")
    data["edges"] = [e for e in data["edges"] if not (e["from"] == "empty" and e["to"] == "miss")]
    data["edges"].append({"from": "miss", "to": "mid"})
    report = validate(data, SEARCH)
    assert [(i.code, i.where) for i in report.errors] == [("V7", "nodes[5]")]


@pytest.mark.parametrize("label", ["$f(x)$", r"\sigma(x)", "f_{Y|X}(y|x)", "x^{2}", r"\frac a b"])
def test_v10_labels_are_plain_text(label):
    data = ir("algorithms/binary-search")
    data["nodes"][0]["label"] = label
    assert "V10" in codes(data, SEARCH)


@pytest.mark.parametrize("label, prop", [
    ("f(y|x)", "条件密度为 $f_{Y|X}(y|x)$"),
    ("σ(Wx + b)", r"输出为 $\sigma(Wx+b)$"),
    ("W·x + b", "线性层计算 $y = Wx + b$"),
    ("Σ x_i", r"把各项相加得 $\sum_i x_i$"),
    ("除以 √d", r"点积除以 $\sqrt{d_k}$ 后做 softmax"),
    ("level 3", "The page walk has four levels."),
    ("第 2 路信号", "两路信号在混音器中相加"),
    ("adder outputs", "an adder that outputs F(x) + x"),
])
def test_v4_formula_terms_counts_and_inflections_ground(label, prop):
    assert _first_node_v4(label, prop) == []


def test_v4_still_rejects_new_words_in_formula_cards():
    assert _first_node_v4("梯度消失", r"输出为 $\sigma(Wx+b)$") != []
    assert {"1", "4"} <= source_tokens("The page walk has four levels.")
    assert "5" not in source_tokens("The page walk has four levels.")


def _dataflow(**changes) -> tuple[dict, Card]:
    card = Card(title="t", essence="残差块把输入加回到子层输出上。",
                propositions=("输入 x 经过两层网络得到 F(x)。", "跳跃连接把 x 送到加法器，输出 F(x) + x。"))
    data = {"decision": "draw", "form": "flow", "arrows": "data", "question": "q",
            "nodes": [{"key": "x", "label": "输入 x", "role": "input", "p": ["P1"]},
                      {"key": "f", "label": "两层网络", "p": ["P1"]},
                      {"key": "add", "label": "加法器", "p": ["P2"]},
                      {"key": "y", "label": "输出 F(x) + x", "role": "output", "p": ["P2"]}],
            "edges": [{"from": "x", "to": "f"}, {"from": "f", "to": "add", "label": "F(x)", "p": ["P1"]},
                      {"from": "x", "to": "add", "label": "跳跃连接", "p": ["P2"]}, {"from": "add", "to": "y"}]}
    data.update(changes)
    return data, card


def test_v11_data_diagrams_show_inputs_and_outputs():
    data, card = _dataflow()
    assert codes(data, card) == []
    data["nodes"][3]["role"] = "step"
    assert codes(data, card) == ["V11"]
    data, card = _dataflow()
    data["edges"].append({"from": "y", "to": "x"})
    assert codes(data, card) == ["V11", "V11"]  # input has an incoming edge, output an outgoing one
    data, card = _dataflow(arrows="control")
    data["nodes"][0]["role"] = "step"
    assert codes(data, card) == []  # a control flow may end in an output without an input
    data["edges"].append({"from": "y", "to": "f"})
    assert codes(data, card) == ["V11"]  # but an output still has incoming edges only
    data, card = _dataflow()
    data["nodes"][0]["role"] = "step"
    assert codes(data, card) == ["V11"]  # a data diagram needs its input


def test_arrows_field_and_retired_data_role():
    data, card = _dataflow(arrows="flows")
    assert codes(data, card) == ["V1"]
    seq = ir("networking/etag-revalidation")
    seq["arrows"] = "data"
    assert codes(seq, ETAG) == ["V1"]
    data, card = _dataflow()
    data["nodes"][1]["role"] = "data"
    report = validate(data, card, LEGACY)
    assert report.ok and report.ir.nodes[1].role == "step"
    assert [w.code for w in report.warnings] == ["W0"]


def test_v4_case_folding_never_turns_the_sum_sign_into_sigma():
    src = r"交叉熵 $H = -\sum_i y_i \log p_i$，其中 $p_i$ 是预测概率"
    assert _first_node_v4("σ", src) != []          # no sigma on the card
    assert _first_node_v4("σ(z)", src) != []
    assert _first_node_v4("Σ y_i log p_i", src) == []
    assert _first_node_v4("Π p_i", r"联合概率 $\prod_i p_i$") == []
    assert _first_node_v4("π", r"联合概率 $\prod_i p_i$") != []
    assert "g" not in source_tokens(src)            # letters of \log are not formula letters
    assert {"σ", "w", "x", "b"} <= source_tokens(r"输出为 $\sigma(Wx+b)$")


def test_v11_inputs_feed_and_outputs_receive():
    data, card = _dataflow()
    data["edges"] = [e for e in data["edges"] if e["from"] != "x" and e["to"] != "y"]
    data["edges"].append({"from": "f", "to": "add", "label": "F(x)", "p": ["P1"]})
    report = validate(data, card)
    assert [(i.code, i.where) for i in report.errors if i.code == "V11"] == [("V11", "nodes[0]"), ("V11", "nodes[3]")]


# Second pilot ----------------------------------------------------------------------

FULL = Limits()


def _gain_flow(card: Card, labels: list[str], refs: list[list[str]]) -> dict:
    """a -> b, a -> c, b -> d, c -> d (a merge), labels and citations given."""
    keys = "abcd"
    return {"decision": "draw", "form": "flow", "question": "q",
            "nodes": [{"key": k, "label": lab, "p": r} for k, lab, r in zip(keys, labels, refs)],
            "edges": [{"from": "a", "to": "b"}, {"from": "a", "to": "c"}, {"from": "b", "to": "d"},
                      {"from": "c", "to": "d"}]}


def test_v12_the_diagram_cites_e():
    data = ir("algorithms/binary-search")
    assert codes(data, SEARCH, FULL) == []
    data["nodes"][5]["p"] = ["P3"]
    assert codes(data, SEARCH, FULL) == ["V12"]
    no_essence = Card.from_dict({**SEARCH.to_dict(), "essence": ""})
    assert "V12" not in codes(data, no_essence, Limits(require_back_edge=False))


def test_v5_card_that_repeats_needs_a_back_edge():
    card = Card(title="t", essence="重复取样直到误差足够小。",
                propositions=("每次取一批样本计算误差。", "误差足够小就停止，否则继续取样。"))
    data = _gain_flow(card, ["取一批样本", "计算误差", "误差足够小", "停止"], [["P1", "E"], ["P1"], ["P2"], ["P2"]])
    assert "V5" in codes(data, card, FULL)
    data["edges"].append({"from": "d", "to": "a"})
    assert "V5" not in codes(data, card, FULL)
    rnn = Card(title="t", essence="循环神经网络把上一步的隐状态传给下一步。",
               propositions=("隐状态来自上一步。", "输出来自隐状态。"))
    flat = _gain_flow(rnn, ["上一步的隐状态", "隐状态", "输出", "下一步"], [["P1", "E"], ["P1"], ["P2"], ["E"]])
    assert "V5" not in codes(flat, rnn, FULL)  # the name of the model is not a loop


def _fan(n: int, decision: bool = False) -> tuple[dict, Card]:
    card = Card(title="t", essence="根据类型选择处理方式。",
                propositions=tuple(f"类型{i}用方式{i}处理。" for i in range(1, n + 1)))
    nodes = [{"key": "r", "label": "根据类型选择", "role": "decision" if decision else "step", "p": ["E"]}]
    nodes += [{"key": f"c{i}", "label": f"方式{i}处理", "p": [f"P{i}"]} for i in range(1, n + 1)]
    edges = [{"from": "r", "to": f"c{i}", "label": f"类型{i}", "p": [f"P{i}"]} for i in range(1, n + 1)]
    return {"decision": "draw", "form": "structure", "question": "q", "nodes": nodes, "edges": edges}, card


def test_v13_fan_out_to_leaves_is_a_list():
    data, card = _fan(4)
    assert "V13" in codes(data, card)
    data, card = _fan(3)
    assert "V13" not in codes(data, card)


def test_v13_parallel_options_are_not_a_decision():
    data, card = _fan(3, decision=True)
    data["form"] = "flow"
    assert "V13" in codes(data, card)
    data["nodes"] = data["nodes"][:3]
    data["edges"] = data["edges"][:2]
    assert "V13" not in codes(data, card)  # two outcomes of a real decision are fine


def test_v14_estimated_width():
    card = Card(title="t", essence="三条路径汇合到结果。",
                propositions=("第一条路径很长很长很长很长很长很长。", "第二条路径很长很长很长很长很长很长。",
                              "第三条路径很长很长很长很长很长很长。"))
    nodes = [{"key": "s", "label": "三条路径", "p": ["E"]}, {"key": "t", "label": "结果", "p": ["E"]}]
    nodes += [{"key": f"m{i}", "label": card.propositions[i][:14], "p": [f"P{i + 1}"]} for i in range(3)]
    edges = [{"from": "s", "to": f"m{i}"} for i in range(3)] + [{"from": f"m{i}", "to": "t"} for i in range(3)]
    data = {"decision": "draw", "form": "structure", "question": "q", "nodes": nodes,
            "edges": [dict(e, label="汇合到结果", p=["E"]) for e in edges]}
    assert "V14" in codes(data, card, FULL)
    assert "V14" not in codes(data, card, Limits(max_width=None))
    for n in data["nodes"][2:]:
        n["label"] = n["label"][:4]
    assert "V14" not in codes(data, card, FULL)


def test_v11_data_diagram_may_end_in_a_store_and_inputs_go_where_used():
    data, card = _dataflow()
    data["nodes"][3]["role"] = "store"
    assert codes(data, card) == []
    data, card = _dataflow()
    data["nodes"].insert(1, {"key": "w", "label": "权重 W", "role": "input", "p": ["P1"]})
    data["edges"].append({"from": "w", "to": "add", "label": "F(x)", "p": ["P1"]})
    report = validate(data, card, LEGACY)
    assert [i.code for i in report.errors] == ["V4", "V11"]  # 权重 W is on no card line, and the adder never uses it


@pytest.mark.parametrize("label, prop", [
    ("或", "先查缓存或查数据库"),
    ("积", "输出是两数之积"),
    ("#", "以 # 开头的行是注释"),
    ("⊕", r"输出 $x \oplus y$"),
])
def test_v4_single_characters_and_symbols_count(label, prop):
    assert _first_node_v4(label, prop) == []


# Fourth round: V3's K1, K3, K7 and the producers' questions ---------------------------------

def _loop_card(p2: str) -> Card:
    return Card(title="t", essence="取样器按误差决定是否继续。",
                propositions=("每次取一批样本计算误差。", p2, "误差足够小就停止，否则继续取样。"))


def _loop_flow(cite_p2: bool) -> dict:
    """a -> b -> d? -> (e | f), no back edge; P2 is cited only when asked."""
    return {"decision": "draw", "form": "flow", "question": "q",
            "nodes": [{"key": "a", "label": "取一批样本", "p": ["P1", "E"]},
                      {"key": "b", "label": "计算误差", "p": ["P1"] + (["P2"] if cite_p2 else [])},
                      {"key": "d", "label": "误差足够小？", "role": "decision", "p": ["P3"]},
                      {"key": "e", "label": "停止", "role": "end", "p": ["P3"]},
                      {"key": "f", "label": "继续取样", "p": ["P3"]}],
            "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "d"},
                      {"from": "d", "to": "e", "label": "是", "p": ["P3"]},
                      {"from": "d", "to": "f", "label": "否", "p": ["P3"]}]}


def test_v5_back_edge_looks_only_at_the_text_the_diagram_cites():
    card = _loop_card("误差计算要重复到足够小为止。")
    assert "V5" not in codes(_loop_flow(cite_p2=False), card, FULL)
    errors = [str(e) for e in validate(_loop_flow(cite_p2=True), card, FULL).errors]
    assert len(errors) == 1 and errors[0].startswith("V5 P2, which the diagram cites, may say the work repeats")
    assert "«误差计算要重复到足够小为止。»" in errors[0]  # the message quotes the cited text it asks about
    # it never pushes a loop onto text that does not state one: the other way out is to say so in no_repeat
    assert ('If it does not, add {"p":"P2","quote":"<the words of P2 around «重复», copied>",'
            '"why":"<what they mean>"} to no_repeat; never add a loop the card does not state') in errors[0]
    looped = _loop_flow(cite_p2=True)
    looped["edges"].append({"from": "f", "to": "a"})
    assert codes(looped, card, FULL) == []


def test_v5_back_edge_is_a_flow_rule():
    card = _loop_card("误差计算要重复到足够小为止。")
    data = _loop_flow(cite_p2=True)
    data["form"] = "structure"
    for n in data["nodes"]:
        n["role"] = "step"
    data["edges"] = [dict(e, label=e.get("label") or "然后", p=e.get("p") or ["P1"]) for e in data["edges"]]
    assert not any("repeats" in str(e) for e in validate(data, card, FULL).errors)


@pytest.mark.parametrize("text", [
    "同一目标已有发现进行时不会重复发起查询。", "避免两路检索返回同一文档造成重复。", "丢弃重复的工具输出与噪声。",
    "去除重复项后再排序。", "推理层应做循环检测。", "循环队列用取模回绕下标。", "高危操作要 human-in-the-loop 确认。",
    "ARQ is Automatic Repeat reQuest.", "The client never repeats the query.", "先判断是否重复再写入。",
    "Γ 函数满足递推式 Γ(r+1)=rΓ(r)。", "服务器主动推送，客户端不必轮询。",
    # prevention, failure and names (V3's X1 list), then duplicates and trends seen in vault samples
    "停止移动时清闩，避免每帧重复 play。", "按钮置灰，以免重复提交。", "条件写错会导致死循环。", "生成时施加重复惩罚。",
    "设最大步数，防止无限循环。", "等待响应直到超时。", "检测到 Agent 循环后改换工具，而不是继续同一循环。",
    "直接透传，避免外层 TLS 与内层 HTTPS 的重复处理。", "TCP 保证数据无丢失、失序和重复。", "按 prompt 统计重复率。",
    "误差在反向传播中不断衰减。", "开源项目持续迭代。", "评估后续迭代难度。", "表中的重复记录要合并。",
    "The agent can get stuck in an infinite loop.", "Cache results to avoid repeating the query.",
    "死循环会一直占用 CPU。", "常见失败之一是无限循环。",
    # V3's Y2: counts, a failure that goes on in the next clause, nouns, a negation a few characters off, lists
    "ReAct 可能陷入循环，反复执行同样的行动。", "出现资源重复。",
    "收到 3 次重复ACK 就快速重传。", "摘要不必随每次问答重复执行。", "改模板而不必改循环结构本身。", "留出后续迭代空间。",
    "工作流能表达分支、循环，以及暂停后恢复。",
])
def test_v5_negations_duplicates_and_names_do_not_ask_for_a_loop(text):
    assert says_repeat(_loop_card(text)) is None
    assert "V5" not in codes(_loop_flow(cite_p2=True), _loop_card(text), FULL)


@pytest.mark.parametrize("text, word", [
    ("误差计算要重复到足够小为止。", "重复"), ("逐位相乘并处理进位。", "逐位"), ("每一轮选一个最近的点。", "每一轮"),
    ("训练迭代直到收敛。", "迭代"), ("Repeat the update until it converges.", "Repeat"), ("循环读取输入，读完为止。", "循环"),
    # added after set D, from vault contexts
    ("距离信息在节点间不断传递。", "不断"), ("反复迭代直至集合不再增大。", "反复"), ("查询直至就绪。", "直至"),
    ("按时间步递推隐状态。", "递推"), ("客户端轮询任务结果。", "轮询"), ("电压在两个阈值间往复变化。", "往复"),
    ("以行块和列块为循环层级，逐块累加输出。", "循环层级"), ("避免越界；然后重复上一步。", "重复"),
    ("防止死循环；每轮最多重试 3 次。", "每轮"), ("陷入循环后重启，再反复读取输入。", "反复"),
    ("反馈含「无需改进」或达到最大迭代次数时终止。", "迭代"),
])
def test_v5_loop_words(text, word):
    ref, quote = says_repeat(_loop_card(text))
    assert ref == "P2" and word in quote


def test_v5_when_e_says_it_the_model_can_say_it_is_no_repeat():
    """V3's D2: E has to stay cited, so before no_repeat the only way out of a misfire on E was to skip."""
    card = Card(title="t", essence="取样器反复按误差决定是否继续。", propositions=_loop_card("x").propositions)
    data = _loop_flow(cite_p2=False)
    [error] = [str(e) for e in validate(data, card, FULL).errors if e.code == "V5"]
    assert error.startswith("V5 E, which the diagram cites") and '{"p":"E","quote":"<the words of E around «反复»' in error
    data["no_repeat"] = [{"p": "E", "quote": "取样器反复按误差决定", "why": "it says how often it checks, not a step"}]
    report = validate(data, card, FULL)
    assert report.ok and not report.warnings and report.ir.no_repeat[0].p == "E"


def _no_repeat_card(p2: str, item: dict):
    data = _loop_flow(cite_p2=True)
    data["no_repeat"] = [item]
    return validate(data, _loop_card(p2), FULL)


def _no_repeat(**item):
    return _no_repeat_card("误差计算要重复到足够小为止。", item)


def test_no_repeat_excuses_only_the_words_it_copies():
    """V3's D2: the model confirms or rejects a loop-word trigger; it never makes up a loop to pass."""
    assert _no_repeat(p="P2", quote="要重复到足够小", why="一次算到足够小，不是循环").ok
    assert _no_repeat(p="P2", quote="误差计算要重复到足够小为止。", why="x").ok   # the whole short sentence
    for item, problem in (
            ({"p": "P1", "quote": "要重复到足够小", "why": "x"}, "quote is not copied from P1"),
            ({"p": "P2", "quote": "要重复到很小", "why": "x"}, "quote is not copied from P2"),
            ({"p": "P2", "quote": "误差计算", "why": "x"}, "quote has no loop word"),
            ({"p": "P9", "quote": "要重复到足够小", "why": "x"}, "P9 is not a source of this card"),
            ({"p": "P2", "quote": "重复" * 21, "why": "x"}, "quote is 42 characters")):
        report = _no_repeat(**item)
        assert [e.code for e in report.errors] == ["V5"], item
        assert [w.code for w in report.warnings] == ["W3"] and problem in str(report.warnings[0]), item
        if item["p"] == "P2":  # the rewrite message says why the item did not count
            assert f"(your no_repeat item for P2: {problem}" in str(report.errors[0])


def test_an_ignored_no_repeat_item_excuses_nothing():
    """V3's G1: a quote copied whole from a long sentence is too long, so its loop word still counts."""
    text = "误差计算要重复到足够小为止，这一步对所有样本都用同一个公式和同一个阈值来判断，不另设规则。"
    assert _no_repeat_card(text, {"p": "P2", "quote": "要重复到足够小", "why": "x"}).ok  # the one loop word
    report = _no_repeat_card(text, {"p": "P2", "quote": text, "why": "x"})
    assert [w.code for w in report.warnings] == ["W3"] and "quote is 45 characters" in str(report.warnings[0])
    assert [e.code for e in report.errors] == ["V5"]


def test_no_repeat_excuses_only_the_source_it_names():
    """V3's G2: the same words in another cited source still ask for the back edge."""
    words = "误差计算要重复到足够小为止。"
    card = Card(title="t", essence="取样器按误差决定是否继续，" + words, propositions=_loop_card(words).propositions)
    data = _loop_flow(cite_p2=True)
    for named, other in (("P2", "E"), ("E", "P2")):
        data["no_repeat"] = [{"p": named, "quote": "要重复到足够小", "why": "x"}]
        report = validate(data, card, FULL)
        [error] = [str(e) for e in report.errors]
        assert not report.warnings and error.startswith(f"V5 {other}, which the diagram cites"), named
    data["no_repeat"] = [{"p": ref, "quote": "要重复到足够小", "why": "x"} for ref in ("E", "P2")]
    assert validate(data, card, FULL).ok


def test_no_repeat_keeps_other_loop_words_in_the_same_text():
    card = _loop_card("误差计算要重复到足够小为止，否则不断取样。")
    data = _loop_flow(cite_p2=True)
    data["no_repeat"] = [{"p": "P2", "quote": "要重复到足够小", "why": "x"}]
    [error] = [str(e) for e in validate(data, card, FULL).errors]
    assert error.startswith("V5 P2") and "«不断»" in error


@pytest.mark.parametrize("bad, where", [
    ("x", "no_repeat"), (["x"], "no_repeat[0]"), ([{"p": "Q", "quote": "q", "why": "w"}], "no_repeat[0].p"),
    ([{"p": "P2", "why": "w"}], "no_repeat[0].quote"), ([{"p": "P2", "quote": "q"}], "no_repeat[0].why")])
def test_v1_no_repeat_shape(bad, where):
    data = _loop_flow(cite_p2=True)
    data["no_repeat"] = bad
    report = validate(data, _loop_card("误差计算要重复到足够小为止。"), FULL)
    assert report.ir is None and [e.where for e in report.errors] == [where]


def test_a_self_loop_is_a_state_or_structure_thing():
    """J8 #63: a flow's self-loop draws a long line far from its label; a state may stay in itself once."""
    data = _loop_flow(cite_p2=False)
    data["edges"].append({"from": "b", "to": "b"})
    [error] = validate(data, _loop_card("x"), FULL).errors
    assert error.code == "V1" and "draw a repeat as an edge back from a later step" in error.message
    ir, issues = parse_ir({"decision": "draw", "form": "state", "question": "q",
                           "nodes": [{"key": k, "label": k, "p": ["P1"]} for k in "abc"],
                           "edges": [{"from": "a", "to": "a", "label": "x", "p": ["P1"]},
                                     {"from": "a", "to": "b", "label": "y", "p": ["P1"]}]})
    assert ir is not None and not issues


def test_v12_replacing_a_renderable_old_diagram_needs_e_and_two_propositions():
    card = Card.from_dict({**_loop_card("误差计算要重复到足够小为止。").to_dict(), "old_diagram": "renders"})
    data = _loop_flow(cite_p2=False)
    data["edges"].append({"from": "f", "to": "a"})
    assert codes(data, card, FULL) == []  # E, P1, P3
    data["nodes"][2]["p"] = data["nodes"][3]["p"] = data["nodes"][4]["p"] = ["P1"]
    data["edges"][2]["p"] = data["edges"][3]["p"] = ["P1"]
    wording_off = Limits(grounding_min=0.0)  # the labels now cite P1 only; this test is about V12
    errors = validate(data, card, wording_off).errors
    assert [e.code for e in errors] == ["V12"] and "renders" in errors[0].message  # E and P1 only
    broken = Card.from_dict({**card.to_dict(), "old_diagram": "broken"})
    assert codes(data, broken, wording_off) == []  # a broken old diagram sets no bar
    wide = Card.from_dict({**card.to_dict(), "old_diagram": "too_wide"})
    assert codes(data, wide, wording_off) == ["V12"]  # a too-wide one renders: the same bar
    one = Card.from_dict({**card.to_dict(), "propositions": ["每次取一批样本计算误差。"]})
    assert "V12" not in codes(data, one, wording_off)  # min(2, 1): E and the only proposition


@pytest.mark.parametrize("refs, n_props, has_e", [
    (("E", "P1", "P2"), 3, True), (("E", "P1"), 3, True), (("P1", "P2"), 3, True), (("P1", "P2"), 3, False),
    (("E", "P1"), 1, True), (("E",), 1, True), ((), 0, False), (("E", "P3", "P1"), 2, True),
])
def test_v12_and_the_writeback_guard_use_one_rule(refs, n_props, has_e):
    card = Card(title="t", essence="e" if has_e else "", propositions=tuple("p" for _ in range(n_props)))
    nodes = [{"key": f"k{i}", "label": "x", "p": [r]} for i, r in enumerate(refs)] or [{"key": "k", "label": "x"}]
    parsed, _ = parse_ir({"decision": "draw", "form": "flow", "question": "q", "nodes": nodes})
    assert marker_strong_enough(marker(card, parsed), n_props, has_e) == strong_enough(refs, n_props, has_e)


def test_v11_generic_words_are_removed_before_tokenising():
    data, card = _dataflow()
    data["nodes"][0]["label"] = "输入数据"
    data["edges"][2]["p"] = ["P1"]  # the adder's edge now cites a line without x
    v11 = lambda: [c for c in codes(data, card) if c == "V11"]
    assert v11() == []  # nothing but generic words: nothing to check (was the pair 入数)
    data["nodes"][0]["label"] = "input data"
    assert v11() == []
    data["nodes"][0]["label"] = "输入 x"
    assert v11() == []  # x is on both lines the targets cite
    data["nodes"][0]["label"] = "输入数据 q"
    assert v11() == ["V11", "V11"]  # q is on neither


def test_v5_unmerged_split_in_a_flow_is_not_gain():
    card = Card(title="t", essence="请求分到两路处理。", propositions=("请求先校验。", "校验后分两路处理。"))
    data = {"decision": "draw", "form": "flow", "question": "q",
            "nodes": [{"key": "a", "label": "请求先校验", "p": ["P1", "E"]},
                      {"key": "b", "label": "分两路处理", "p": ["P2"]},
                      {"key": "c", "label": "两路处理", "p": ["P2"]},
                      {"key": "d", "label": "请求分到两路", "p": ["E"]}],
            "edges": [{"from": "a", "to": "b"}, {"from": "a", "to": "c"}, {"from": "b", "to": "d"}]}
    errors = [str(e) for e in validate(data, card, FULL).errors]
    assert len(errors) == 1 and "paths that split and never meet again" in errors[0]


def test_v2_a_sequence_has_two_or_three_participants():
    nodes = [{"key": k, "label": k, "p": ["P1"]} for k in "abcd"]
    msgs = [{"from": "a", "to": "b", "text": "首次请求", "p": ["P1"]}] * 3
    data = {"decision": "draw", "form": "sequence", "question": "q", "nodes": nodes, "messages": msgs}
    assert "V2" in codes(data, ETAG)
    data["nodes"] = nodes[:3]
    assert "V2" not in codes(data, ETAG)

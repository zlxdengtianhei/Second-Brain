"""Generator: ids, shapes, escaping, fixed direction, no colour, marker."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from secondbrain.diagrams.emit import emit, escape, fenced
from secondbrain.diagrams.ir import Card, DiagramIR, Group, Link, Node
from secondbrain.diagrams.validate import validate

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "diagrams"
CARDS = {c["ref"]: Card.from_dict(c) for c in json.loads((FIX / "cards.json").read_text(encoding="utf-8"))}
IRS = {x["ref"]: x["ir"] for x in json.loads((FIX / "irs.json").read_text(encoding="utf-8"))}


def parsed(ref: str) -> DiagramIR:
    report = validate(IRS[ref], CARDS[ref])
    assert report.ok
    return report.ir


def test_flow_golden_output():
    assert emit(parsed("algorithms/binary-search")) == "\n".join([
        "flowchart TB",
        '  n1["取区间中点 mid"]',
        '  n2{"比较 a[mid] 与目标值"}',
        '  n3["lo = mid + 1"]',
        '  n4["hi = mid - 1"]',
        '  n5(["返回 mid"])',
        '  n6{"lo #gt; hi？"}',
        '  n7(["返回 -1 表示未找到"])',
        "  n1 --> n2",
        '  n2 -->|"小于目标值"| n3',
        '  n2 -->|"大于目标值"| n4',
        '  n2 -->|"相等"| n5',
        "  n3 --> n6",
        "  n4 --> n6",
        '  n6 -->|"是"| n7',
        '  n6 -->|"否"| n1',
    ])


def test_groups_become_subgraphs_and_data_nodes_cylinders():
    mm = emit(parsed("operating-systems/segmented-paging"))
    assert '  subgraph g1 ["逻辑地址的三部分"]\n    n1["段号"]' in mm
    assert '  n4[("段表")]' in mm and '  n7(["物理地址：第 3 次访问内存"])' in mm


def test_role_shapes():
    ir = DiagramIR("draw", form="flow", question="q",
                   nodes=(Node("x", "输入 x", "input"), Node("t", "页表", "store"), Node("s", "相加", "step"),
                          Node("y", "输出 y", "output"), Node("old", "旧角色", "data")),
                   edges=(Link("x", "s"), Link("t", "s"), Link("s", "y"), Link("old", "s")))
    assert emit(ir).splitlines()[1:6] == ['  n1[/"输入 x"/]', '  n2[("页表")]', '  n3["相加"]',
                                          '  n4[\\"输出 y"\\]', '  n5["旧角色"]']


def test_sequence_golden_output():
    assert emit(parsed("networking/etag-revalidation")) == "\n".join([
        "sequenceDiagram",
        "  participant n1 as 浏览器",
        "  participant n2 as 浏览器缓存",
        "  participant n3 as 服务器",
        "  n1->>n3: 首次请求",
        "  n3-->>n1: 返回资源的 ETag",
        "  n1->>n2: 把资源和 ETag 存入缓存",
        "  n1->>n3: 带 If-None-Match 的条件请求",
        "  n3-->>n1: 资源未变化：304 Not Modified",
        "  Note over n1: 继续使用缓存副本",
    ])


def test_sequence_async_and_two_party_note():
    ir = DiagramIR("draw", form="sequence", question="q",
                   nodes=(Node("a", "A", "actor"), Node("b", "B", "actor")),
                   messages=(Link("a", "b", "fire", style="async"), Link("a", "b", "both", note=True)))
    assert emit(ir).splitlines()[3:] == ["  n1-)n2: fire", "  Note over n1,n2: both"]


def test_state_start_end_and_escaping():
    ir = DiagramIR("draw", form="state", question="q",
                   nodes=(Node("i", "空闲", "start"), Node("r", "运行_中", "state"), Node("d", "完成", "end")),
                   edges=(Link("i", "r", "收到任务: 开始"), Link("r", "d", "**结束**"), Link("r", "r", "心跳")))
    assert emit(ir).splitlines() == [
        "stateDiagram-v2",
        '  state "空闲" as n1',
        '  state "运行#95;中" as n2',
        '  state "完成" as n3',
        "  [*] --> n1",
        "  n1 --> n2 : 收到任务#58; 开始",
        "  n2 --> n3 : #42;#42;结束#42;#42;",
        "  n2 --> n2 : 心跳",
        "  n3 --> [*]",
    ]


def test_keys_never_reach_the_output_so_keywords_are_safe():
    ir = DiagramIR("draw", form="flow", question="q",
                   nodes=(Node("end", "end", "step"), Node("call", "graph", "decision"),
                          Node("style", "style", "end")),
                   edges=(Link("end", "call"), Link("call", "style", "是"), Link("call", "end", "否")))
    mm = emit(ir)
    assert mm.splitlines()[1:4] == ['  n1["end"]', '  n2{"graph"}', '  n3(["style"])']
    assert "  n2 -->|\"否\"| n1" in mm


@pytest.mark.parametrize("raw, out", [
    ('say "hi"', "say #quot;hi#quot;"),
    ("a<br>b", "a#lt;br#gt;b"),
    ("x;y", "x#59;y"),
    ("C# and #1", "C#35; and #35;1"),
    ("two\nlines\t here", "two lines here"),
    ("$x^2$", "#36;x^2#36;"),
    ("`code`", "#96;code#96;"),
    ("a\\nb", "a#92;nb"),
    ("R&D", "R&D"),
    ("&lt;b&gt;", "#38;lt#59;b#38;gt#59;"),
    ("50% off", "50% off"),
    ("%%{init: {}}%%", "#37;#37;{init: {}}#37;#37;"),
    ("lifestyle", "lifestyle"),
    ('style:"a"', "#115;tyle:#quot;a#quot;"),
    ("f(x) [a] {b} a|b a: b", "f(x) [a] {b} a|b a: b"),
])
def test_escape(raw, out):
    assert escape(raw) == out


def test_flow_is_always_top_to_bottom_and_never_coloured():
    data = dict(IRS["algorithms/binary-search"], direction="LR")
    report = validate(data, CARDS["algorithms/binary-search"])
    mm = emit(report.ir)
    assert mm.startswith("flowchart TB\n")
    for word in ("style ", "classDef", "linkStyle", "fill:", "color:", ":::"):
        assert word not in mm


def test_marker_line_and_fence():
    ir = parsed("algorithms/binary-search")
    mm = emit(ir, marker="%% diagram v1 props=P1,P2,P3 sha=0123abcd")
    assert mm.splitlines()[:2] == ["%% diagram v1 props=P1,P2,P3 sha=0123abcd", "flowchart TB"]
    assert fenced(mm) == "```mermaid\n" + mm + "\n```"
    with pytest.raises(ValueError):
        emit(ir, marker="not a comment")


def test_skip_ir_cannot_be_emitted():
    with pytest.raises(ValueError):
        emit(DiagramIR("skip", reason="contrast card"))


def test_output_is_deterministic():
    ir = parsed("operating-systems/segmented-paging")
    assert emit(ir) == emit(parsed("operating-systems/segmented-paging"))
    assert Group("逻辑地址的三部分", ("seg", "page", "off"), ("P1",)) in ir.groups

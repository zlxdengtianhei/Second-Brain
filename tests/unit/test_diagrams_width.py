"""V14 width estimate: pinned against widths measured in Obsidian's Mermaid, the 700 px line, and the
prompt's rule of thumb (rule 7) checked against both the estimate and the rendered width.

The measured widths come from tools/mermaid_check (mermaid@11.13.0 with Obsidian's patch and config,
Chrome on macOS, -apple-system / PingFang fonts), 2026-10-13. They pin the layout constants: a change of
the node gap, the edge-label gap, the group frame or the sequence margins moves at least one case out of
its tolerance.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from secondbrain.diagrams.emit import emit
from secondbrain.diagrams.ir import Card, parse_ir
from secondbrain.diagrams.pipeline import load_prompt
from secondbrain.diagrams.validate import Limits, validate
from secondbrain.diagrams.width import estimate_width

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "mermaid_check" / "check.mjs"
CJK = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"


def c(n: int, off: int = 0) -> str:
    """n distinct CJK characters (n wide)."""
    return (CJK[off:] + CJK)[:n]


def flow(nodes, edges, form="flow", **extra) -> dict:
    return {"decision": "draw", "form": form, "question": "q", **extra,
            "nodes": [{"p": ["P1"], **n} for n in nodes],
            "edges": [dict(e, p=["P1"]) if e.get("label") else e for e in edges]}


def row(n: int, width: float, role: str = "step", back: bool = False, groups: bool = False) -> dict:
    """start -> n nodes side by side -> merge (-> start again when back)."""
    keys = [f"b{i}" for i in range(n)]
    nodes = [{"key": "a", "label": "起"}] + [{"key": k, "label": c(int(width), i), "role": role} for i, k in enumerate(keys)]
    nodes.append({"key": "m", "label": "合"})
    edges = [{"from": "a", "to": k} for k in keys] + [{"from": k, "to": "m"} for k in keys]
    if back:
        nodes.insert(0, {"key": "s", "label": "始"})
        edges = [{"from": "s", "to": "a"}] + edges + [{"from": "m", "to": "s"}]
    extra = {"groups": [{"label": "组", "members": keys}]} if groups else {}
    return flow(nodes, edges, **extra)


def exits(n: int, width: float) -> dict:
    """A decision with n labelled exits side by side, merging again."""
    keys = [f"b{i}" for i in range(n)]
    nodes = [{"key": "d", "label": "哪种？", "role": "decision"}] + [{"key": k, "label": "步"} for k in keys]
    nodes.append({"key": "m", "label": "合"})
    return flow(nodes, [{"from": "d", "to": k, "label": c(int(width), i)} for i, k in enumerate(keys)]
                + [{"from": k, "to": "m"} for k in keys])


def seq(names: list[str], msgs: list[dict]) -> dict:
    return {"decision": "draw", "form": "sequence", "question": "q",
            "nodes": [{"key": f"a{i}", "label": n, "p": ["P1"]} for i, n in enumerate(names)],
            "messages": [dict(m, p=["P1"]) for m in msgs]}


def msg(a: int, b: int, text: str, **kw) -> dict:
    return {"from": f"a{a}", "to": f"a{b}", "text": text, **kw}


# (IR, measured width, tolerance of the estimate)
MEASURED = {
    "row3_8": (row(3, 8), 680, 15),
    "row3_9": (row(3, 9), 728, 15),
    "row3_7_back": (row(3, 7, back=True), 667, 15),
    "row2_20": (row(2, 20), 586, 15),
    # 40 Latin letters without a space: Mermaid cannot break the word (the estimate's 8.6 px a letter runs wide)
    "row2_latin40": (flow([{"key": "a", "label": "a"}, {"key": "b", "label": "abcdefghijklmnopqrstuvwxyzabcdefghijklmn"},
                           {"key": "d", "label": "bcdefghijklmnopqrstuvwxyzabcdefghijklmno"}, {"key": "m", "label": "m"}],
                          [{"from": "a", "to": "b"}, {"from": "a", "to": "d"}, {"from": "b", "to": "m"},
                           {"from": "d", "to": "m"}]), 839, 40),
    "row2_8_group": (row(2, 8, groups=True), 512, 5),
    # a group framing a whole layer adds 70 px; its title widens it when wider than the members
    "row3_7_group": (row(3, 7, groups=True), 702, 5),
    "row1_2_group_title14": (flow([{"key": "a", "label": "起"}, {"key": "b", "label": "甲乙"}, {"key": "m", "label": "合"}],
                                  [{"from": "a", "to": "b"}, {"from": "b", "to": "m"}],
                                  groups=[{"label": c(14, 9), "members": ["b"]}]), 248, 5),
    # two groups side by side: 55 px each; one inside a layer: 40 px, estimated 55 (its place in the layer is unknown)
    "row4_4_two_groups": (flow([{"key": "a", "label": "起"}] + [{"key": f"b{i}", "label": c(4, i)} for i in range(4)]
                               + [{"key": "m", "label": "合"}],
                               [{"from": "a", "to": f"b{i}"} for i in range(4)] + [{"from": f"b{i}", "to": "m"} for i in range(4)],
                               groups=[{"label": "甲", "members": ["b0", "b1"]}, {"label": "乙", "members": ["b2", "b3"]}]),
                          772, 5),
    "row3_4_middle_group": (flow([{"key": "a", "label": "起"}] + [{"key": f"b{i}", "label": c(4, i)} for i in range(3)]
                                 + [{"key": "m", "label": "合"}],
                                 [{"from": "a", "to": f"b{i}"} for i in range(3)] + [{"from": f"b{i}", "to": "m"} for i in range(3)],
                                 groups=[{"label": "甲", "members": ["b1"]}]), 528, 15),
    # a group outside the widest layer adds nothing to it
    "row3_7_group_above": (flow([{"key": "s", "label": "始"}, {"key": "g", "label": c(4, 1)}]
                                + [{"key": f"b{i}", "label": c(7, i + 2)} for i in range(3)] + [{"key": "m", "label": "合"}],
                                [{"from": "s", "to": "g"}] + [{"from": "g", "to": f"b{i}"} for i in range(3)]
                                + [{"from": f"b{i}", "to": "m"} for i in range(3)],
                                groups=[{"label": "组", "members": ["g"]}]), 632, 5),
    "exits3_8": (exits(3, 8), 440, 15),
    "exits3_14": (exits(3, 14), 656, 15),
    "seq2_msg10": (seq(["A", "B"], [msg(0, 1, c(10))]), 480, 15),
    "seq2_name10": (seq([c(10), "B"], [msg(0, 1, "x")]), 480, 15),
    "seq2_note_first20": (seq(["A", "B"], [msg(0, 0, c(20), note=True)]), 565, 15),
    "seq3_note_mid18": (seq(["A", "B", "C"], [msg(1, 1, c(18), note=True)]), 658, 15),
    "seq2_note_both30": (seq(["A", "B"], [msg(0, 1, c(30), note=True)]), 550, 15),
    "seq2_self20": (seq(["A", "B"], [msg(0, 0, c(20))]), 554, 15),
    "seq3_skip30": (seq(["A", "B", "C"], [msg(0, 2, c(30))]), 650, 15),
    "seq3_both10": (seq(["A", "B", "C"], [msg(0, 1, c(10)), msg(2, 1, c(10))]), 710, 15),
}


def _ir(data: dict):
    parsed, issues = parse_ir(data)
    assert parsed is not None, issues
    return parsed


@pytest.mark.parametrize("name", sorted(MEASURED))
def test_estimate_matches_the_measured_width(name):
    data, real, tol = MEASURED[name]
    assert abs(estimate_width(_ir(data)) - real) <= tol, (name, estimate_width(_ir(data)), real)


def late_inputs() -> dict:
    """Set D's late-input data path (made-up labels of the same widths and roles): inputs that join deep in the
    diagram. Measured 587 px; with every input in the top layer the estimate was 786 and rejected it."""
    n = lambda k, w, off, role="step": {"key": k, "label": c(w, off), "role": role}
    e = lambda a, b, label=None: {"from": a, "to": b, **({"label": label} if label else {})}
    return flow([n("k", 9, 0, "input"), n("pub", 5, 1, "input"), n("enc", 7, 2), n("dis", 11, 3), n("old", 3, 4, "input"),
                 n("hmac", 5, 5), n("priv", 5, 6, "input"), n("dec", 5, 7), n("db", 5, 8, "store")],
                [e("k", "enc"), e("pub", "enc"), e("enc", "dis"), e("dis", "hmac"), e("old", "hmac"),
                 e("hmac", "dec", c(7, 9)), e("priv", "dec"), e("dec", "db", c(2, 10))], arrows="data")


def test_a_source_sits_just_above_its_successor():
    """dagre keeps edges short, so an input that joins late does not widen the top layer."""
    assert estimate_width(_ir(late_inputs())) <= 700


def test_inputs_are_estimated_on_the_wide_side():
    est = estimate_width(_ir(row(3, 7, role="input")))
    assert 614 <= est <= 700  # measured 614: parallelograms take less room than the estimate gives them


def test_state_diagrams_have_no_estimate():
    data = flow([{"key": "a", "label": "甲", "role": "state"}, {"key": "b", "label": "乙", "role": "state"}],
                [{"from": "a", "to": "b", "label": "去"}], form="state")
    assert estimate_width(_ir(data)) is None


def _v14(data: dict, limits: Limits = Limits(grounding_min=0.0, require_essence=False, require_back_edge=False)):
    card = Card(title="t", essence="", propositions=("p",))
    return [str(e) for e in validate(data, card, limits).errors if e.code == "V14"]


def test_v14_draws_the_line_at_700_px():
    assert _v14(row(3, 8)) == []                                  # 680
    assert _v14(row(3, 9)) == ["V14 estimated 728 px wide, limit 700: fewer nodes side by side, "
                               "shorter labels, or fewer participants"]
    assert _v14(row(3, 9), Limits(grounding_min=0.0, require_essence=False, require_back_edge=False,
                                  max_width=None)) == []
    assert _v14(seq([c(8), c(8, 3), c(8, 6)], [msg(0, 1, c(10)), msg(1, 2, c(10)), msg(2, 0, "x")])) != []


# The prompt's rule 7 ------------------------------------------------------------------

def _rule7() -> dict[str, int]:
    text = next(line for line in load_prompt().splitlines() if line.startswith("7. "))
    m = re.search(r"at most 3 nodes side by side, labels at most (\d+) wide when 3 are \((\d+) in a group\); at most 3 "
                  r"labelled edges side by side; sequence: names at most (\d+), notes and self-messages (\d+) wide, and "
                  r"with 3 participants messages (\d+) wide", text)
    assert m, text
    return dict(zip(("node3", "group3", "name", "note", "msg3"), map(int, m.groups())))


def rule7_cases() -> dict[str, dict]:
    """The largest diagrams rule 7 allows: each must fit 700 px."""
    r, lim = _rule7(), Limits()
    n3, g3, name, note, m3 = r["node3"], r["group3"], r["name"], r["note"], r["msg3"]
    edge, full = int(lim.edge_label_max), int(lim.message_max)
    names2, names3 = [c(name), c(name, 3)], [c(name), c(name, 3), c(name, 6)]
    return {
        "3 nodes, passing back edge": row(3, n3, back=True),
        "3 nodes in a group": row(3, g3, groups=True),
        "2 nodes in a group, longest labels": row(2, lim.node_label_max, groups=True),
        "3 inputs": row(3, n3, role="input"),
        "3 decisions": row(3, min(n3, lim.decision_label_max or n3), role="decision"),
        "2 nodes, longest labels": row(2, lim.node_label_max),
        "3 labelled exits, longest labels": exits(3, edge),
        "structure, 3 labelled edges": flow([{"key": f"b{i}", "label": c(n3, i)} for i in range(3)] + [{"key": "m", "label": "合"}],
                                            [{"from": f"b{i}", "to": "m", "label": c(edge, i + 5)} for i in range(3)],
                                            form="structure"),
        "sequence, 2 participants": seq(names2, [msg(0, 1, c(full, 1)), msg(1, 0, c(full, 2), style="reply"),
                                                 msg(0, 0, c(note, 3), note=True), msg(1, 1, c(note, 4), note=True),
                                                 msg(0, 1, c(note, 5), note=True), msg(1, 1, c(note, 6)),
                                                 msg(0, 0, c(note, 7))]),
        "sequence, 3 participants": seq(names3, [msg(0, 1, c(m3, 1)), msg(1, 2, c(m3, 2)), msg(2, 1, c(m3, 4)),
                                                 msg(0, 2, c(full, 5)), msg(0, 0, c(note, 7), note=True),
                                                 msg(2, 2, c(note, 8), note=True), msg(1, 1, c(note, 9), note=True),
                                                 msg(0, 1, c(note, 3), note=True), msg(1, 1, c(note, 2)),
                                                 msg(2, 2, c(note, 1)), msg(0, 0, c(note, 6))]),
    }


@pytest.mark.parametrize("name", sorted(rule7_cases()))
def test_prompt_rule_of_thumb_fits_the_estimate(name):
    est = estimate_width(_ir(rule7_cases()[name]))
    assert est <= 700, (name, est)


def _tool():
    node = shutil.which("node")
    modules = Path(os.environ.get("MERMAID_CHECK_MODULES") or TOOL.parent)
    if not node or not (modules / "node_modules" / "mermaid").is_dir():
        return None
    return node, modules


@pytest.mark.skipif(_tool() is None, reason="tools/mermaid_check dependencies not installed")
def test_rule_of_thumb_and_pinned_widths_render_as_measured(tmp_path):
    cases = {f"rule7: {k}": v for k, v in rule7_cases().items()}
    cases["rule7: late inputs"] = late_inputs()
    cases.update({k: v[0] for k, v in MEASURED.items()})
    src, out = tmp_path / "in.jsonl", tmp_path / "out.jsonl"
    src.write_text("".join(json.dumps({"id": k, "text": emit(_ir(v))}, ensure_ascii=False) + "\n"
                           for k, v in cases.items()), encoding="utf-8")
    node, modules = _tool()
    proc = subprocess.run([node, str(TOOL), "--modules", str(modules), str(src), str(out)],
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr
    got = {r["id"]: r for r in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
    for k in cases:
        if k.startswith("rule7: "):
            assert got[k]["width"] <= 700 and not got[k]["label_overlaps"], (k, got[k]["width"])
        else:
            assert abs(got[k]["width"] - MEASURED[k][1]) <= 2, (k, got[k]["width"], MEASURED[k][1])

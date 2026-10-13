"""V9 regression: every emitted diagram parses and renders in Obsidian's Mermaid.

The corpus puts each hazardous label (Mermaid keywords, syntax characters,
entities, HTML, math, odd Unicode) into every label position of every form,
plus seeded random diagrams, and runs them all through tools/mermaid_check.

This test needs Node.js, the tool's npm dependencies and a Chromium binary.
It is skipped unless they are present:
  MERMAID_CHECK_BROWSER  path to Chrome/Chromium (or a standard install location)
  MERMAID_CHECK_MODULES  directory holding node_modules (default: the tool dir)
  MERMAID_CHECK_RANDOM   number of random diagrams (default 600)
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from secondbrain.diagrams.emit import emit
from secondbrain.diagrams.ir import DiagramIR, Group, Link, Node, clean_text

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "mermaid_check" / "check.mjs"

HAZARDS = [
    "plain", "中文标签", "外层循环满 k 次？", "dist[n] > 0x3f3f3f3f / 2？",
    # keywords of the flowchart, sequence and state grammars
    "end", "END", "End", "graph", "flowchart", "style", "call", "class", "click", "subgraph",
    "direction", "default", "linkStyle", "classDef", "participant", "actor", "Note", "note",
    "loop", "alt", "else", "opt", "par", "and", "rect", "critical", "break", "activate",
    "deactivate", "autonumber", "title", "box", "create", "destroy", "link", "links", "as",
    "state", "choice", "over", "right of", "left of", "TB", "LR", "n1", "g1", "P1",
    "end end", "A as B", "accTitle: t", "accDescr: d",
    # statements that look like syntax
    "[*]", "<<choice>>", "<<fork>>", "--", "-->", "->>", "-->>", "-)", "--x", "==>", "-.->",
    "---", "~~~", "o--o", "x--x", "a --> b", "a -> b", "n1->>n2: hi", "n1 --> n2",
    'state "x" as y', "Note over n1: x", "classDef x fill:#f00;", "style n1 fill:#f00;",
    "linkStyle 0 stroke:#f00;", "style:#quot;x#quot;", 'style:"a"', "classDef:#a;",
    "click n1 call f()", "subgraph x [y]",
    # punctuation and brackets
    "A;B", ";", "a#b", "#", "#quot;", "#35;", "#lt;", "#9829;", "&amp;", "&lt;b&gt;", "&#123;",
    "&", "&&", "R&D", '"quoted"', '"', "it's", "'", "{}", "{a}", "{{a}}", "[a]", "[[a]]",
    "[(a)]", "([a])", "((a))", "(a)", "f(x)", "dist[e.b]", "C(h,w)", "[/api/v1]", "[\\x]",
    ">a]", "a|b", "|a|", "||", "|", ":", "a: b", "::", "key:: value", ",", "a, b", ".", "...",
    "…", "?", "？", "!", "@", "a@b", "^", "*", "**bold**", "_x_", "~x~", "+", "+a", "-a", "- a",
    "1. item", "# heading", "## h", "> quote", "=", "==", "a=b", "/", "//", "\\", "}", "]", ")",
    "http://example.com/a?b=c&d=e#f", "C:\\path\\x", "a\\nb", "\\n",
    # html, markdown, math, comments, directives
    "<br>", "<br/>", "<b>bold</b>", "<script>alert(1)</script>", "<img src=x onerror=alert(1)>",
    "`code`", "`", "$x^2$", "$$x^2$$", "\\(x\\)", "%%", "%% c", "%%{init: {\"theme\":\"dark\"}}%%",
    "100%", "50% off", "%{", "---",
    # unicode and whitespace
    "C₋₁", "x²", "α→β", "→", "⇒", "😀", "👨\u200d👩\u200d👧", "العربية", "עברית", "é", "e\u0301",
    "a\u200bb", "tab\there", "line1\nline2", "  padded  ", "x" * 60, "长" * 40,
]

WORDS = ["请求", "响应", "缓存", "命中", "回表", "索引", "load", "store", "retry", "token",
         "状态", "超时", "ACK", "SYN", "页表", "偏移", "x", "mid", "lo", "hi"]
FLOW_ROLES = ["step", "decision", "store", "input", "output", "start", "end"]


def _flow(label: str, slot: str) -> tuple[DiagramIR, list[str]]:
    role = slot if slot in FLOW_ROLES else "step"
    nodes = [Node("a", label if slot in FLOW_ROLES + ["structure"] else "开始节点", role),
             Node("b", "第二步", "step"), Node("c", "第三步", "step")]
    edges = [Link("a", "b", label if slot == "edge" else "条件"), Link("b", "c"),
             Link("c", "c", label if slot == "selfloop" else "重试")]
    groups = (Group(label, ("b", "c")),) if slot == "group" else ()
    form = "structure" if slot == "structure" else "flow"
    return DiagramIR("draw", form=form, question="q", nodes=tuple(nodes), edges=tuple(edges),
                     groups=groups), [label]


def _sequence(label: str, slot: str) -> tuple[DiagramIR, list[str]]:
    nodes = (Node("u", label if slot == "alias" else "用户", "actor"), Node("s", "服务器", "actor"))
    msgs = [Link("u", "s", "请求")]
    if slot in ("sync", "reply", "async"):
        msgs.append(Link("s", "u", label, style=slot))
    elif slot == "note1":
        msgs.append(Link("s", "s", label, note=True))
    elif slot == "note2":
        msgs.append(Link("u", "s", label, note=True))
    return DiagramIR("draw", form="sequence", question="q", nodes=nodes, messages=tuple(msgs)), [label]


def _state(label: str, slot: str) -> tuple[DiagramIR, list[str]]:
    role = slot if slot in ("start", "end") else "state"
    nodes = (Node("a", label if slot in ("state", "start", "end") else "空闲", role),
             Node("b", "运行", "state"), Node("c", "结束态", "end" if role != "end" else "state"))
    edges = (Link("a", "b", label if slot == "transition" else "启动"), Link("b", "c", "完成"),
             Link("b", "b", label if slot == "selfloop" else "心跳"))
    return DiagramIR("draw", form="state", question="q", nodes=nodes, edges=edges), [label]


SLOTS = ([(_flow, s) for s in FLOW_ROLES + ["edge", "group", "structure", "selfloop"]]
         + [(_sequence, s) for s in ("alias", "sync", "reply", "async", "note1", "note2")]
         + [(_state, s) for s in ("state", "start", "end", "transition", "selfloop")])


def _random_label(rng: random.Random) -> str:
    parts = [rng.choice(WORDS) for _ in range(rng.randint(1, 3))]
    if rng.random() < 0.6:
        parts.insert(rng.randint(0, len(parts)), rng.choice(HAZARDS))
    label = rng.choice(["", " "]).join(parts)
    return label if clean_text(label) else "x"


def _random_ir(rng: random.Random) -> tuple[DiagramIR, list[str]]:
    form = rng.choice(["flow", "structure", "sequence", "state"])
    n = rng.randint(1, 9)
    keys = [f"k{i}" for i in range(n)]
    labels: list[str] = []
    if form == "sequence":
        nodes = tuple(Node(k, _random_label(rng), "actor") for k in keys[:5])
        msgs = []
        for _ in range(rng.randint(1, 8)):
            a, b = rng.choice(nodes).key, rng.choice(nodes).key
            msgs.append(Link(a, b, _random_label(rng), style=rng.choice(["sync", "reply", "async"]),
                             note=rng.random() < 0.2))
        labels = [x.label for x in nodes] + [m.label for m in msgs]
        return DiagramIR("draw", form=form, question="q", nodes=nodes, messages=tuple(msgs)), labels
    roles = {"flow": FLOW_ROLES, "structure": ["step", "store", "input", "output", "actor"],
             "state": ["state", "start", "end"]}[form]
    nodes = tuple(Node(k, _random_label(rng), rng.choice(roles)) for k in keys)
    edges: list[Link] = []
    for _ in range(rng.randint(0, 12)):
        a, b = rng.choice(keys), rng.choice(keys)
        if a == b and any(e.src == e.dst == a for e in edges):
            continue  # V1 allows one self-loop per node
        edges.append(Link(a, b, _random_label(rng) if rng.random() < 0.7 else ""))
    edges = tuple(edges)
    groups: list[Group] = []
    if form in ("flow", "structure") and n >= 3 and rng.random() < 0.5:
        free = keys[:]
        rng.shuffle(free)
        while len(free) >= 2 and rng.random() < 0.7:
            take = [free.pop() for _ in range(rng.randint(1, min(3, len(free))))]
            groups.append(Group(_random_label(rng), tuple(take)))
    labels = [x.label for x in nodes] + [e.label for e in edges if e.label] + [g.label for g in groups]
    return DiagramIR("draw", form=form, question="q", nodes=nodes, edges=edges, groups=tuple(groups)), labels


def corpus(n_random: int = 600, seed: int = 20261013) -> list[tuple[str, str, list[str]]]:
    """(id, mermaid, labels expected in the rendered text) for the whole corpus."""
    out = []
    for k, h in enumerate(dict.fromkeys(HAZARDS)):
        if not clean_text(h):
            continue
        for make, slot in SLOTS:
            ir, labels = make(h, slot)
            out.append((f"{ir.form}:{slot}:{k}:{h}", emit(ir), labels))
    rng = random.Random(seed)
    for i in range(n_random):
        ir, labels = _random_ir(rng)
        marker = "%% diagram v1 props=P1,P2 sha=0123abcd" if rng.random() < 0.5 else None
        out.append((f"random:{i}", emit(ir, marker=marker), labels))
    return out


def _squash(s: str) -> str:
    return "".join(clean_text(s).split())


def _tool_ready() -> tuple[str, Path] | None:
    node = shutil.which("node")
    modules = Path(os.environ.get("MERMAID_CHECK_MODULES") or TOOL.parent)
    if not node or not (modules / "node_modules" / "mermaid").is_dir():
        return None
    return node, modules


def test_corpus_is_deterministic_and_one_line_per_label():
    a, b = corpus(50), corpus(50)
    assert [x[1] for x in a] == [x[1] for x in b]
    for _, mm, _ in a:
        lines = mm.split("\n")
        body = lines[2:] if lines[0].startswith("%% ") else lines[1:]
        for line in body:
            assert line.startswith("  "), line


@pytest.mark.skipif(_tool_ready() is None, reason="tools/mermaid_check dependencies not installed")
def test_every_emitted_diagram_parses_and_keeps_its_labels(tmp_path):
    node, modules = _tool_ready()
    items = corpus(int(os.environ.get("MERMAID_CHECK_RANDOM", "600")))
    src = tmp_path / "corpus.jsonl"
    src.write_text("".join(json.dumps({"id": i, "text": mm}, ensure_ascii=False) + "\n"
                           for i, mm, _ in items), encoding="utf-8")
    out = tmp_path / "results.jsonl"
    proc = subprocess.run([node, str(TOOL), "--modules", str(modules), "--label-text", str(src), str(out)],
                          capture_output=True, text=True, timeout=3600)
    assert out.exists(), proc.stderr
    results = {r["id"]: r for r in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
    expected = {i: labels for i, _, labels in items}
    broken = [(i, r.get("error") or r.get("render_error")) for i, r in results.items()
              if not r["ok"] or r.get("render_error")]
    lost = [(i, label) for i, r in results.items() if r["ok"] and not r.get("render_error")
            for label in expected[i] if _squash(label) not in _squash(r.get("label_text", ""))]
    assert len(results) == len(items) == len(expected)
    assert not broken, broken[:10]
    assert not lost, lost[:10]

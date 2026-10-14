"""audit-repair: broken old flowcharts rewritten so they parse, without changing what they say.

The cases are written for this test (no vault content); each pattern is one the
vault's broken diagrams showed. With tools/mermaid_check installed, every repair
must render and keep every label, and diagrams that already render must read
back with the same nodes and edges.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from secondbrain.diagrams import audit, prune, rejudge
from secondbrain.diagrams.repair import repair

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "mermaid_check" / "check.mjs"

BROKEN = {
    "nested brackets": "flowchart TD\n    A[开始] --> B[取 dp[i][j]]\n    B --> C{区间 [L, R] 为空?}\n    C -->|是| D[结束]\n",
    "parentheses": "flowchart LR\nA[梯度 g] --> B[步长 lr/sqrt(G+eps)]\nB --> C[更新 θ]\n",
    "pipe in label": "flowchart TD\nA[事件 A] --> B[条件概率 P(A|B)]\nB --> C[全概率 P(A)]\n",
    "keyword ids": 'flowchart LR\n  call["函数调用"] -->|"解析"| ast["AST"]\n  style["风格"] --> call\n  graph["图"] --> end\n',
    "one line": "flowchart LR A[发出请求] --> B[生成脉冲] --> C[启动转换]",
    "missing breaks": "flowchart TD\nA[入口] --> B[处理]B --> C[出口]C --> D[日志]\n",
    "semicolons": "flowchart LR; A[RP] -->|RTR| B[获取 VRP]; B --> C{匹配?}; C -->|是| D[Valid]",
    "edge text": "flowchart TD\nA[请求] -- 超时(30s) --> B[重试]\nB --> A\n",
    "pipe edge label": "flowchart TD\nA[事件] -->|P(B|A)| B[后验]\n",
    "symbol ids": "flowchart LR\n  C₋₁ --> C₀\n  C₀ --> C₁\n",
    "subgraph title": "flowchart TB\nsubgraph S[阶段(一)]\nA[读] --> B[写]\nend\nB --> C[完成]\n",
    "math label": "flowchart TD\nA[下界] --> B[$\\max\\{NF/u_s, F/d_{min}\\}$]\n",
    "quotes in label": 'flowchart TD\nA[他说 "你好" (再见)] --> B[结束]\n',
}


def _labels(text: str) -> list[str]:
    return re.findall(r'"([^"]*)"', text)


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_repair_quotes_every_label_and_puts_one_statement_per_line(name):
    r = repair(BROKEN[name])
    assert r is not None and r.fixes, name
    lines = r.text.splitlines()
    assert lines[0].split()[0] in ("flowchart", "graph")
    for line in lines[1:]:
        assert line.startswith("  ") and line.count("-->") <= 2, line
    for ch in re.findall(r"[一-鿿]", BROKEN[name]):
        assert ch in r.text  # no text is lost


def test_repair_details():
    assert '"他说 #quot;你好#quot; (再见)"' in repair(BROKEN["quotes in label"]).text
    assert '"取 dp[i][j]"' in repair(BROKEN["nested brackets"]).text
    assert '"区间 [L, R] 为空?"' in repair(BROKEN["nested brackets"]).text
    kw = repair(BROKEN["keyword ids"])
    assert "call_n" in kw.text and "style_n" in kw.text and "graph_n" in kw.text and "end_n" in kw.text
    assert "keyword id" in kw.fixes
    # V3 Y4: a renamed id with no shape shows its old id, not the new name
    assert 'end_n["end"]' in kw.text and 'call_n["函数调用"]' in kw.text
    assert repair('flowchart TD\n  A[开始] --> end\n  end --> B[结束了]').text.count('end_n["end"]') == 1
    assert repair(BROKEN["one line"]).text.splitlines()[0] == "flowchart LR"
    assert len(repair(BROKEN["missing breaks"]).text.splitlines()) == 4
    assert '-->|"超时(30s)"|' in repair(BROKEN["edge text"]).text
    assert '-->|"P(B|A)"|' in repair(BROKEN["pipe edge label"]).text
    sym = repair(BROKEN["symbol ids"]).text
    assert '["C₋₁"]' in sym and "C₋₁ -->" not in sym
    assert 'subgraph S["阶段(一)"]' in repair(BROKEN["subgraph title"]).text


def test_repair_keeps_quoted_labels_style_lines_and_comments():
    src = ('flowchart TD\n  %% a comment\n  a["已加引号 (x)"] --> b[未加引号 (y)]\n  classDef hot fill:#f96\n'
           '  class a hot\n  style b fill:#fff\n')
    text = repair(src).text
    assert '"已加引号 (x)"' in text and '"未加引号 (y)"' in text
    assert "  %% a comment" in text and "  classDef hot fill:#f96" in text and "  style b fill:#fff" in text


def test_repair_gives_up_on_what_it_cannot_read():
    assert repair("sequenceDiagram\n  A->>B: hi") is None
    assert repair("flowchart TD\nA[x] -->|y|\n") is None          # an edge to nowhere


# Through the audit, prune and write-back ----------------------------------------------

def _card(ref: str) -> dict:
    return {"ref": ref, "title": ref, "kind": "mechanism", "domain": "d", "essence": "e", "sha256": "s" + ref,
            "propositions": ["p1", "p2", "p3"], "blocks": ["…"]}


GOOD = {"ok": True, "type": "flowchart-v2", "direction": "LR", "width": 900,
        "nodes": [{"id": f"n{i}", "label": f"步骤{i}", "shape": "square"} for i in range(5)],
        "edges": [{"from": "n0", "to": "n1", "label": ""}, {"from": "n1", "to": "n2", "label": "小于"},
                  {"from": "n1", "to": "n3", "label": "大于"}, {"from": "n2", "to": "n4", "label": ""},
                  {"from": "n3", "to": "n4", "label": ""}]}


def test_tier_uses_a_repair_that_renders_and_records_it():
    cards = [_card("a.md"), _card("b.md")]
    checks = [{"id": "a.md#0", "ok": False, "error": "Parse error"}, {"id": "b.md#0", "ok": False, "error": "x"}]
    repairs = [{"id": "a.md#0", "text": "flowchart LR\n  n0 --> n1", "fixes": ["quoted label"], "check": GOOD},
               {"id": "b.md#0", "text": "flowchart LR", "fixes": [], "check": {"ok": False, "error": "still"}}]
    report = audit.tier(cards, checks, repairs=repairs)
    a, b = report.cards["a.md"], report.cards["b.md"]
    assert a["ok"] and a["repair"] == {"text": "flowchart LR\n  n0 --> n1", "fixes": ["quoted label"]}
    assert (a["direction"], a["width"]) == ("LR", 900) and a["tier"] != "redraw"
    assert b["tier"] == "redraw" and "repair" not in b
    assert report.to_dict()["repaired"] == 1


VAULT = Path(__file__).resolve().parents[1] / "fixtures" / "diagrams" / "vault"
TCP = "10_Knowledge/networking/TCP 三次握手.md"
FIXED = 'flowchart LR\n  c["客户端"] -->|"SYN"| s["服务器"]\n  s -->|"SYN+ACK"| c\n  c -->|"ACK"| s'


def _tcp(tmp_path: Path) -> tuple[Path, str]:
    shutil.copytree(VAULT, tmp_path / "v")
    path = tmp_path / "v" / TCP
    import hashlib
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_prune_writes_the_repaired_source_of_a_kept_diagram(tmp_path):
    path, sha = _tcp(tmp_path)
    before = path.read_text(encoding="utf-8")
    tiers = {TCP: {"tier": "keep", "why": "branch", "kind": "mechanism", "n_props": 3, "sha256": sha, "ok": True,
                   "direction": "LR", "width": 900, "repair": {"text": FIXED, "fixes": ["quoted label"]}}}
    [act] = prune.plan(tiers)
    assert (act["action"], act["flip"]) == ("repair", True)
    [row] = prune.apply(tmp_path / "v", [act], write=True)
    assert row["status"] == "written"
    old_block = before.split("```mermaid\n")[1].split("```")[0]
    assert path.read_text(encoding="utf-8") == before.replace(old_block, FIXED.replace("flowchart LR", "flowchart TB") + "\n")


def test_writeback_keeps_a_repaired_old_diagram_as_repaired(tmp_path):
    path, sha = _tcp(tmp_path)
    before = path.read_text(encoding="utf-8")
    tiers = {TCP: {"tier": "review", "why": "no_gain", "kind": "mechanism", "n_props": 3, "sha256": sha, "ok": True,
                   "type": "flowchart-v2", "direction": "LR", "width": 500, "graph": {"labels": [], "edges": []},
                   "repair": {"text": FIXED, "fixes": ["quoted label"]}}}
    pool = rejudge.select_pool(tiers)
    cards = [{**audit.read_card(before, TCP), "sha256": sha}]
    assert rejudge.pool_cards(pool, cards)[0]["old_mermaid"] == FIXED          # packets show the repair
    skip = [{"ref": TCP, "status": "skip", "reason": "x"}]
    [act] = rejudge.plan(pool, skip)
    assert (act["action"], act["rule"]) == ("repair_old", "skip:old_parses+repaired")
    assert [p["ref"] for p in rejudge.audit_candidates(pool, skip)] == [TCP]   # still audited
    [row] = rejudge.apply(tmp_path / "v", [act], write=True)
    assert row["status"] == "written"
    old_block = before.split("```mermaid\n")[1].split("```")[0]
    assert path.read_text(encoding="utf-8") == before.replace(old_block, FIXED + "\n")
    [gone] = rejudge.plan(pool, skip, audits=[{"ref": TCP, "verdict": "remove", "category": "restates"}])
    assert gone["action"] == "remove"                                       # the audit can still remove it
    wide = rejudge.select_pool({TCP: {**tiers[TCP], "width": 900}})
    [flip] = rejudge.plan(wide, skip)
    assert (flip["action"], flip["flip"], flip["rule"]) == ("repair_old", True, "skip:old_parses+tb+repaired")


# With the browser ------------------------------------------------------------------------

def _tool():
    node = shutil.which("node")
    modules = Path(os.environ.get("MERMAID_CHECK_MODULES") or TOOL.parent)
    if not node or not (modules / "node_modules" / "mermaid").is_dir():
        return None
    return node, modules


VALID = [
    'flowchart TD\n  A["开始"] --> B{"判断"}\n  B -->|"是"| C["处理"]\n  B -->|"否"| D["结束"]\n  C --> B',
    "graph LR\n  a[输入] --> b((圆)) --> c[(库)]\n  a -.-> d>旗] & e{{六边形}}\n  d ==> f([体育场])",
    "flowchart TB\n  subgraph S1 [第一组]\n    x1[甲] --> x2[乙]\n  end\n  x2 --- y[丙]\n  classDef k fill:#eee\n  class y k",
]


@pytest.mark.skipif(_tool() is None, reason="tools/mermaid_check dependencies not installed")
def test_repairs_render_keep_their_labels_and_valid_diagrams_read_back_the_same(tmp_path):
    node, modules = _tool()
    blocks = [{"id": f"broken:{k}", "text": repair(v).text} for k, v in BROKEN.items()]
    blocks += [{"id": f"valid:{i}", "text": t} for i, t in enumerate(VALID)]
    blocks += [{"id": f"reread:{i}", "text": repair(t).text} for i, t in enumerate(VALID)]
    src, out = tmp_path / "in.jsonl", tmp_path / "out.jsonl"
    src.write_text("".join(json.dumps(b, ensure_ascii=False) + "\n" for b in blocks), encoding="utf-8")
    proc = subprocess.run([node, str(TOOL), "--modules", str(modules), "--label-text", str(src), str(out)],
                          capture_output=True, text=True, timeout=600)
    got = {r["id"]: r for r in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
    assert proc.returncode == 0, (proc.stderr, [k for k, r in got.items() if not r["ok"]])
    squash = lambda s: re.sub(r"\s+", "", s)
    for k, v in BROKEN.items():
        text = squash(got[f"broken:{k}"].get("label_text", ""))
        for run in re.findall(r"[一-鿿]{2,}", v):
            assert run in text, (k, run)
    for i in range(len(VALID)):
        a, b = got[f"valid:{i}"], got[f"reread:{i}"]
        assert (len(a["nodes"]), len(a["edges"])) == (len(b["nodes"]), len(b["edges"])), i


@pytest.mark.skipif(_tool() is None, reason="tools/mermaid_check dependencies not installed")
def test_audit_repair_command(tmp_path, capsys):
    from secondbrain.diagrams.__main__ import main
    node, modules = _tool()
    d = tmp_path / "audit"
    d.mkdir()
    blocks = [{"id": "a.md#0", "text": BROKEN["nested brackets"]}, {"id": "b.md#0", "text": "flowchart TD\nA[x] -->|y|\n"},
              {"id": "c.md#0", "text": VALID[0]}]
    checks = [{"id": "a.md#0", "ok": False, "error": "Parse error"}, {"id": "b.md#0", "ok": False, "error": "Parse error"},
              {"id": "c.md#0", "ok": True}]
    for name, rows in (("blocks.jsonl", blocks), ("checks.jsonl", checks)):
        (d / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    assert main(["audit-repair", str(d), "--checker", str(TOOL), "--checker-modules", str(modules)]) == 0
    assert json.loads(capsys.readouterr().out) == {"broken": 2, "rewritten": 1, "renders": 1, "unreadable": 1}
    [row] = [json.loads(x) for x in (d / "repairs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert row["id"] == "a.md#0" and row["check"]["ok"] and row["text"] == repair(BROKEN["nested brackets"]).text

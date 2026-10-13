"""Audit of existing diagrams (read-only) and pruning on a copy."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from secondbrain.diagrams import audit, prune
from secondbrain.diagrams.__main__ import main

VAULT = Path(__file__).resolve().parents[1] / "fixtures" / "diagrams" / "vault"
TCP = "10_Knowledge/networking/TCP 三次握手.md"
BUBBLE = "10_Knowledge/algorithms/冒泡排序.md"
DRINKS = "10_Knowledge/beverages/茶与咖啡的提神方式.md"


def card(ref: str) -> dict:
    return audit.read_card((VAULT / ref).read_text(encoding="utf-8"), ref)


def test_read_card_fields_and_blocks():
    c = card(TCP)
    assert (c["title"], c["kind"], c["domain"]) == ("TCP 三次握手", "mechanism", "networking")
    assert c["essence"] == "客户端与服务器交换三个报文段后才建立连接。"
    assert c["propositions"][2] == "客户端回复 ACK 后双方进入 ESTABLISHED。"
    assert c["related_titles"] == ["TCP 四次挥手", "可靠传输"]
    assert len(c["blocks"]) == 1 and c["blocks"][0].startswith("flowchart LR\n")
    b = card(BUBBLE)
    assert "代码" in audit.sections(audit.split_frontmatter((VAULT / BUBBLE).read_text(encoding="utf-8"))[1])
    assert len(b["blocks"]) == 1 and b["related_titles"] == ["选择排序"]
    assert card(DRINKS)["blocks"] == []


def test_extract_writes_cards_and_checker_input(tmp_path):
    assert audit.extract(VAULT, tmp_path) == {"cards": 3, "blocks": 2}
    blocks = list(audit.read_jsonl(tmp_path / "blocks.jsonl"))
    assert [b["id"] for b in blocks] == [f"{BUBBLE}#0", f"{TCP}#0"]


def _check(nodes, edges, **kw):
    return {"ok": True, "type": "flowchart-v2", "direction": "TB",
            "nodes": [{"id": n, "label": label, "shape": shape} for n, label, shape in nodes],
            "edges": [{"from": a, "to": b, "label": lab} for a, b, lab in edges], **kw}


def _card(kind="mechanism", domain="algorithms", props=("取区间中点 mid", "比较 a[mid] 与目标值")):
    return {"ref": "x.md", "title": "二分查找", "kind": kind, "domain": domain, "essence": "",
            "propositions": list(props), "sha256": "0", "blocks": ["…"]}


GROUNDED = [("a", "取区间中点 mid", "square"), ("b", "比较 a[mid] 与目标值", "diamond"),
            ("c", "取区间中点", "square"), ("d", "比较目标值", "square")]
PLAIN = [(n, label, "square") for n, label, _ in GROUNDED]
BRANCH = [("a", "b", ""), ("b", "c", "小于"), ("b", "d", "大于")]


@pytest.mark.parametrize("card_kw, check, expected", [
    ({"domain": "english"}, _check(GROUNDED, BRANCH), ("delete", "domain")),
    ({}, {"ok": False, "error": "Parse error"}, ("redraw", "broken")),
    ({"kind": "fact"}, {"ok": False, "error": "Parse error"}, ("delete", "broken+fact/contrast")),
    ({}, {"ok": True, "type": "sequence"}, ("keep", "sequence/state")),
    ({}, _check(GROUNDED[:3], BRANCH[:2]), ("delete", "le2_edges")),
    ({}, _check(GROUNDED, [("a", "b", "x"), ("b", "c", "y"), ("c", "d", "z")]), ("delete", "short_chain")),
    ({"kind": "contrast"}, _check(GROUNDED[:1] + [("b", "比较", "square")] + GROUNDED[2:] + [("e", "e", "square")],
                                  [("a", "b", ""), ("a", "c", ""), ("a", "d", ""), ("d", "e", "")]),
     ("delete", "fact/contrast_no_gain")),
    ({}, _check(PLAIN + [("e", "e", "square")], [("a", "b", "同"), ("a", "c", "同"), ("a", "d", "同"),
                                                     ("d", "e", "同")]),
     ("delete", "star_same_label")),
    ({}, _check(GROUNDED, BRANCH), ("keep", "branch")),
    ({}, _check([(n, "无关词语", s) for n, _, s in GROUNDED], BRANCH), ("review", "gain_low_grounding")),
    ({}, _check(PLAIN + [("e", "e", "square")], [("a", "b", "1"), ("a", "c", "2"), ("a", "d", "3"),
                                                     ("d", "e", "4")]),
     ("review", "no_gain")),
])
def test_tier_rules(card_kw, check, expected):
    rules = audit.TierRules(delete_domains=("english",))
    assert audit.tier_one(_card(**card_kw), check, rules) == expected


def test_shapes():
    n = lambda *ids: [{"id": i} for i in ids]  # noqa: E731
    e = lambda *pairs: [{"from": a, "to": b} for a, b in pairs]  # noqa: E731
    assert audit.shape_of(n("a", "b", "c"), e(("a", "b"), ("b", "c"))) == "chain"
    assert audit.shape_of(n("a", "b", "c", "d"), e(("a", "b"), ("a", "c"), ("a", "d"))) == "star"
    assert audit.shape_of(n("a", "b", "c"), e(("a", "b"), ("b", "c"), ("c", "a"))) == "cyclic"
    assert audit.shape_of(n("a", "b", "c", "d", "e"), e(("a", "b"), ("a", "c"), ("c", "d"), ("c", "e"))) == "tree"
    assert audit.shape_of(n("a", "b", "c", "d"), e(("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"))) == "dag"
    assert audit.shape_of([], []) == "empty"


def test_delete_section_keeps_everything_else():
    text = (VAULT / BUBBLE).read_text(encoding="utf-8")
    out = prune.delete_section(text, "图")
    assert "```mermaid" not in out and "## 图" not in out
    assert out.replace("\n", "") == text.replace(text[text.index("## 图"):text.index("## 代码")], "").replace("\n", "")
    assert "## not a heading inside a code block" in out
    with pytest.raises(ValueError):
        prune.delete_section(out, "图")


def test_direction_tb_only_touches_the_header():
    text = (VAULT / TCP).read_text(encoding="utf-8")
    out = prune.direction_tb(text, "图")
    assert out == text.replace("flowchart LR\n", "flowchart TB\n", 1)
    marked = text.replace("```mermaid\n", "```mermaid\n%% note\n", 1)
    assert "%% note\nflowchart TB" in prune.direction_tb(marked, "图")
    with pytest.raises(ValueError):
        prune.direction_tb(out, "图")


def test_plan_keeps_review_pool_and_flips_only_wide_lr():
    tiers = {
        "a.md": {"tier": "delete", "why": "le2_edges", "kind": "mechanism", "n_props": 3, "sha256": "1"},
        "b.md": {"tier": "delete", "why": "le2_edges", "kind": "mechanism", "n_props": 2, "sha256": "2"},
        "c.md": {"tier": "keep", "why": "branch", "kind": "fact", "n_props": 1, "sha256": "3",
                 "direction": "LR", "width": 900},
        "d.md": {"tier": "keep", "why": "branch", "kind": "fact", "n_props": 1, "sha256": "4",
                 "direction": "TB", "width": 900},
        "e.md": {"tier": "keep", "why": "branch", "kind": "fact", "n_props": 1, "sha256": "5",
                 "direction": "LR", "width": 640},
        "f.md": {"tier": "review", "why": "no_gain", "kind": "fact", "n_props": 1, "sha256": "6"},
    }
    assert [(a["ref"], a["action"]) for a in prune.plan(tiers)] == [("b.md", "delete_section"),
                                                                    ("c.md", "direction_tb")]


def test_apply_previews_writes_and_refuses_edited_files(tmp_path):
    shutil.copytree(VAULT, tmp_path / "v")
    v = tmp_path / "v"
    sha = card(TCP)["sha256"]
    acts = [{"ref": TCP, "action": "direction_tb", "why": "width 900", "sha256": sha},
            {"ref": BUBBLE, "action": "delete_section", "why": "le2_edges", "sha256": "stale"},
            {"ref": "10_Knowledge/gone.md", "action": "delete_section", "why": "x", "sha256": "0"}]
    preview = prune.apply(v, acts)
    assert [r["status"] for r in preview] == ["planned", "conflict", "missing"]
    assert (v / TCP).read_text(encoding="utf-8") == (VAULT / TCP).read_text(encoding="utf-8")
    written = prune.apply(v, acts, write=True)
    assert written[0]["status"] == "written" and (written[0]["lines_added"], written[0]["lines_removed"]) == (1, 1)
    assert "flowchart TB" in (v / TCP).read_text(encoding="utf-8")
    assert (v / BUBBLE).read_text(encoding="utf-8") == (VAULT / BUBBLE).read_text(encoding="utf-8")


def test_cli_audit_round_trip(tmp_path, capsys):
    shutil.copytree(VAULT, tmp_path / "v")
    out = tmp_path / "audit"
    assert main(["audit-extract", str(tmp_path / "v"), str(out)]) == 0
    checks = [{"id": f"{TCP}#0", "ok": True, "type": "flowchart-v2", "direction": "LR", "width": 820,
               "nodes": [{"id": "c", "label": "客户端", "shape": "square"},
                         {"id": "s", "label": "服务器", "shape": "diamond"}],
               "edges": [{"from": "c", "to": "s", "label": "SYN"}, {"from": "s", "to": "c", "label": "SYN+ACK"},
                         {"from": "c", "to": "s", "label": "ACK"}]},
              {"id": f"{BUBBLE}#0", "ok": True, "type": "flowchart-v2", "direction": "TB", "width": 200,
               "nodes": [{"id": "A", "label": "比较相邻元素", "shape": "square"},
                         {"id": "B", "label": "逆序则交换", "shape": "square"}],
               "edges": [{"from": "A", "to": "B", "label": ""}]}]
    (out / "checks.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in checks),
                                      encoding="utf-8")
    assert main(["audit-tier", str(out)]) == 0
    tiers = json.loads((out / "tiers.json").read_text(encoding="utf-8"))
    assert tiers["counts"] == {"keep": 1, "delete": 1}
    assert tiers["cards"][TCP]["why"] == "branch"
    assert main(["prune", str(tmp_path / "v"), str(out), "--write"]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["actions"] == {"delete_section": 1, "direction_tb": 1}
    assert summary["status"] == {"written": 2}
    assert "## 图" not in (tmp_path / "v" / BUBBLE).read_text(encoding="utf-8")


def test_merge_counts_as_gain_only_from_five_nodes():
    labels = [("a", "取区间中点 mid"), ("b", "比较目标值"), ("c", "取区间中点"), ("d", "比较 a[mid] 与目标值"),
              ("e", "目标值")]
    nodes = [(k, v, "square") for k, v in labels]
    five = _check(nodes, [("a", "c", "1"), ("b", "c", "2"), ("c", "d", "3"), ("d", "e", "4")])
    assert audit.tier_one(_card(), five) == ("keep", "merge")
    four = _check(nodes[:4], [("a", "c", "1"), ("b", "c", "2"), ("c", "d", "3")])
    assert audit.tier_one(_card(), four) == ("review", "no_gain")

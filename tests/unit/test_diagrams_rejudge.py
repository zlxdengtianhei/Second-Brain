"""Re-judge pool selection and byte-precise write-back of model outcomes."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from secondbrain.diagrams import audit, prune, rejudge
from secondbrain.diagrams.__main__ import main

VAULT = Path(__file__).resolve().parents[1] / "fixtures" / "diagrams" / "vault"
TCP = "10_Knowledge/networking/TCP 三次握手.md"
BUBBLE = "10_Knowledge/algorithms/冒泡排序.md"
DRINKS = "10_Knowledge/beverages/茶与咖啡的提神方式.md"
NEW = "%% diagram v1 props=P1,P2 sha=0123abcd\nflowchart TB\n  n1[\"客户端\"] -->|\"SYN\"| n2[\"服务器\"]"

TIERS = {
    "a.md": {"tier": "review", "why": "no_gain", "kind": "mechanism", "n_props": 1, "sha256": "1", "ok": True},
    "b.md": {"tier": "redraw", "why": "broken", "kind": "concept", "n_props": 2, "sha256": "2", "ok": False},
    "c.md": {"tier": "delete", "why": "le2_edges", "kind": "mechanism", "n_props": 3, "sha256": "3", "ok": True},
    "g.md": {"tier": "delete", "why": "domain", "kind": "mechanism", "n_props": 3, "sha256": "7", "ok": True,
             "domain": "english"},
    "h.md": {"tier": "review", "why": "no_gain", "kind": "concept", "n_props": 2, "sha256": "8", "ok": True,
             "domain": "vocab"},
    "d.md": {"tier": "delete", "why": "le2_edges", "kind": "mechanism", "n_props": 2, "sha256": "4", "ok": True},
    "e.md": {"tier": "keep", "why": "branch", "kind": "mechanism", "n_props": 4, "sha256": "5", "ok": True,
             "direction": "LR", "width": 900},
    "f.md": {"tier": "delete", "why": "fact/contrast_no_gain", "kind": "contrast", "n_props": 4, "sha256": "6",
             "ok": True},
}


def test_pool_is_review_redraw_and_kept_back_deletes():
    pool = rejudge.select_pool(TIERS)
    assert [(p["ref"], p["pool_reason"], p["old_parses"]) for p in pool] == [
        ("a.md", "review", True), ("b.md", "redraw", False), ("c.md", "delete_review", True),
        ("g.md", "delete_review", True), ("h.md", "review", True)]
    touched_by_prune = {a["ref"] for a in prune.plan(TIERS)}
    assert touched_by_prune == {"d.md", "e.md", "f.md"}
    assert not touched_by_prune & {p["ref"] for p in pool}


def test_plan_applies_the_write_back_rule():
    pool = rejudge.select_pool(TIERS)
    outcomes = [{"ref": "a.md", "status": "rewrite"}, {"ref": "a.md", "status": "draw", "mermaid": NEW},
                {"ref": "b.md", "status": "skip", "reason": "list"},
                {"ref": "c.md", "status": "skip", "reason": "linear"},
                {"ref": "e.md", "status": "draw", "mermaid": NEW}]
    acts = {a["ref"]: (a["rule"], a["action"]) for a in rejudge.plan(pool, outcomes)}
    assert acts == {"a.md": ("draw", "replace"), "b.md": ("skip:old_broken", "remove"),
                    "c.md": ("skip:delete_review", "remove"), "e.md": ("not_in_pool", "not_in_pool"),
                    "g.md": ("pending", "pending"), "h.md": ("pending", "pending")}
    pending = rejudge.plan(pool, [{"ref": "a.md", "status": "rewrite"}])
    assert [a["action"] for a in pending] == ["pending"] * 5


def _skips(policy=rejudge.WritebackPolicy()):
    pool = rejudge.select_pool(TIERS)
    outcomes = [{"ref": p["ref"], "status": "skip", "reason": "no structure"} for p in pool]
    return {a["ref"]: (a["rule"], a["action"]) for a in rejudge.plan(pool, outcomes, policy)}


def test_skip_policy_by_pool_reason_and_domain():
    assert _skips() == {
        "a.md": ("skip:old_parses", "keep_old"),       # (c) review card, old diagram renders
        "b.md": ("skip:old_broken", "remove"),         # (c) redraw card, old diagram broken
        "c.md": ("skip:delete_review", "remove"),      # (a) already judged a restatement
        "g.md": ("skip:domain", "remove"),             # (b) the audit tiered the domain for deletion
        "h.md": ("skip:old_parses", "keep_old"),
    }


def test_delete_review_switch_keeps_the_domain_rule():
    flipped = _skips(rejudge.WritebackPolicy(delete_review_on_skip="keep"))
    assert flipped["c.md"] == ("skip:old_parses", "keep_old")
    assert flipped["g.md"] == ("skip:domain", "remove")
    listed = _skips(rejudge.WritebackPolicy(delete_review_on_skip="keep", remove_domains=("vocab",)))
    assert listed["h.md"] == ("skip:domain", "remove")
    with pytest.raises(ValueError):
        rejudge.WritebackPolicy(delete_review_on_skip="drop")


def test_summary_counts_rule_per_pool_reason():
    pool = rejudge.select_pool(TIERS)
    outcomes = [{"ref": "a.md", "status": "draw", "mermaid": NEW}, {"ref": "b.md", "status": "skip"},
                {"ref": "c.md", "status": "skip"}, {"ref": "g.md", "status": "skip"},
                {"ref": "e.md", "status": "skip"}]
    assert rejudge.summary(rejudge.plan(pool, outcomes)) == {
        "delete_review": {"skip:delete_review→remove": 1, "skip:domain→remove": 1},
        "not_in_pool": {"not_in_pool→not_in_pool": 1},
        "redraw": {"skip:old_broken→remove": 1},
        "review": {"draw→replace": 1, "pending→pending": 1},
    }


def test_replace_section_changes_only_that_section():
    text = (VAULT / TCP).read_text(encoding="utf-8")
    out = rejudge.replace_section(text, NEW)
    head, tail = text.split("## 图\n", 1)
    rest = tail[tail.index("\n## "):]
    assert out == head + "## 图\n```mermaid\n" + NEW + "\n```\n" + rest
    assert rejudge.replace_section(out, NEW) == out


def test_replace_section_at_end_of_file_and_insert_when_missing():
    text = "## 本质\ne\n\n## 命题\n- P1. p\n\n## 图\n```mermaid\nflowchart LR\n  a --> b\n```\n"
    assert rejudge.replace_section(text, "flowchart TB") == \
        "## 本质\ne\n\n## 命题\n- P1. p\n\n## 图\n```mermaid\nflowchart TB\n```\n"
    drinks = (VAULT / DRINKS).read_text(encoding="utf-8")
    out = rejudge.replace_section(drinks, "flowchart TB")
    assert out == drinks.replace("\n## 相关", "\n## 图\n```mermaid\nflowchart TB\n```\n\n## 相关")
    with pytest.raises(ValueError):
        rejudge.replace_section("no sections here\n", "flowchart TB")


def test_apply_guards_hash_and_previews_by_default(tmp_path):
    shutil.copytree(VAULT, tmp_path / "v")
    v = tmp_path / "v"
    sha = {r: audit.read_card((VAULT / r).read_bytes().decode("utf-8"), r)["sha256"] for r in (TCP, BUBBLE)}
    acts = [{"ref": TCP, "action": "replace", "mermaid": NEW, "sha256": sha[TCP]},
            {"ref": BUBBLE, "action": "remove", "sha256": sha[BUBBLE]},
            {"ref": DRINKS, "action": "remove", "sha256": "stale"},
            {"ref": "x.md", "action": "keep_old", "sha256": "0"}]
    assert [r["status"] for r in rejudge.apply(v, acts)] == ["planned", "planned", "conflict", "unchanged"]
    assert (v / TCP).read_bytes() == (VAULT / TCP).read_bytes()
    rows = rejudge.apply(v, acts, write=True)
    assert [r["status"] for r in rows] == ["written", "written", "conflict", "unchanged"]
    assert (v / TCP).read_text(encoding="utf-8") == rejudge.replace_section(
        (VAULT / TCP).read_text(encoding="utf-8"), NEW)
    bubble = (v / BUBBLE).read_text(encoding="utf-8")
    assert "## 图" not in bubble and "## 代码" in bubble and "## not a heading inside a code block" in bubble
    assert (v / DRINKS).read_bytes() == (VAULT / DRINKS).read_bytes()


def test_cli_pool_and_writeback(tmp_path, capsys):
    shutil.copytree(VAULT, tmp_path / "v")
    out = tmp_path / "audit"
    main(["audit-extract", str(tmp_path / "v"), str(out)])
    checks = [{"id": f"{TCP}#0", "ok": True, "type": "flowchart-v2", "direction": "LR", "width": 500,
               "nodes": [{"id": "c", "label": "客户端", "shape": "square"}, {"id": "s", "label": "服务器", "shape": "square"}],
               "edges": [{"from": "c", "to": "s", "label": "SYN"}, {"from": "s", "to": "c", "label": "SYN+ACK"},
                         {"from": "c", "to": "s", "label": "ACK"}]},
              {"id": f"{BUBBLE}#0", "ok": False, "error": "Parse error"}]
    (out / "checks.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in checks),
                                      encoding="utf-8")
    main(["audit-tier", str(out)])
    capsys.readouterr()
    assert main(["pool", str(out)]) == 0
    assert json.loads(capsys.readouterr().out) == {"review": 1, "redraw": 1}
    pool = [json.loads(x) for x in (out / "pool.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(p["ref"], p["pool_reason"]) for p in pool] == [(BUBBLE, "redraw"), (TCP, "review")]
    assert pool[1]["propositions"][0] == "客户端发送 SYN，进入 SYN_SENT。"

    outcomes = tmp_path / "outcomes.jsonl"
    outcomes.write_text(json.dumps({"ref": TCP, "status": "draw", "mermaid": NEW}, ensure_ascii=False) + "\n" +
                        json.dumps({"ref": BUBBLE, "status": "skip", "reason": "linear"}, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    before = {p: p.read_bytes() for p in (tmp_path / "v").rglob("*.md")}
    main(["writeback", str(tmp_path / "v"), str(out), str(outcomes)])
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == {"planned": 2}
    assert preview["by_pool_reason"] == {"redraw": {"skip:old_broken→remove": 1}, "review": {"draw→replace": 1}}
    assert before == {p: p.read_bytes() for p in (tmp_path / "v").rglob("*.md")}
    main(["writeback", str(tmp_path / "v"), str(out), str(outcomes), "--write"])
    written = json.loads(capsys.readouterr().out)
    assert (written["actions"], written["status"]) == ({"remove": 1, "replace": 1}, {"written": 2})
    manifest = [json.loads(x) for x in (out / "writeback_manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(m["pool_reason"], m["rule"], m["status"]) for m in manifest] == [
        ("redraw", "skip:old_broken", "written"), ("review", "draw", "written")]
    changed = {p for p in before if p.read_bytes() != before[p]}
    assert {p.relative_to(tmp_path / "v").as_posix() for p in changed} == {TCP, BUBBLE}
    assert NEW in (tmp_path / "v" / TCP).read_text(encoding="utf-8")
    replaced = [json.loads(x) for x in (out / "writeback_mermaid.jsonl").read_text(encoding="utf-8").splitlines()]
    assert replaced == [{"id": TCP, "text": NEW}]

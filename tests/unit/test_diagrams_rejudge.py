"""Re-judge pool selection and byte-precise write-back of model outcomes."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from secondbrain.diagrams import audit, prune, rejudge
from secondbrain.diagrams.__main__ import main

VAULT = Path(__file__).resolve().parents[1] / "fixtures" / "diagrams" / "vault"
TOOL = Path(__file__).resolve().parents[2] / "tools" / "mermaid_check" / "check.mjs"
TCP = "10_Knowledge/networking/TCP 三次握手.md"
BUBBLE = "10_Knowledge/algorithms/冒泡排序.md"
DRINKS = "10_Knowledge/beverages/茶与咖啡的提神方式.md"
NEW = "%% diagram v1 props=E,P1,P2 sha=0123abcd\nflowchart TB\n  n1[\"客户端\"] -->|\"SYN\"| n2[\"服务器\"]"

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


def _marked(props: str) -> str:
    return f"%% diagram v1 props={props} sha=0123abcd\nflowchart TB\n  n1[\"a\"] --> n2[\"b\"]"


def test_new_diagram_must_carry_e_to_replace_a_renderable_old_one():
    assert rejudge.strong_enough(_marked("E,P1,P2"), 3)
    assert not rejudge.strong_enough(_marked("P1,P2,P3"), 3)       # no E
    assert not rejudge.strong_enough(_marked("E,P2"), 3)           # one side proposition
    assert rejudge.strong_enough(_marked("E,P1"), 1)
    assert rejudge.strong_enough(_marked("P1,P2"), 2, has_essence=False)
    assert not rejudge.strong_enough("flowchart TB\n  a --> b", 1)  # no marker
    pool = rejudge.select_pool(TIERS)
    acts = {a["ref"]: (a["rule"], a["action"]) for a in rejudge.plan(pool, [
        {"ref": "a.md", "status": "draw", "mermaid": _marked("P1")},   # old renders: weak draw keeps it
        {"ref": "b.md", "status": "draw", "mermaid": _marked("P1")},   # old broken: any valid draw replaces
    ])}
    assert acts["a.md"] == ("draw:weak/skip:old_parses", "keep_old") and acts["b.md"] == ("draw", "replace")


def test_weak_draw_follows_the_skip_rules():
    """V3 K2: a weak draw writes nothing, so it is a skip for the old diagram, under a draw:weak rule name."""
    pool = rejudge.select_pool(TIERS)
    weak = [{"ref": r, "status": "draw", "mermaid": _marked("E,P1")} for r in ("a.md", "c.md", "g.md", "h.md")]
    acts = {a["ref"]: (a["rule"], a["action"]) for a in rejudge.plan(pool, weak)}
    assert acts == {"a.md": ("draw", "replace"),                       # one proposition: E and P1 are enough
                    "b.md": ("pending", "pending"),
                    "c.md": ("draw:weak/skip:delete_review", "remove"),
                    "g.md": ("draw:weak/skip:domain", "remove"),
                    "h.md": ("draw:weak/skip:old_parses", "keep_old")}
    keep = rejudge.WritebackPolicy(delete_review_on_skip="keep", remove_domains=("vocab",))
    acts = {a["ref"]: (a["rule"], a["action"]) for a in rejudge.plan(pool, weak, keep)}
    assert acts["c.md"] == ("draw:weak/skip:old_parses", "keep_old")
    assert acts["h.md"] == ("draw:weak/skip:domain", "remove")
    counts = rejudge.summary(rejudge.plan(pool, weak))
    assert sum(n for c in counts.values() for k, n in c.items() if k.startswith("draw:weak")) == 3


def test_plan_removes_only_on_restates_or_misleading_and_reads_any_audit_row():
    """V3 K6: the rule holds in plan() itself, not only in oldcheck.judge()."""
    pool = rejudge.select_pool(TIERS)
    skip = [{"ref": "a.md", "status": "skip"}, {"ref": "h.md", "status": "draw", "mermaid": _marked("E,P1")}]
    def acts(audits):
        return {a["ref"]: (a["rule"], a["action"]) for a in rejudge.plan(pool, skip, audits=audits)}
    assert acts([{"ref": "a.md", "verdict": "remove", "category": "useful"}])["a.md"] == ("audit:useful", "keep_old")
    assert acts([{"ref": "a.md", "verdict": "remove"}])["a.md"] == ("audit:unsure", "keep_old")
    assert acts([{"ref": "a.md", "status": "draw", "mermaid": NEW}])["a.md"] == ("audit:unsure", "keep_old")
    assert acts([{"ref": "a.md", "verdict": "remove", "category": "misleading"}])["a.md"] == ("audit:misleading", "remove")
    weak = acts([{"ref": "h.md", "verdict": "remove", "category": "restates"}])
    assert weak["h.md"] == ("draw:weak/audit:restates", "remove")  # the audit acts on weak draws too


def test_pool_carries_what_the_guard_and_the_packet_need():
    tiers = {**TIERS, "a.md": {**TIERS["a.md"], "has_essence": False, "n_props": 3}}
    rows = {p["ref"]: p for p in rejudge.select_pool(tiers)}
    assert (rows["a.md"]["n_props"], rows["a.md"]["has_essence"]) == (3, False)
    assert rows["h.md"]["has_essence"] is True
    cards = [{"ref": r, "title": r, "kind": "mechanism", "domain": None, "essence": "e", "propositions": ["p"],
              "related_titles": [], "blocks": ["flowchart TB\n  a --> b"]} for r in rows]
    by_ref = {c["ref"]: c for c in rejudge.pool_cards(list(rows.values()), cards)}
    assert by_ref["a.md"]["old_diagram"] == "renders" and by_ref["b.md"]["old_diagram"] == "broken"
    weak = [{"ref": "a.md", "status": "draw", "mermaid": _marked("P1,P2")}]  # no E needed: the card has none
    assert rejudge.plan([rows["a.md"]], weak)[0]["action"] == "replace"


def test_old_audit_removes_only_old_diagrams_that_would_stay():
    pool = rejudge.select_pool(TIERS)
    outcomes = [{"ref": "a.md", "status": "skip"}, {"ref": "b.md", "status": "skip"},
                {"ref": "h.md", "status": "draw", "mermaid": _marked("E,P1,P2")}]
    audits = [{"ref": "a.md", "verdict": "remove", "category": "restates", "reason": "chain"},
              {"ref": "b.md", "verdict": "keep", "category": "useful"},
              {"ref": "h.md", "verdict": "remove", "category": "misleading"}]
    acts = {a["ref"]: (a["rule"], a["action"]) for a in rejudge.plan(pool, outcomes, audits=audits)}
    assert acts["a.md"] == ("audit:restates", "remove")
    assert acts["b.md"] == ("skip:old_broken", "remove")   # broken: removed anyway, audit not consulted
    assert acts["h.md"] == ("draw", "replace")              # replaced: audit not consulted
    kept = rejudge.plan(pool, outcomes, audits=[{"ref": "a.md", "verdict": "keep", "category": "useful"}])
    assert next(a for a in kept if a["ref"] == "a.md")["rule"] == "audit:useful"


def test_audit_candidates_before_and_after_the_draw_step():
    pool = rejudge.select_pool(TIERS)
    assert [p["ref"] for p in rejudge.audit_candidates(pool)] == ["a.md", "h.md"]
    flipped = rejudge.WritebackPolicy(delete_review_on_skip="keep")
    assert [p["ref"] for p in rejudge.audit_candidates(pool, policy=flipped)] == ["a.md", "c.md", "h.md"]
    after = rejudge.audit_candidates(pool, [{"ref": "a.md", "status": "draw", "mermaid": _marked("E,P1")},
                                            {"ref": "h.md", "status": "skip"}])
    assert [p["ref"] for p in after] == ["h.md"]


def test_cli_holdout_list_with_old_audit(tmp_path, capsys):
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
    refs = tmp_path / "refs.txt"
    refs.write_text(f"# holdout\n{TCP}\n10_Knowledge/not/in-pool.md\n", encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    capsys.readouterr()
    main(["pool", str(out), "--refs", str(refs), "--out", str(run / "pool.jsonl")])
    assert json.loads(capsys.readouterr().out) == {"review": 1, "not_in_pool": 1}
    pool = [json.loads(x) for x in (run / "pool.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(p["ref"], p["old_diagram"]) for p in pool] == [(TCP, "renders")]
    assert pool[0]["old_mermaid"].startswith("flowchart LR")

    main(["packets", str(run / "pool.jsonl"), str(run / "draw")])
    draw_packet = json.loads((run / "draw" / "packet-001.json").read_text(encoding="utf-8"))
    assert draw_packet["input"]["cards"][0]["old_diagram"] == "renders"
    assert draw_packet["input"]["cards"][0]["old_mermaid"] == pool[0]["old_mermaid"]  # what a redraw would copy
    main(["old-audit-packets", str(run / "pool.jsonl"), str(run / "old")])
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["audited"] == 1
    old_packet = json.loads((run / "old" / "old-audit-001.json").read_text(encoding="utf-8"))
    assert old_packet["input"]["cards"][0]["old_mermaid"].startswith("flowchart LR")

    (run / "draw" / "packet-001.answer.json").write_text(json.dumps(
        {"results": [{"ref": TCP, "ir": {"decision": "skip", "reason": "short sequence"}}]}), encoding="utf-8")
    main(["apply", str(run / "pool.jsonl"), str(run / "draw" / "packet-001.answer.json"), "--out", str(run / "r0")])
    (run / "old" / "old-audit-001.answer.json").write_text(json.dumps(
        {"results": [{"ref": TCP, "verdict": "remove", "category": "restates", "reason": "three arrows in order"}]}),
        encoding="utf-8")
    main(["old-audit-apply", str(run / "old" / "old-audit-001.answer.json"), "--out", str(run / "old")])
    capsys.readouterr()
    main(["writeback", str(tmp_path / "v"), str(out), str(run / "r0" / "outcomes.jsonl"),
          "--old-audit", str(run / "old" / "old_audit_outcomes.jsonl"), "--refs", str(refs), "--write"])
    done = json.loads(capsys.readouterr().out)
    assert done["by_pool_reason"] == {"review": {"audit:restates→remove": 1}}
    assert "## 图" not in (tmp_path / "v" / TCP).read_text(encoding="utf-8")
    assert "## 图" in (tmp_path / "v" / BUBBLE).read_text(encoding="utf-8")  # outside the holdout list


def test_old_audit_packets_take_the_writeback_policy_flags(tmp_path, capsys):
    rows = rejudge.pool_cards(rejudge.select_pool(TIERS), [
        {"ref": r, "title": r, "kind": "mechanism", "domain": TIERS[r].get("domain"), "essence": "e",
         "propositions": ["p"], "related_titles": [], "blocks": ["flowchart TB\n  a --> b"]} for r in TIERS])
    pool = tmp_path / "pool.jsonl"
    pool.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def audited(*flags):
        capsys.readouterr()
        assert main(["old-audit-packets", str(pool), str(tmp_path / "old"), *flags]) == 0
        return json.loads(capsys.readouterr().out.strip().splitlines()[-1])["audited"]
    assert audited() == 2                                          # a.md, h.md
    assert audited("--delete-review-on-skip", "keep") == 3          # and c.md
    assert audited("--remove-domain", "vocab") == 1                 # h.md's domain is removed on skip



# Third pilot (J3): redraws of the old diagram, and wide kept diagrams ---------------------

OLD_EVOL = {"labels": ["简单指令", "复杂指令", "任务变体", "指令数据"], "edges": [[0, 1], [0, 2], [1, 3], [2, 3]]}
NEW_EVOL = ("%% diagram v1 props=E,P1,P2 sha=0123abcd\nflowchart TB\n  n1[\"原指令\"]\n  n2[\"带更多约束的复杂指令\"]\n"
            "  n3[\"相关任务变体\"]\n  n4[\"更高质量的指令数据\"]\n  n1 -->|\"深度进化\"| n2\n  n1 -->|\"广度进化\"| n3\n"
            "  n2 -->|\"合成\"| n4\n  n3 -->|\"合成\"| n4")


def test_same_structure_spots_a_redraw_of_the_old_flowchart():
    assert rejudge.same_structure(NEW_EVOL, OLD_EVOL)                     # pilot 3's Evol-Instruct pair
    chain = {**OLD_EVOL, "edges": [[0, 1], [1, 2], [2, 3]]}
    assert not rejudge.same_structure(NEW_EVOL, chain)                    # another shape
    other = {**OLD_EVOL, "labels": ["请求", "缓存", "数据库", "响应"]}
    assert not rejudge.same_structure(NEW_EVOL, other)                    # the same small shape, other words
    assert not rejudge.same_structure(NEW_EVOL, None)                     # no graph recorded for the old one
    seq = "%% diagram v1 props=E,P1,P2 sha=0123abcd\nsequenceDiagram\n  participant n1 as A"
    assert not rejudge.same_structure(seq, OLD_EVOL)


def test_a_redraw_is_not_written_and_follows_the_skip_rules():
    tiers = {r: {**t, "graph": OLD_EVOL} for r, t in TIERS.items()}
    pool = rejudge.select_pool(tiers)
    draws = [{"ref": r, "status": "draw", "mermaid": NEW_EVOL} for r in ("a.md", "b.md", "c.md", "h.md")]
    acts = {a["ref"]: (a["rule"], a["action"]) for a in rejudge.plan(pool, draws)}
    assert acts["a.md"] == ("draw:same_as_old/skip:old_parses", "keep_old")
    assert acts["b.md"] == ("draw", "replace")                            # the old one does not render
    assert acts["c.md"] == ("draw:same_as_old/skip:delete_review", "remove")
    audits = [{"ref": "h.md", "verdict": "remove", "category": "restates"}]
    assert {a["ref"]: a["rule"] for a in rejudge.plan(pool, draws, audits=audits)}["h.md"] == \
        "draw:same_as_old/audit:restates"
    assert [p["ref"] for p in rejudge.audit_candidates(pool, draws)] == ["a.md", "h.md"]


def test_kept_wide_lr_diagram_is_turned_upright(tmp_path):
    shutil.copytree(VAULT, tmp_path / "v")
    path = tmp_path / "v" / TCP
    before = path.read_text(encoding="utf-8")
    row = {"ref": TCP, "pool_reason": "review", "tier_why": "no_gain", "n_props": 3, "has_essence": True,
           "old_parses": True, "sha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
           "old_direction": "LR", "old_width": 850, "old_graph": None}
    skip = [{"ref": TCP, "status": "skip", "reason": "short sequence"}]
    [act] = rejudge.plan([row], skip, audits=[{"ref": TCP, "verdict": "keep", "category": "useful"}])
    assert (act["rule"], act["action"]) == ("audit:useful+tb", "direction_tb")
    assert rejudge.audit_candidates([row], skip) == [row]                 # still audited: it stays
    [done] = rejudge.apply(tmp_path / "v", [act], write=True)
    assert done["status"] == "written"
    assert path.read_text(encoding="utf-8") == before.replace("flowchart LR", "flowchart TB")
    narrow = rejudge.plan([{**row, "old_width": 650}], skip)[0]
    assert (narrow["rule"], narrow["action"]) == ("skip:old_parses", "keep_old")
    upright = rejudge.plan([{**row, "old_direction": "TB"}], skip)[0]
    assert (upright["rule"], upright["action"]) == ("skip:old_parses+wide", "keep_old")
    state = rejudge.plan([{**row, "old_type": "stateDiagram", "old_direction": None}], skip)[0]
    assert (state["rule"], state["action"]) == ("skip:old_parses", "keep_old")  # Obsidian scales it to fit
    removed = rejudge.plan([row], skip, audits=[{"ref": TCP, "verdict": "remove", "category": "restates"}])[0]
    assert removed["action"] == "remove"


def test_pool_carries_the_old_diagrams_direction_width_and_graph():
    tiers = {"a.md": {**TIERS["a.md"], "type": "flowchart-v2", "direction": "LR", "width": 900, "graph": OLD_EVOL}}
    [row] = rejudge.select_pool(tiers)
    assert (row["old_type"], row["old_direction"], row["old_width"], row["old_graph"]) == (
        "flowchart-v2", "LR", 900, OLD_EVOL)


def test_a_pool_without_old_graphs_is_refused(tmp_path, capsys):
    """V3's X4: a tiers.json from before the same-structure guard has no graphs; write-back must not run on it."""
    old = {r: {**t, "type": "flowchart-v2"} for r, t in TIERS.items()}         # rendering flowcharts, no graph
    pool = rejudge.select_pool(old)
    draws = [{"ref": "a.md", "status": "draw", "mermaid": NEW_EVOL}]
    with pytest.raises(ValueError, match="no old_graph .*rerun audit-tier"):
        rejudge.plan(pool, draws)
    with pytest.raises(ValueError, match="no old_graph"):
        rejudge.audit_candidates(pool, draws)                                # the after-draw audit packets too
    graphs = {r: {**t, "graph": OLD_EVOL} for r, t in old.items()}
    assert rejudge.plan(rejudge.select_pool(graphs), draws)                  # the same pool with graphs runs
    broken = rejudge.select_pool({"b.md": {**TIERS["b.md"]}})                 # a broken old diagram needs none
    assert rejudge.plan(broken, [{"ref": "b.md", "status": "skip", "reason": "x"}])

    shutil.copytree(VAULT, tmp_path / "v")
    out = tmp_path / "audit"
    main(["audit-extract", str(tmp_path / "v"), str(out)])
    sha = hashlib.sha256((tmp_path / "v" / TCP).read_bytes()).hexdigest()
    (out / "tiers.json").write_text(json.dumps({"cards": {TCP: {
        "tier": "review", "why": "no_gain", "kind": "mechanism", "n_props": 3, "sha256": sha, "ok": True,
        "type": "flowchart-v2", "direction": "TB", "width": 300}}}), encoding="utf-8")
    outcomes = tmp_path / "outcomes.jsonl"
    outcomes.write_text(json.dumps({"ref": TCP, "status": "draw", "mermaid": NEW}) + "\n", encoding="utf-8")
    capsys.readouterr()
    with pytest.raises(SystemExit, match="no old_graph"):
        main(["writeback", str(tmp_path / "v"), str(out), str(outcomes), "--write"])
    assert not (out / "writeback_manifest.jsonl").exists()


def _wide_lr(tmp_path) -> tuple[Path, dict]:
    """The TCP card with an LR diagram of five long nodes after one: still wide once turned TB."""
    shutil.copytree(VAULT, tmp_path / "v")
    path = tmp_path / "v" / TCP
    fan = "flowchart LR\n" + "".join(f"  a[起] --> b{i}[{'甲乙丙丁戊己庚辛'[i:] + '甲乙丙丁戊己庚辛'[:i]}]\n" for i in range(5))
    text = path.read_text(encoding="utf-8")
    old = text.split("```mermaid\n")[1].split("```")[0]
    path.write_text(text.replace(old, fan), encoding="utf-8")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, {"ref": TCP, "pool_reason": "review", "tier_why": "no_gain", "n_props": 3, "has_essence": True,
                  "old_parses": True, "sha256": sha, "old_direction": "LR", "old_width": 900, "old_graph": None}


def test_a_kept_diagram_still_too_wide_after_turning_says_so(tmp_path):
    """V3's X7: the rule name shows a kept old diagram that is still wider than 700 px once turned TB."""
    path, row = _wide_lr(tmp_path)
    skip = [{"ref": TCP, "status": "skip", "reason": "x"}]
    [act] = rejudge.plan([row], skip)
    assert (act["action"], act["rule"]) == ("direction_tb", "skip:old_parses+tb")
    [src] = rejudge.kept_sources(tmp_path / "v", [act])
    assert src["id"] == TCP and src["text"].startswith("flowchart TB\n") and "b4[" in src["text"]
    wide = rejudge.mark_still_wide([act], {TCP: {"ok": True, "type": "flowchart-v2", "width": 741}})
    assert wide[0]["rule"] == "skip:old_parses+tb+wide" and wide[0]["action"] == "direction_tb"
    assert rejudge.mark_still_wide([act], {TCP: {"ok": True, "type": "flowchart-v2", "width": 700}}) == [act]
    assert rejudge.mark_still_wide([act], {}) == [act]                      # not rendered: unchanged
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert rejudge.kept_sources(tmp_path / "v", [act]) == []               # changed since the audit: not rendered


def _checker():
    node = shutil.which("node")
    modules = Path(os.environ.get("MERMAID_CHECK_MODULES") or TOOL.parent)
    return (node, modules) if node and (modules / "node_modules" / "mermaid").is_dir() else None


@pytest.mark.skipif(_checker() is None, reason="tools/mermaid_check dependencies not installed")
def test_writeback_checker_marks_kept_diagrams_still_too_wide(tmp_path, capsys):
    _, modules = _checker()
    path, row = _wide_lr(tmp_path)
    out = tmp_path / "audit"
    main(["audit-extract", str(tmp_path / "v"), str(out)])
    (out / "tiers.json").write_text(json.dumps({"cards": {TCP: {
        "tier": "review", "why": "no_gain", "kind": "mechanism", "n_props": 3, "sha256": row["sha256"], "ok": True,
        "type": "flowchart-v2", "direction": "LR", "width": 900, "graph": {"labels": [], "edges": []}}}}),
        encoding="utf-8")
    outcomes = tmp_path / "outcomes.jsonl"
    outcomes.write_text(json.dumps({"ref": TCP, "status": "skip", "reason": "x"}) + "\n", encoding="utf-8")
    capsys.readouterr()
    main(["writeback", str(tmp_path / "v"), str(out), str(outcomes), "--checker", str(TOOL),
          "--checker-modules", str(modules)])
    plan = json.loads(capsys.readouterr().out)
    assert plan["by_pool_reason"] == {"review": {"skip:old_parses+tb+wide→direction_tb": 1}}
    [kept] = [json.loads(x) for x in (out / "kept_checks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert kept["width"] > 700 and kept["direction"] == "TB"


def test_the_redraw_guard_needs_both_the_words_and_the_degrees():
    """V3's surviving mutants: the word threshold of the default policy, and the degree comparison."""
    draws = [{"ref": "a.md", "status": "draw", "mermaid": NEW_EVOL}]

    def action(graph):
        pool = rejudge.select_pool({"a.md": {**TIERS["a.md"], "type": "flowchart-v2", "graph": graph}})
        return rejudge.plan(pool, draws)[0]["action"]

    assert action(OLD_EVOL) == "keep_old"                                    # same shape, same words: a redraw
    assert action({**OLD_EVOL, "labels": ["请求", "缓存", "数据库", "响应"]}) == "replace"  # same shape, other words
    fan = {**OLD_EVOL, "edges": [[0, 1], [0, 2], [0, 3], [1, 2]]}            # 4 nodes, 4 edges, other degrees
    assert action(fan) == "replace"


def test_kept_wide_diagrams_turn_from_701_px_and_from_rl():
    skip = [{"ref": "a.md", "status": "skip", "reason": "x"}]

    def rule(width, direction="LR"):
        pool = rejudge.select_pool({"a.md": {**TIERS["a.md"], "type": "flowchart-v2", "graph": OLD_EVOL,
                                             "direction": direction, "width": width}})
        a = rejudge.plan(pool, skip)[0]
        return a["action"], a["rule"]

    assert rule(700) == ("keep_old", "skip:old_parses")
    assert rule(701) == ("direction_tb", "skip:old_parses+tb")
    assert rule(744) == ("direction_tb", "skip:old_parses+tb")               # J3's id 14
    assert rule(850, "RL") == ("direction_tb", "skip:old_parses+tb")
    assert rule(850, "TB") == ("keep_old", "skip:old_parses+wide")


@pytest.mark.skipif(_checker() is None, reason="tools/mermaid_check dependencies not installed")
def test_prune_checker_names_kept_diagrams_still_too_wide(tmp_path, capsys):
    _, modules = _checker()
    path, row = _wide_lr(tmp_path)
    out = tmp_path / "audit"
    out.mkdir()
    (out / "tiers.json").write_text(json.dumps({"cards": {TCP: {
        "tier": "keep", "why": "branch", "kind": "mechanism", "n_props": 3, "sha256": row["sha256"], "ok": True,
        "type": "flowchart-v2", "direction": "LR", "width": 900}}}), encoding="utf-8")
    capsys.readouterr()
    main(["prune", str(tmp_path / "v"), str(out), "--checker", str(TOOL), "--checker-modules", str(modules)])
    summary = json.loads(capsys.readouterr().out)
    assert (summary["actions"], summary["still_wide"]) == ({"direction_tb": 1}, 1)
    [planned] = [json.loads(x) for x in (out / "prune_plan.jsonl").read_text(encoding="utf-8").splitlines()]
    assert planned["why"].startswith("width 900; still ") and planned["why"].endswith(" px wide")

"""Batch classification backlog: frozen plan, recoverable prepare, retryable run.

These drive the real prepare/materialize/run pipeline against temp vaults with
fake executors (no model processes, no network), and
cover: parent-plan interruption recovery, repeated resume, frozen-parameter
rejection, input drift, missing/candidate-only reviews, max-batches leaving
work unfinished, missing vs invalid classification separation, and the explicit
per-child recording of status/assigned/gap/domainreview/rejected.
"""
from __future__ import annotations

import json
import shutil

import pytest

from types import SimpleNamespace

from secondbrain import backlog, classification, jobs, runner, store
from secondbrain.executors import FakeExecutor
from secondbrain.cli import main as cli_main
from tests.helpers import (DOMAIN, EXISTING, PILLAR, TOPIC,
                               build_vault, submit_reviewed)

OTHER_DOMAIN = "transformer"


def quote_for(cid: str) -> str:
    return f"{cid} 的知识正文核心句。"


def card_text(cid: str, domain: str, *, pillar=None, topic=None) -> str:
    body = f"## 本质\n{quote_for(cid)}\n"
    return (f"---\nid: {cid}\ntitle: 卡{cid}\ndomain: {domain}\n"
            f"pillar: {pillar if pillar is not None else 'null'}\n"
            f"topic: {topic if topic is not None else 'null'}\n---\n{body}")


def build_batch_vault(tmp_path, *, attention=5, transformer=2, invalid=2):
    """Vault with classified EXISTING card plus missing/invalid classified cards."""
    root = build_vault(tmp_path)
    notes = [{"id": EXISTING, "path": f"10_Knowledge/{DOMAIN}/{EXISTING}.md",
              "title": "已有卡", "domain": DOMAIN}]
    specs = []
    for i in range(1, attention + 1):
        specs.append((f"card_miss_a{i}", DOMAIN))
    for i in range(1, transformer + 1):
        specs.append((f"card_miss_t{i}", OTHER_DOMAIN))
    for i in range(1, invalid + 1):
        specs.append((f"card_bad{i}", DOMAIN))
    for cid, domain in specs:
        (root / "10_Knowledge" / domain).mkdir(parents=True, exist_ok=True)
        text = card_text(cid, domain)
        if cid.startswith("card_bad"):
            text = text.replace("pillar: null", "pillar: 错误支柱").replace("topic: null", "topic: 错误主题")
        (root / "10_Knowledge" / domain / f"{cid}.md").write_text(text, encoding="utf-8")
        notes.append({"id": cid, "path": f"10_Knowledge/{domain}/{cid}.md",
                      "title": f"卡{cid}", "domain": domain})
    (root / "_data" / "notes_registry.json").write_text(
        json.dumps({"notes": notes}, ensure_ascii=False), encoding="utf-8")
    return root


def plan(root, job_id):
    return jobs.read_input(root, job_id)["children"]


def child_ids(root, child):
    packet = jobs.read_input(root, child["job_id"])
    return [c["id"] for c in packet["cards"]]


def all_missing_ids(root, job_id):
    out = []
    for child in plan(root, job_id):
        out.extend(child_ids(root, child))
    return sorted(out)


#: Executors used by ``backlog.run(**EX)``; install_dispatch swaps in recording fakes.
EX = {"producer": FakeExecutor("gpt-test", outputs={}), "reviewer": FakeExecutor("claude-test", outputs={})}


def install_dispatch(monkeypatch, root, *, disposition="assigned", fail_review_first=False, reject_once=False):
    """Fake producer/reviewer executors: real run records and submit, no model."""
    calls = []
    ctl = SimpleNamespace(fail_review_first=fail_review_first)

    def produce(task):
        calls.append({"role": "producer", "job_id": task.job_id})
        packet = jobs.read_input(root, task.job_id)
        rows = []
        for c in packet["cards"]:
            row = {"id": c["id"], "input_sha256": c["sha256"],
                   "evidence_quote": quote_for(c["id"]), "reason": "该卡覆盖此主题"}
            if disposition == "assigned":
                row.update(disposition="assigned", pillar=PILLAR, topic=TOPIC)
            else:
                row.update(disposition="taxonomy_gap")
            rows.append(row)
        return {"items": rows}

    def review(task):
        calls.append({"role": "reviewer", "job_id": task.job_id})
        packet = jobs.read_input(root, task.job_id)
        if ctl.fail_review_first and sum(1 for c in calls if c["role"] == "reviewer") == 1:
            raise ValueError("模拟复核阶段崩溃")
        first_review = sum(x["role"] == "reviewer" for x in calls) == 1
        return {"items": [{"id": c["id"], "decision": "revise" if reject_once and first_review and c["id"] == "card_miss_a1" else "accept",
                           "reason": "独立核对原文与候选"} for c in packet["cards"]]}

    monkeypatch.setitem(EX, "producer", FakeExecutor("gpt-test", responder=produce))
    monkeypatch.setitem(EX, "reviewer", FakeExecutor("claude-test", responder=review))
    return calls, ctl


# ---------------------------------------------------------------------------
# prepare: frozen plan, materialization, interruption recovery
# ---------------------------------------------------------------------------
def test_prepare_frozen_plan_then_materializes_children(tmp_path):
    root = build_batch_vault(tmp_path)
    state = backlog.prepare(root, job_id="b1", batch_size=2)
    assert state["kind"] == "classification-batch"
    assert state["child_count"] == 4          # attention 5/2 -> 3, transformer 2/2 -> 1
    assert state["input_count"] == 7
    assert state["materialized_children"] == 4
    children = state["children"]
    assert [c["domain"] for c in children] == [DOMAIN, DOMAIN, DOMAIN, OTHER_DOMAIN]
    assert [c["ids"] for c in children] == [
        ["card_miss_a1", "card_miss_a2"], ["card_miss_a3", "card_miss_a4"],
        ["card_miss_a5"], ["card_miss_t1", "card_miss_t2"]]
    for child in children:
        assert jobs.load(root, child["job_id"])["kind"] == "classification"
        assert child_ids(root, child) == child["ids"]


def test_reprepare_same_job_is_idempotent_and_keeps_plan(tmp_path):
    root = build_batch_vault(tmp_path)
    first = backlog.prepare(root, job_id="b2", batch_size=2)
    again = backlog.prepare(root, job_id="b2", batch_size=2)
    assert again["resumed"] is True
    assert again["materialized_children"] == 0
    assert again["child_count"] == first["child_count"]
    assert [c["job_id"] for c in again["children"]] == [c["job_id"] for c in first["children"]]
    classification_jobs = [e for e in jobs.list_jobs(root) if e["kind"] == "classification"]
    assert len(classification_jobs) == 4


def test_prepare_interruption_recoverable_without_orphaned_subtasks(tmp_path, monkeypatch):
    root = build_batch_vault(tmp_path)
    real = classification.prepare
    counter = {"n": 0}

    def flaky(root_, *, ids, job_id, **kw):
        if job_id.startswith("b3-"):
            counter["n"] += 1
            if counter["n"] == 3:
                raise RuntimeError("模拟prepare中断")
        return real(root_, ids=ids, job_id=job_id, **kw)

    monkeypatch.setattr(classification, "prepare", flaky)
    with pytest.raises(RuntimeError):
        backlog.prepare(root, job_id="b3", batch_size=2)
    # Parent plan is frozen; two children materialized before the crash.
    parent = jobs.load(root, "b3")
    assert parent["kind"] == "classification-batch" and parent["child_count"] == 4
    existing = sorted(e["job_id"] for e in jobs.list_jobs(root) if e["kind"] == "classification")
    assert existing == ["b3-001", "b3-002"]
    # Re-prepare with the same frozen parameters completes the plan: no card lost.
    monkeypatch.setattr(classification, "prepare", real)
    resumed = backlog.prepare(root, job_id="b3", batch_size=2)
    assert resumed["resumed"] is True
    assert resumed["materialized_children"] == 2
    assert all_missing_ids(root, "b3") == sorted(
        cid for child in plan(root, "b3") for cid in child["ids"])


def test_same_job_changed_params_explicitly_rejected(tmp_path):
    root = build_batch_vault(tmp_path)
    backlog.prepare(root, job_id="b4", batch_size=2)
    with pytest.raises(ValueError, match="已冻结"):
        backlog.prepare(root, job_id="b4", domain=DOMAIN)
    with pytest.raises(ValueError, match="已冻结"):
        backlog.prepare(root, job_id="b4", batch_size=3)
    with pytest.raises(ValueError, match="已冻结"):
        backlog.prepare(root, job_id="b4", repair_invalid=True)
    with pytest.raises(ValueError, match="已冻结"):
        backlog.prepare(root, job_id="b4", include_pending=True)
    # Exact same parameters are fine and resume the plan.
    assert backlog.prepare(root, job_id="b4", batch_size=2)["resumed"] is True


def test_domain_filter_and_missing_vs_invalid_separated(tmp_path):
    root = build_batch_vault(tmp_path)
    both = backlog.prepare(root, job_id="both", batch_size=50)
    assert both["input_count"] == 7
    both_ids = {cid for child in both["children"] for cid in child["ids"]}
    assert not both_ids & {f"card_bad{i}" for i in range(1, 3)}
    # Independent vaults so earlier plans do not count as prior work.
    miss_root = build_batch_vault(tmp_path / "m")
    missing = backlog.prepare(miss_root, job_id="miss", domain=DOMAIN, batch_size=100)
    assert missing["input_count"] == 5
    assert [cid for child in missing["children"] for cid in child["ids"]] == \
        [f"card_miss_a{i}" for i in range(1, 6)]
    inv_root = build_batch_vault(tmp_path / "i")
    invalid = backlog.prepare(inv_root, job_id="inv", repair_invalid=True, batch_size=100)
    assert invalid["input_count"] == 2
    assert sorted(cid for child in invalid["children"] for cid in child["ids"]) == \
        sorted(f"card_bad{i}" for i in range(1, 3))
    assert not set(plan(miss_root, "miss")[0]["ids"]) & set(plan(inv_root, "inv")[0]["ids"])


def test_historical_prepared_work_is_not_repeated(tmp_path):
    root = build_batch_vault(tmp_path)
    first = backlog.prepare(root, job_id="p1", batch_size=2)
    assert first["child_count"] == 4
    second = backlog.prepare(root, job_id="p2", batch_size=2)
    assert second["status"] == "unchanged"
    assert set(second["skipped_ids"]) == {
        f"card_miss_a{i}" for i in range(1, 6)} | {f"card_miss_t{i}" for i in range(1, 3)}
    assert len(second["prior_jobs"]) == 4
    # include_pending re-opens the already prepared cards under a new job.
    again = backlog.prepare(root, job_id="p3", batch_size=2, include_pending=True)
    assert again["input_count"] == 7
    assert again["prior_jobs"] == []  # include_pending skips the history filter entirely
    classification_jobs = sorted(e["job_id"] for e in jobs.list_jobs(root)
                                 if e["kind"] == "classification")
    assert classification_jobs[:4] == [f"p1-00{i}" for i in range(1, 5)]
    assert set(again["skipped_ids"]) == set()


def test_prepare_validation_errors(tmp_path):
    root = build_batch_vault(tmp_path)
    with pytest.raises(ValueError, match="1–100"):
        backlog.prepare(root, job_id="v1", batch_size=0)
    with pytest.raises(ValueError, match="未知领域"):
        backlog.prepare(root, job_id="v2", domain="nope")
    with pytest.raises(ValueError, match="任务ID过长"):
        backlog.prepare(root, job_id="v" * 120)


# ---------------------------------------------------------------------------
# run: reviewed binding, no re-runs, retryability, limits
# ---------------------------------------------------------------------------
def test_run_reviews_applies_and_records_per_child(tmp_path, monkeypatch):
    root = build_batch_vault(tmp_path)
    backlog.prepare(root, job_id="full", batch_size=2)
    calls, _ = install_dispatch(monkeypatch, root)
    result = backlog.run(root, "full", **EX, apply=True)
    assert result["status"] == "processed"
    assert result["reviewed_cards"] == 7
    assert result["decision_counts"] == {"assigned": 7}
    assert result["unresolved_count"] == 0
    assert len(result["outcomes"]) == 4
    for child in plan(root, "full"):
        outcome = next(o for o in result["outcomes"] if o["job_id"] == child["job_id"])
        assert outcome["reviewed"] is True
        assert outcome["assigned"] == len(child["ids"])
        assert outcome["gap"] == 0 and outcome["domainreview"] == 0 and outcome["rejected"] == 0
        assert outcome["status"] == "applied"
    text = (root / f"10_Knowledge/{DOMAIN}/card_miss_a1.md").read_text()
    assert f"pillar: {PILLAR}" in text and f"topic: {TOPIC}" in text
    assert len(calls) == 8  # producer + reviewer for each of the 4 children


def test_repeated_resume_does_not_rerun_completed_children(tmp_path, monkeypatch):
    root = build_batch_vault(tmp_path)
    backlog.prepare(root, job_id="rep", batch_size=2)
    calls, _ = install_dispatch(monkeypatch, root)
    first = backlog.run(root, "rep", **EX, apply=True)
    assert first["status"] == "processed"
    before = len(calls)
    second = backlog.run(root, "rep", **EX, apply=True)
    assert second["status"] == "processed"
    assert len(calls) == before  # no child was dispatched again
    assert second["outcomes"] == first["outcomes"]


def test_capped_apply_is_partial_and_corruption_outside_cap_is_visible(tmp_path, monkeypatch):
    root = build_batch_vault(tmp_path, attention=6, transformer=0, invalid=0)
    backlog.prepare(root, job_id='capped', batch_size=2)
    calls, _ = install_dispatch(monkeypatch, root)
    backlog.run(root, 'capped', **EX, apply=False)
    before = len(calls)
    result = backlog.run(root, 'capped', **EX, apply=True, max_batches=1)
    assert result['status'] == 'partial'
    assert sum(not o['accepted_assignments_applied'] for o in result['outcomes']) == 2
    last = jobs.load(root, 'capped-003')
    path = store.state_root(root) / last['review']['path']
    path.write_bytes(b'{}')
    result = backlog.run(root, 'capped', **EX, apply=True, max_batches=1)
    corrupt = next(o for o in result['outcomes'] if o['job_id'] == 'capped-003')
    assert result['status'] == 'partial' and corrupt['error'] and not corrupt['reviewed']
    assert len(calls) == before


def test_max_batches_leaves_rest_incomplete_then_resume_completes(tmp_path, monkeypatch):
    root = build_batch_vault(tmp_path)
    backlog.prepare(root, job_id="mb", batch_size=2)
    calls, _ = install_dispatch(monkeypatch, root)
    result = backlog.run(root, "mb", **EX, apply=True, max_batches=2)
    assert result["status"] == "partial"
    done = {o["job_id"] for o in result["outcomes"] if o.get("decisions")}
    assert len(done) == 2
    assert sum(not x["reviewed"] for x in result["outcomes"]) == 2
    # The two completed children never run again during the finishing pass.
    finished = backlog.run(root, "mb", **EX, apply=True)
    assert finished["status"] == "processed"
    assert len(finished["outcomes"]) == 4
    for job_id in done:
        assert [c for c in calls if c["job_id"] == job_id] == \
            [{"role": "producer", "job_id": job_id}, {"role": "reviewer", "job_id": job_id}]


def test_candidate_only_child_is_error_not_reviewed_then_recovers(tmp_path, monkeypatch):
    root = build_batch_vault(tmp_path)
    backlog.prepare(root, job_id="cr", batch_size=2)
    calls, dispatch = install_dispatch(monkeypatch, root, fail_review_first=True)
    result = backlog.run(root, "cr", **EX, apply=True)
    assert result["status"] == "partial"
    errored = [o for o in result["outcomes"] if o.get("error")]
    assert len(errored) == 1
    assert errored[0]["reviewed"] is False
    assert "decisions" not in errored[0]
    crashed = errored[0]["job_id"]
    child_state = jobs.load(root, crashed)
    assert child_state.get("candidate") and not child_state.get("review")
    # Resume: reviewer-only round for the crashed child; the producer is not re-run.
    dispatch.fail_review_first = False
    final = backlog.run(root, "cr", **EX, apply=True)
    assert final["status"] == "processed"
    role_counts = {"producer": 0, "reviewer": 0}
    for c in calls:
        if c["job_id"] == crashed:
            role_counts[c["role"]] += 1
    # One failed review attempt + one successful retry; never a second production.
    assert role_counts == {"producer": 1, "reviewer": 2}


def test_tampered_child_input_kept_retryable_without_model_run(tmp_path, monkeypatch):
    root = build_batch_vault(tmp_path)
    backlog.prepare(root, job_id="ic", batch_size=2)
    target = plan(root, "ic")[1]
    state_path = store.state_root(root) / jobs.location(target["job_id"], "input.json")
    original = state_path.read_bytes()
    calls, _ = install_dispatch(monkeypatch, root)
    state_path.write_text("{}")
    result = backlog.run(root, "ic", **EX, apply=True)
    assert result["status"] == "partial"
    errored = next(o for o in result["outcomes"] if o["job_id"] == target["job_id"])
    assert "任务输入被修改" in errored["error"]
    assert errored["reviewed"] is False
    assert not any(c["job_id"] == target["job_id"] for c in calls)
    # Restoring the exact bytes lets the same plan resume without re-preparing.
    state_path.write_bytes(original)
    final = backlog.run(root, "ic", **EX, apply=True)
    assert final["status"] == "processed"
    outcome = next(o for o in final["outcomes"] if o["job_id"] == target["job_id"])
    assert outcome["reviewed"] is True


def test_child_bound_to_wrong_input_is_rejected_from_plan(tmp_path, monkeypatch):
    root = build_batch_vault(tmp_path)
    backlog.prepare(root, job_id="pm", batch_size=2)
    target = plan(root, "pm")[0]
    shutil.rmtree(store.state_root(root) / "jobs" / target["job_id"])
    classification.prepare(root, ids=["card_miss_t1"], job_id=target["job_id"])
    calls, _ = install_dispatch(monkeypatch, root)
    result = backlog.run(root, "pm", **EX, apply=True)
    assert result["status"] == "partial"
    errored = next(o for o in result["outcomes"] if o["job_id"] == target["job_id"])
    assert "输入与冻结计划不一致" in errored["error"]
    assert not any(c["job_id"] == target["job_id"] for c in calls)
    assert sum(1 for o in result["outcomes"] if o.get("reviewed")) == 3


def test_run_validation_limits(tmp_path):
    root = build_batch_vault(tmp_path)
    classification.prepare(root, ids=["card_miss_a1"], job_id="classy")
    backlog.prepare(root, job_id="lim", batch_size=100)
    with pytest.raises(ValueError, match="1–3"):
        backlog.run(root, "lim", **EX, parallel=0)
    with pytest.raises(ValueError, match="1–3"):
        backlog.run(root, "lim", **EX, parallel=4)
    with pytest.raises(ValueError, match="max_batches"):
        backlog.run(root, "lim", **EX, max_batches=0)
    with pytest.raises(ValueError, match="不是分类批次计划"):
        backlog.run(root, "classy", **EX)


def test_parallel_run_records_failures_retryably_without_rerun(tmp_path, monkeypatch):
    root = build_batch_vault(tmp_path, attention=6)
    backlog.prepare(root, job_id="par", batch_size=2)
    children = plan(root, "par")
    assert len(children) == 4
    # One child fails deterministically; the other three must be completed in the
    # parallel pass and never dispatched again. Worker state writes are
    # serialized in the stub so the only failure is the injected one; the
    # coordinator's own parent updates still ride the real non-blocking lock.
    import threading
    worker_serial = threading.Lock()
    calls = []
    injected = {"job_id": children[1]["job_id"], "done": False}
    def stub_run_job(root_, job_id_, *, producer, reviewer, apply=True):
        calls.append(job_id_)
        with worker_serial:
            if job_id_ == injected["job_id"] and not injected["done"]:
                injected["done"] = True
                raise ValueError("模拟并发子任务失败")
            packet = jobs.read_input(root_, job_id_)
            rows = [{"id": c["id"], "input_sha256": c["sha256"], "disposition": "assigned",
                     "pillar": PILLAR, "topic": TOPIC, "evidence_quote": quote_for(c["id"]),
                     "reason": "该卡覆盖此主题"} for c in packet["cards"]]
            review = {"items": [{"id": c["id"], "decision": "accept", "reason": "独立核对原文"}
                                for c in packet["cards"]]}
            submit_reviewed(root_, job_id_, {"items": rows}, review)
        return jobs.load(root_, job_id_)

    monkeypatch.setattr(runner, "run_job", stub_run_job)
    result = backlog.run(root, "par", **EX, apply=False, parallel=2)
    assert result["status"] == "partial"
    errored = [o for o in result["outcomes"] if o.get("error")]
    assert [(o["job_id"], o["reviewed"]) for o in errored] == [(injected["job_id"], False)]
    done_first = {o["job_id"] for o in result["outcomes"] if o.get("decisions")}
    assert done_first == {c["job_id"] for c in children if c["job_id"] != injected["job_id"]}
    snapshot_len = len(calls)
    final = backlog.run(root, "par", **EX, apply=False, parallel=1)
    assert final["status"] == "reviewed"
    assert all(o.get("reviewed") for o in final["outcomes"])
    assert calls[snapshot_len:] == [injected["job_id"]]
    # Children fully reviewed in the parallel pass were never dispatched again.
    assert not any(j in done_first for j in calls[snapshot_len:])


# ---------------------------------------------------------------------------
# summarize: disposition/rejection recording, reviewed discipline
# ---------------------------------------------------------------------------
def _submit_rows(root, job_id, rows):
    from tests.helpers import submit_as
    submit_as(root, job_id, {"items": rows}, "producer")  # no review yet


def test_summarize_records_status_assigned_gap_domainreview_rejected(tmp_path):
    root = build_batch_vault(tmp_path)
    backlog.prepare(root, job_id="mix", batch_size=100)
    children = plan(root, "mix")
    assert len(children) == 2
    child = children[0]
    packet = jobs.read_input(root, child["job_id"])
    rows = []
    for c in packet["cards"]:
        row = {"id": c["id"], "input_sha256": c["sha256"],
               "evidence_quote": quote_for(c["id"]), "reason": "原文覆盖"}
        if c["id"] == "card_miss_a3":
            row.update(disposition="taxonomy_gap")
        elif c["id"] == "card_miss_a4":
            row.update(disposition="needs_domain_review")
        else:
            row.update(disposition="assigned", pillar=PILLAR, topic=TOPIC)
        rows.append(row)
    review = {"items": [{"id": r["id"], "decision": "accept", "reason": "核过"}
                        if r["id"] != "card_miss_a4" else
                        {"id": r["id"], "decision": "revise", "reason": "该卡更接近他域"}
                        for r in rows]}
    submit_reviewed(root, child["job_id"], {"items": rows}, review)
    outcome = backlog.summarize(root, child)
    assert outcome["reviewed"] is True
    assert outcome["status"] == "review_submitted"
    assert outcome["assigned"] == 3 and outcome["gap"] == 1
    assert outcome["domainreview"] == 0 and outcome["rejected"] == 1
    assert outcome["decisions"] == {"assigned": 3, "taxonomy_gap": 1, "rejected": 1}
    # A child with only a candidate is never counted as reviewed.
    candidate_only = children[1]
    hashes = {c["id"]: c["sha256"] for c in jobs.read_input(root, candidate_only["job_id"])["cards"]}
    _submit_rows(root, candidate_only["job_id"],
                 [{"id": cid, "input_sha256": hashes[cid], "disposition": "assigned",
                   "pillar": PILLAR, "topic": TOPIC,
                   "evidence_quote": quote_for(cid), "reason": "r"}
                  for cid in ("card_miss_t1", "card_miss_t2")])
    partial = backlog.summarize(root, candidate_only)
    assert partial["reviewed"] is False
    assert "decisions" not in partial


def test_run_gap_batch_records_unresolved_not_cleared(tmp_path, monkeypatch):
    root = build_batch_vault(tmp_path)
    backlog.prepare(root, job_id="gap", batch_size=100)
    calls, _ = install_dispatch(monkeypatch, root, disposition="taxonomy_gap")
    result = backlog.run(root, "gap", **EX, apply=False)
    assert result["status"] == "reviewed"          # fully reviewed, not fabricated
    assert result["decision_counts"] == {"taxonomy_gap": 7}
    assert result["unresolved_count"] == 7
    assert result["reviewed_cards"] == 7           # every gap disposition was accepted after review
    assert all(o["gap"] == o["input_count"] and o["rejected"] == 0
               for o in result["outcomes"])


# ---------------------------------------------------------------------------
# CLI wiring: backlog command and resume batch routing
# ---------------------------------------------------------------------------
def test_cli_backlog_prepare_only_prints_frozen_plan(tmp_path, capsys):
    root = build_batch_vault(tmp_path)
    rc = cli_main(["--vault", str(root), "backlog", "--batch-size", "2", "--prepare-only"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kind"] == "classification-batch"
    assert out["child_count"] == 4 and out["input_count"] == 7
    assert len(out["children"]) == 4


def test_cli_resume_routes_batch_jobs_to_backlog_run(tmp_path, monkeypatch, capsys):
    root = build_batch_vault(tmp_path)
    assert cli_main(["--vault", str(root), "backlog", "--batch-size", "2", "--prepare-only"]) == 0
    job_id = json.loads(capsys.readouterr().out)["job_id"]
    captured = {}

    def fake_run(root_, job_id_, *, producer, reviewer, parallel, apply, max_batches):
        captured.update(job_id=job_id_, parallel=parallel, apply=apply, max_batches=max_batches)
        return {"job_id": job_id_, "status": "reviewed"}

    monkeypatch.setattr(backlog, "run", fake_run)
    monkeypatch.setattr("secondbrain.cli.make_executors", lambda args, root: (EX["producer"], EX["reviewer"]))
    rc = cli_main(["--vault", str(root), "resume", "--job", job_id,
                   "--proposal-only", "--parallel", "2", "--max-batches", "1"])
    assert rc == 0
    assert captured == {"job_id": job_id, "parallel": 2, "apply": False, "max_batches": 1}


def test_cli_backlog_job_route_runs_remaining(tmp_path, monkeypatch, capsys):
    root = build_batch_vault(tmp_path)
    assert cli_main(["--vault", str(root), "backlog", "--batch-size", "2", "--prepare-only"]) == 0
    job_id = json.loads(capsys.readouterr().out)["job_id"]
    captured = {}

    def fake_run(root_, job_id_, **kw):
        captured.update(kw)
        return {"job_id": job_id_, "status": "processed"}

    monkeypatch.setattr(backlog, "run", fake_run)
    monkeypatch.setattr("secondbrain.cli.make_executors", lambda args, root: (EX["producer"], EX["reviewer"]))
    rc = cli_main(["--vault", str(root), "backlog", "--job", job_id,
                   "--batch-size", "2", "--max-batches", "1"])
    assert rc == 0
    assert captured["apply"] is True and captured["max_batches"] == 1 and captured["parallel"] == 1


def test_cli_backlog_param_change_rejected_with_error(tmp_path, capsys):
    root = build_batch_vault(tmp_path)
    assert cli_main(["--vault", str(root), "backlog", "--batch-size", "2", "--prepare-only"]) == 0
    job_id = json.loads(capsys.readouterr().out)["job_id"]
    rc = cli_main(["--vault", str(root), "backlog", "--job", job_id,
                   "--batch-size", "3", "--prepare-only"])
    assert rc == 2
    err = json.loads(capsys.readouterr().err)
    assert "已冻结" in err["error"]


def test_cli_resume_non_batch_routes_to_runner(tmp_path, monkeypatch, capsys):
    root = build_vault(tmp_path)
    path = root / f"10_Knowledge/{DOMAIN}/{EXISTING}.md"
    path.write_text(path.read_text(encoding="utf-8").replace(
        f"pillar: {PILLAR}", "pillar: null").replace(f"topic: {TOPIC}", "topic: null"), encoding="utf-8")
    classification.prepare(root, ids=[EXISTING], job_id="cls")
    captured = {}

    def fake_run_job(root_, job_id, **kw):
        captured.update(job_id=job_id, apply=kw.get("apply"))
        return jobs.load(root_, job_id)

    monkeypatch.setattr(runner, "run_job", fake_run_job)
    monkeypatch.setattr("secondbrain.cli.make_executors", lambda args, root: (EX["producer"], EX["reviewer"]))
    rc = cli_main(["--vault", str(root), "resume", "--job", "cls"])
    assert rc == 0
    assert captured == {"job_id": "cls", "apply": True}

def test_batch_counts_repaired_rows_and_applies_them_without_repeated_models(tmp_path, monkeypatch):
    root = build_batch_vault(tmp_path)
    backlog.prepare(root, job_id='repair-batch', batch_size=100)
    calls, _ = install_dispatch(monkeypatch, root, reject_once=True)
    reviewed = backlog.run(root, 'repair-batch', **EX, apply=False)
    assert reviewed['decision_counts'] == {'assigned': 7}
    repairs = [c for c in calls if c['job_id'].startswith('repair-') and c['job_id'] != 'repair-batch-001' and c['job_id'] != 'repair-batch-002']
    assert len(repairs) == 2
    assert jobs.read_input(root, repairs[0]['job_id'])['cards'][0]['id'] == 'card_miss_a1'
    assert len(jobs.read_input(root, repairs[0]['job_id'])['cards']) == 1
    count = len(calls)
    result = backlog.run(root, 'repair-batch', **EX, apply=True)
    assert result['status'] == 'processed' and len(calls) == count
    from secondbrain.vault import read_snapshot
    assert all(c.fm.get('pillar') and c.fm.get('topic') for c in read_snapshot(root).cards.values())
    assert all(x['accepted_assignments_applied'] for x in result['outcomes'])


def test_duplicate_batch_coordinator_is_rejected(tmp_path):
    root = build_batch_vault(tmp_path)
    backlog.prepare(root, job_id='locked-batch')
    import threading
    from secondbrain.locks import job_lock
    held, release = threading.Event(), threading.Event()

    def other_coordinator():
        with job_lock(root, 'locked-batch'):
            held.set()
            release.wait(10)
    t = threading.Thread(target=other_coordinator)
    t.start()
    held.wait(10)
    try:
        with pytest.raises(store.Conflict, match='already being driven'):
            backlog.run(root, 'locked-batch', **EX)
    finally:
        release.set()
        t.join()


def test_interrupted_preparation_cannot_materialize_changed_card_versions(tmp_path, monkeypatch):
    root = build_batch_vault(tmp_path)
    original = classification.prepare
    def stop(*a, **kw):
        raise RuntimeError('stop before child')
    monkeypatch.setattr(classification, 'prepare', stop)
    with pytest.raises(RuntimeError): backlog.prepare(root, job_id='drift-plan')
    monkeypatch.setattr(classification, 'prepare', original)
    target = root / '10_Knowledge' / DOMAIN / 'card_miss_a1.md'
    target.write_text(target.read_text() + '\nexternal change')
    resumed = backlog.prepare(root, job_id='drift-plan')
    assert resumed['materialized_children'] == 0 and resumed['materialize_errors']

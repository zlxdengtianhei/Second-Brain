"""Projection jobs: daily apply, frozen resume and write limits.

Acceptance A: ``daily --apply`` (real CLI in a subprocess, explicit ``--vault``)
records a real ``projection`` job with traceable frozen input.

Acceptance B: an interrupted projection resumes the original frozen date/plan
without regenerating on another day and without duplicate writes; and the
projection job interface refuses card/registry/catalog paths and non-projection
kinds, so the deterministic path can never write knowledge cards or bypass the
semantic review gate.

Everything is isolated in temp vaults; processing state is redirected by the
autouse conftest fixture and every CLI invocation passes ``--vault``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from secondbrain import jobs, store
from secondbrain.projections import generate
from tests.helpers import build_vault

VENV_PYTHON = sys.executable


def assert_projection_paths(paths):
    """Deterministic projections may only touch the browse/entry surface."""
    standalone = {"HOME.md", "使用指南.md", "系统总览.md"}
    for rel in paths:
        parts = rel.split("/")
        if rel in standalone:
            continue
        if parts[0] == "20_MOCs":
            assert len(parts) == 2 and parts[1] != "_pillars" and rel.endswith(".md")
            continue
        if parts[0] == "50_Reviews":
            assert len(parts) == 2 and rel.endswith(".md")
            continue
        if parts[:2] == ["_data", "projections"]:
            assert len(parts) == 3 and rel.endswith(".json")
            continue
        pytest.fail(f"projection wrote a disallowed path: {rel}")


def test_zero_change_daily_completes_without_writes(tmp_path):
    root = build_vault(tmp_path)
    packet = {
        "kind": jobs.PROJECTION_KIND,
        "projection": "daily",
        "date": "2026-09-07",
        "options": {"reviews": True, "entries": True},
    }
    jobs.create(root, "daily-empty", jobs.PROJECTION_KIND, packet)
    result = jobs.freeze_projection(root, "daily-empty", [], result={"status": "applied"})
    assert result["status"] == "applied"
    assert result["applied_paths"] == []


def test_daily_apply_through_real_cli(tmp_path):
    root = build_vault(tmp_path)
    (root / "使用指南.md").write_text(
        "Guide\n<!-- GENERATED:BEGIN kb-stats -->old<!-- GENERATED:END kb-stats -->\n",
        encoding="utf-8")

    # Real CLI runs the daily projection against an explicit --vault only.
    # Preview is zero-knowledge: no job record, no vault writes.
    preview = subprocess.run([VENV_PYTHON, "-m", "secondbrain", "--vault", str(root), "daily"],
                             capture_output=True, text=True, cwd=root)
    assert preview.returncode == 0, preview.stderr
    planned = json.loads(preview.stdout)
    assert planned["status"] == "planned"
    assert not (root / "HOME.md").exists()
    assert jobs.list_jobs(root) == []

    ran = subprocess.run([VENV_PYTHON, "-m", "secondbrain", "--vault", str(root),
                          "daily", "--apply"], capture_output=True, text=True, cwd=root)
    assert ran.returncode == 0, ran.stderr
    applied = json.loads(ran.stdout)
    assert applied["status"] == "applied"
    assert applied["kind"] == "projection"
    assert applied["date"] == date.today().isoformat()
    job_id = applied["job_id"]
    applied_paths = applied["applied_paths"]
    assert applied_paths and "HOME.md" in applied_paths
    assert any(p.startswith("50_Reviews/今日复习-") for p in applied_paths)
    assert_projection_paths(applied_paths)
    assert (root / "HOME.md").is_file()

    # The frozen input/intent is traceable: date, operation options, fingerprint.
    packet = jobs.read_input(root, job_id)
    assert packet["kind"] == "projection" and packet["projection"] == "daily"
    assert packet["date"] == applied["date"]
    assert packet["options"] == {"reviews": True, "entries": True}
    assert packet["snapshot_fingerprint"]
    assert jobs.load(root, job_id)["candidate"] is None and jobs.load(root, job_id)["review"] is None



def test_interrupted_projection_resumes_frozen_date_and_stays_in_bounds(tmp_path, monkeypatch):
    root = build_vault(tmp_path)
    job_id = "daily-frozen"
    today = date(2026, 9, 7)

    # Crash after the durable file writes but before the job state update.
    real_update = jobs.update

    def boom(*args, **kwargs):
        raise RuntimeError("injected crash after durable write")

    monkeypatch.setattr(jobs, "update", boom)
    with pytest.raises(RuntimeError, match="injected crash"):
        generate(root, job_id=job_id, apply=True, today=today)
    monkeypatch.setattr(jobs, "update", real_update)

    state = jobs.load(root, job_id)
    assert state["status"] == "applying"
    assert state["kind"] == "projection"
    assert state["candidate"] is None and state["review"] is None
    frozen = jobs.read_input(root, job_id)
    assert frozen["date"] == "2026-09-07"
    assert frozen["projection"] == "daily"
    assert frozen["options"] == {"reviews": True, "entries": True}
    intent = json.loads((store.state_root(root) / jobs.location(job_id, "commit-intent.json")).read_text())
    assert intent["projection"] is True
    assert "candidate_sha256" not in intent and "review_sha256" not in intent
    review_rel = "50_Reviews/今日复习-2026-09-07.md"
    assert (root / review_rel).is_file()
    review_before = (root / review_rel).read_bytes()
    home_before = (root / "HOME.md").read_bytes()

    # Resume replays the original frozen plan (same date) — no regeneration, and
    # the already-applied journal makes the replay write-free.
    resumed = jobs.resume_transaction(root, job_id)
    assert resumed["status"] == "applied"
    assert resumed["date"] == "2026-09-07"
    assert resumed["applied_paths"]
    assert (root / review_rel).read_bytes() == review_before
    assert (root / "HOME.md").read_bytes() == home_before
    assert not (root / "50_Reviews" / "今日复习-2026-09-08.md").exists()
    # A second resume is idempotent: terminal state is returned, nothing rewritten.
    again = jobs.resume_transaction(root, job_id)
    assert again["status"] == "applied"
    assert again["applied_paths"] == resumed["applied_paths"]
    assert (root / "HOME.md").read_bytes() == home_before

    # The same task id cannot be re-run on another day pretending to be a resume.
    with pytest.raises(store.Conflict, match="已绑定其他输入"):
        generate(root, job_id=job_id, apply=True, today=date(2026, 9, 8))

    # A projection job cannot write cards, the registry or the catalog …
    jobs.create(root, "proj-bad", jobs.PROJECTION_KIND, {"date": "2026-09-07"})
    card = root / "10_Knowledge/attention/card_exist.md"
    card_before = card.read_bytes()
    for rel in ["10_Knowledge/attention/card_exist.md",
                "_data/notes_registry.json", "_data/secondbrain/catalog.json"]:
        raw = (root / rel).read_bytes()
        with pytest.raises(ValueError, match="不能写该路径"):
            jobs.freeze_projection(root, "proj-bad",
                                   [store.Change(rel, store.digest(raw), b"tampered")], checks={})
    assert card.read_bytes() == card_before
    assert jobs.load(root, "proj-bad")["status"] == "prepared"
    assert not jobs.load(root, "proj-bad").get("commit_intent")

    # … and a forged projection-shaped frozen plan is re-checked on resume, so
    # even a tampered intent cannot smuggle knowledge-card writes through.
    forged_card = {"projection": True, "result": {"status": "applied"}, "checks": {}, "changes": [
        {"path": "10_Knowledge/attention/card_exist.md", "before_sha256": store.digest(card_before),
         "after": "dGFtcGVyZWQ="}]}
    store.write_json(root, jobs.location("proj-bad", "commit-intent.json"), forged_card)
    jobs.update(root, "proj-bad", status="applying",
                commit_intent=store.digest(store.read_state(root, jobs.location("proj-bad", "commit-intent.json"))))
    with pytest.raises(ValueError, match="不能写该路径"):
        jobs.resume_transaction(root, "proj-bad")
    assert card.read_bytes() == card_before

    # Taxonomy is a source-of-truth surface, not a browse projection: freeze
    # rejects the whole _pillars directory without changing its original bytes.
    pillar = root / "20_MOCs/_pillars/Example Attention Map.yaml"
    pillar_before = pillar.read_bytes()
    jobs.create(root, "proj-pillar", jobs.PROJECTION_KIND, {"date": "2026-09-07"})
    with pytest.raises(ValueError, match="不能写该路径"):
        jobs.freeze_projection(root, "proj-pillar", [store.Change(
            "20_MOCs/_pillars/Example Attention Map.yaml", store.digest(pillar_before), b"tampered")], checks={})
    assert pillar.read_bytes() == pillar_before
    assert not jobs.load(root, "proj-pillar").get("commit_intent")

    # Resume re-checks the same boundary, so a forged frozen intent cannot write
    # taxonomy either; the YAML remains byte-for-byte unchanged.
    forged_pillar = {"projection": True, "result": {"status": "applied"}, "checks": {}, "changes": [
        {"path": "20_MOCs/_pillars/Example Attention Map.yaml", "before_sha256": store.digest(pillar_before),
         "after": "dGFtcGVyZWQ="}]}
    store.write_json(root, jobs.location("proj-pillar", "commit-intent.json"), forged_pillar)
    jobs.update(root, "proj-pillar", status="applying",
                commit_intent=store.digest(store.read_state(root, jobs.location("proj-pillar", "commit-intent.json"))))
    with pytest.raises(ValueError, match="不能写该路径"):
        jobs.resume_transaction(root, "proj-pillar")
    assert pillar.read_bytes() == pillar_before

    # substitutes for the independent model review of ingest/classification.
    jobs.create(root, "semantic-job", "classification", {"cards": []})
    with pytest.raises(ValueError, match="只有确定性投影"):
        jobs.freeze_projection(root, "semantic-job",
                               [store.Change("50_Reviews/out.md", None, b"x")], checks={})
    # A forged projection-shaped frozen plan cannot hijack a semantic job's resume.
    store.write_json(root, jobs.location("semantic-job", "commit-intent.json"),
                     {"projection": True, "result": {"status": "applied"}, "checks": {}, "changes": []})
    jobs.update(root, "semantic-job", status="applying",
                commit_intent=store.digest(store.read_state(root, jobs.location("semantic-job", "commit-intent.json"))))
    with pytest.raises(store.Conflict, match="类型与任务类型不一致"):
        jobs.resume_transaction(root, "semantic-job")
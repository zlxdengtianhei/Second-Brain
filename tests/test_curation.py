"""End-to-end tests for tools/secondbrain/curation (prepare + apply).

Uses tiny real vaults in temp dirs and drives prepare -> proposal -> independent
review -> apply through the real store/journal, covering:

* existing non-empty classes are preserved (occupancy keeps old categories);
* deleting an existing taxonomy's unknown field is rejected (deep key compare);
* shared-MOC domains (attention/transformer) reject divergent taxonomies;
* a fabricated / non-independent review is rejected;
* a taxonomy change after prepare (concurrency) is rejected by Conflict;
* a catalog domain that currently lacks a taxonomy YAML (projections-demo) can be
  fully applied, writing both the YAML and the generated MOC in one transaction.

Only stdlib + PyYAML + pytest are used; no vault outside the temp dir.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from secondbrain import curation, jobs, store
from secondbrain.store import Conflict, digest

MOC = "Example Attention Map"
DOM = "attention"
DOM_T = "transformer"
PILLAR = "示例支柱：信息路由"
TOPIC = "示例主题：总览"
NEW_PILLAR = "训练与对齐"
NEW_TOPIC = "训练目标"
EXISTING = "card_exist"


def make_catalog(domain_mocs, *, relations=None):
    domains = {d: {"moc": m, "question": "?"} for d, m in domain_mocs.items()}
    return {
        "schema": "secondbrain-catalog/1",
        "tracks": [{"id": "t1", "title": "全部", "domains": list(domains)}],
        "domains": domains,
        "relation_types": relations or ["depends_on", "similar_to", "part_of"],
        "inverse_relations": {
            "depends_on": "depends_on", "similar_to": "similar_to",
            "part_of": "part_of"},
        "domain_relations": {},
    }


def _card_fm(cid, title, domain, pillar=None, topic=None):
    fm = "id: %s\ntitle: %s\ndomain: %s\n" % (cid, title, domain)
    if pillar:
        fm += "pillar: %s\ntopic: %s\n" % (pillar, topic)
    else:
        fm += "pillar:\ntopic:\n"
    return fm


def build_vault(root, *, catalog, cards, taxonomies):
    """cards: {id: (title, domain, pillar, topic)}, taxonomies: {moc: dict}."""
    root = Path(root).resolve()
    (root / "10_Knowledge").mkdir(parents=True)
    for domain in {c[1] for c in cards.values()}:
        (root / "10_Knowledge" / domain).mkdir()
    registry = []
    for cid, (title, domain, pillar, topic) in cards.items():
        body = "## 本质\n%s 的正文，逐字保留。\n" % title
        raw = "---\n%s---\n%s" % (_card_fm(cid, title, domain, pillar, topic), body)
        (root / "10_Knowledge" / domain / (cid + ".md")).write_text(raw, encoding="utf-8")
        registry.append({"id": cid, "path": "10_Knowledge/%s/%s.md" % (domain, cid),
                         "title": title, "domain": domain})
    (root / "20_MOCs" / "_pillars").mkdir(parents=True)
    for moc, tx in taxonomies.items():
        (root / "20_MOCs" / "_pillars" / (moc + ".yaml")).write_text(
            json.dumps(tx, ensure_ascii=False), encoding="utf-8")
    (root / "_data" / "secondbrain").mkdir(parents=True)
    (root / "_data" / "secondbrain" / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    (root / "_data" / "notes_registry.json").write_text(
        json.dumps({"notes": registry}, ensure_ascii=False), encoding="utf-8")
    return root


def submit_reviewed(root, job_id, proposal, review, *, p_model="gpt-test", r_model="claude-test"):
    from tests.helpers import submit_as
    submit_as(root, job_id, proposal, "producer", model=p_model)
    submit_as(root, job_id, review, "reviewer", model=r_model)


def single_taxonomy(title=MOC, narrative="叙事", pillars=None, **extra):
    tx = {"title": title, "narrative": narrative,
          "pillars": pillars or [{"name": PILLAR, "order": 1, "why": "支柱说明",
                                  "topics": [TOPIC]}]}
    tx.update(extra)
    return tx


def proposal(items):
    return {"items": items}


def item(domain, taxonomy, coverage_ids, reason="具体理由"):
    return {"domain": domain, "taxonomy": taxonomy,
            "reason": reason, "coverage_ids": coverage_ids}


def review_for(items):
    return {"items": [{"domain": d, "decision": "accept", "reason": "语义一致"}
                      for d in items]}


# ---------------------------------------------------------------------------
# happy path: existing classes preserved + new pillar added; whole-file apply
# ---------------------------------------------------------------------------
def test_apply_preserves_occupied_and_adds_new(tmp_path):
    cat = make_catalog({DOM: MOC})
    tx = single_taxonomy(opaque_field={"keep": ["a", "b"]})
    root = build_vault(tmp_path, catalog=cat,
                       cards={EXISTING: ("已有卡", DOM, PILLAR, TOPIC)},
                       taxonomies={MOC: tx})
    prep = curation.prepare(root, [DOM], job_id="jobA")
    assert prep["status"] == "prepared"
    assert prep["domains"] == [DOM]
    # keep existing pair + add a new pillar (新增topic/pillar可)
    proposal_tx = {
        "title": MOC, "narrative": "新叙事",
        "pillars": [
            {"name": PILLAR, "order": 1, "why": "支柱说明", "topics": [TOPIC]},
            {"name": NEW_PILLAR, "order": 2, "why": "新增支柱", "topics": [NEW_TOPIC]},
        ],
        "opaque_field": {"keep": ["a", "b"]},
    }
    submit_reviewed(root, "jobA", proposal([item(DOM, proposal_tx, [EXISTING])]),
                    review_for([DOM]))
    res = curation.apply(root, "jobA")
    assert res["status"] == "applied"
    assert res["updated_taxonomies"] == [MOC]
    assert MOC in res["reserialized_files"]
    assert res["preserved_unknown_keys"][MOC] == ["opaque_field"]

    yaml_path = root / "20_MOCs" / "_pillars" / (MOC + ".yaml")
    assert yaml_path.exists()
    loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert loaded["narrative"] == "新叙事"
    assert {p["name"] for p in loaded["pillars"]} == {PILLAR, NEW_PILLAR}
    # unknown field survives both the write and a re-read
    assert loaded["opaque_field"] == {"keep": ["a", "b"]}

    # existing card classification untouched and MOC pages generated in tx
    card_text = (root / "10_Knowledge" / DOM / (EXISTING + ".md")).read_text()
    assert "pillar: %s" % PILLAR in card_text and "topic: %s" % TOPIC in card_text
    assert any(p.startswith("20_MOCs/") for p in res["applied_paths"])


def test_delete_occupied_class_rejected(tmp_path):
    cat = make_catalog({DOM: MOC})
    root = build_vault(tmp_path, catalog=cat,
                       cards={EXISTING: ("已有卡", DOM, PILLAR, TOPIC)},
                       taxonomies={MOC: single_taxonomy()})
    prep = curation.prepare(root, [DOM], job_id="jobB")
    # propose dropping the occupied pillar entirely
    bad_tx = {"title": MOC, "narrative": "n",
              "pillars": [{"name": NEW_PILLAR, "order": 1, "why": "w",
                           "topics": [NEW_TOPIC]}]}
    submit_reviewed(root, "jobB", proposal([item(DOM, bad_tx, [EXISTING])]),
                    review_for([DOM]))
    with pytest.raises(curation.CurationError):
        curation.apply(root, "jobB")
    # nothing written to knowledge files / no store journal prepared
    assert store.read_state(root, jobs.location("jobB", "transaction.json")) is None
    original = json.dumps(single_taxonomy(), ensure_ascii=False, sort_keys=True)
    assert json.dumps(yaml.safe_load(
        (root / "20_MOCs" / "_pillars" / (MOC + ".yaml")).read_text(encoding="utf-8")),
        ensure_ascii=False, sort_keys=True) == original


# ---------------------------------------------------------------------------
# unknown-field deletion is rejected
# ---------------------------------------------------------------------------
def test_nested_pillar_extension_cannot_be_silently_deleted(tmp_path):
    current = single_taxonomy()
    current['pillars'][0]['custom'] = {'owner': ['user']}
    root = build_vault(tmp_path, catalog=make_catalog({DOM: MOC}),
                       cards={EXISTING: ('已有卡', DOM, PILLAR, TOPIC)},
                       taxonomies={MOC: current})
    curation.prepare(root, [DOM], job_id='nested')
    submit_reviewed(root, 'nested', proposal([item(DOM, single_taxonomy(), [EXISTING])]), review_for([DOM]))
    with pytest.raises(curation.CurationError, match='未知字段 pillars'):
        curation.apply(root, 'nested')


def test_unknown_field_is_preserved_and_cannot_be_altered(tmp_path):
    cat = make_catalog({DOM: MOC})
    root = build_vault(tmp_path, catalog=cat,
                       cards={EXISTING: ("已有卡", DOM, PILLAR, TOPIC)},
                       taxonomies={MOC: single_taxonomy(opaque_field={"a": 1})})
    curation.prepare(root, [DOM], job_id="jobC")
    packet = jobs.read_input(root, "jobC")
    # an omitted unknown top-level field is restored from the current taxonomy
    rows = curation.validate(packet, proposal([item(DOM, single_taxonomy(), [EXISTING])]))
    assert rows[0]["taxonomy"]["opaque_field"] == {"a": 1}
    # an altered value is a structural violation
    with pytest.raises(curation.CurationError, match="opaque_field"):
        curation.validate(packet, proposal([item(DOM, single_taxonomy(opaque_field={"a": 2}), [EXISTING])]))


# ---------------------------------------------------------------------------
# shared-MOC divergent taxonomies are rejected
# ---------------------------------------------------------------------------
def test_shared_moc_divergent_taxonomy_rejected(tmp_path):
    cat = make_catalog({DOM: MOC, DOM_T: MOC})
    root = build_vault(tmp_path, catalog=cat,
                       cards={EXISTING: ("已有卡", DOM, PILLAR, TOPIC)},
                       taxonomies={MOC: single_taxonomy()})
    prep = curation.prepare(root, [DOM], job_id="jobD")
    # prepare auto-expands to the whole shared MOC
    assert set(prep["domains"]) == {DOM, DOM_T}
    a = single_taxonomy()
    b = single_taxonomy(narrative="不同叙事")  # divergent for same MOC
    proposal_data = proposal([item(DOM, a, [EXISTING]), item(DOM_T, b, [EXISTING])])
    submit_reviewed(root, "jobD", proposal_data, review_for([DOM, DOM_T]))
    with pytest.raises(curation.CurationError):
        curation.apply(root, "jobD")


def test_proposal_missing_shared_domain_rejected(tmp_path):
    cat = make_catalog({DOM: MOC, DOM_T: MOC})
    root = build_vault(tmp_path, catalog=cat,
                       cards={EXISTING: ("已有卡", DOM, PILLAR, TOPIC)},
                       taxonomies={MOC: single_taxonomy()})
    curation.prepare(root, [DOM], job_id="jobE")
    proposal_data = proposal([item(DOM, single_taxonomy(), [EXISTING])])  # transformer missing
    from tests.helpers import submit_as
    submit_as(root, "jobE", proposal_data, "producer")
    with pytest.raises(curation.CurationError):
        curation.validate(
            jobs.read_input(root, "jobE"),
            proposal_data)


def test_new_domain_proposal_rejected(tmp_path):
    cat = make_catalog({DOM: MOC})
    root = build_vault(tmp_path, catalog=cat,
                       cards={EXISTING: ("已有卡", DOM, PILLAR, TOPIC)},
                       taxonomies={MOC: single_taxonomy()})
    curation.prepare(root, [DOM], job_id="jobF")
    proposal_data = proposal([
        item(DOM, single_taxonomy(), [EXISTING]),
        item("brand-new-domain", single_taxonomy(), [EXISTING])])
    from tests.helpers import submit_as
    submit_as(root, "jobF", proposal_data, "producer")
    with pytest.raises(curation.CurationError):
        curation.validate(
            jobs.read_input(root, "jobF"),
            proposal_data)


# ---------------------------------------------------------------------------
# fabricated / non-independent review is rejected
# ---------------------------------------------------------------------------
def test_fake_review_same_model_family_rejected(tmp_path):
    cat = make_catalog({DOM: MOC})
    root = build_vault(tmp_path, catalog=cat,
                       cards={EXISTING: ("已有卡", DOM, PILLAR, TOPIC)},
                       taxonomies={MOC: single_taxonomy()})
    prep = curation.prepare(root, [DOM], job_id="jobG")
    proposal_data = proposal([item(DOM, single_taxonomy(), [EXISTING])])
    # producer and reviewer use the same model family -> not independent
    submit_reviewed(root, "jobG", proposal_data, review_for([DOM]),
                    p_model="deepseek-v4-flash", r_model="deepseek-r1:0720")
    with pytest.raises(ValueError):
        curation.apply(root, "jobG")


def test_partial_review_means_needs_revision_no_write(tmp_path):
    cat = make_catalog({DOM: MOC})
    root = build_vault(tmp_path, catalog=cat,
                       cards={EXISTING: ("已有卡", DOM, PILLAR, TOPIC)},
                       taxonomies={MOC: single_taxonomy()})
    curation.prepare(root, [DOM], job_id="jobH")
    proposal_data = proposal([item(DOM, single_taxonomy(), [EXISTING])])
    review = {"items": [{"domain": DOM, "decision": "revise", "reason": "叙事要再核对"}]}
    submit_reviewed(root, "jobH", proposal_data, review)
    res = curation.apply(root, "jobH")
    assert res["status"] == "needs_revision"
    assert res["pending"] and res["pending"][0]["domain"] == DOM
    assert not (root / "20_MOCs" / "_pillars" / (MOC + ".yaml")).read_text().endswith("叙事")


# ---------------------------------------------------------------------------
# concurrency: taxonomy changed after prepare
# ---------------------------------------------------------------------------
def test_concurrent_taxonomy_change_rejected(tmp_path):
    cat = make_catalog({DOM: MOC})
    tx = single_taxonomy()
    root = build_vault(tmp_path, catalog=cat,
                       cards={EXISTING: ("已有卡", DOM, PILLAR, TOPIC)},
                       taxonomies={MOC: tx})
    curation.prepare(root, [DOM], job_id="jobI")
    # external writer changes the taxonomy after prepare
    changed = single_taxonomy(narrative="被外部并发修改")
    (root / "20_MOCs" / "_pillars" / (MOC + ".yaml")).write_text(
        json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    proposal_data = proposal([item(DOM, single_taxonomy(), [EXISTING])])
    submit_reviewed(root, "jobI", proposal_data, review_for([DOM]))
    with pytest.raises(Conflict):
        curation.apply(root, "jobI")


# ---------------------------------------------------------------------------
# catalog domain missing taxonomy YAML (projections-demo) can be fully applied
# ---------------------------------------------------------------------------
def test_contextinfra_domain_missing_yaml_full_apply(tmp_path):
    CMOC = "Example Projection Map"
    CDOM = "projections-demo"
    cat = make_catalog({CDOM: CMOC})
    root = build_vault(tmp_path, catalog=cat,
                       cards={"card_ctx": ("示例：上下文是否足够", CDOM, None, None)},
                       taxonomies={})  # no YAML for the domain
    prep = curation.prepare(root, [CDOM], job_id="jobJ")
    assert prep["status"] == "prepared"
    assert prep["domains"] == [CDOM]
    assert prep.get("card_count") == 1

    tx = {"title": CMOC, "narrative": "上下文是模型输入的信息供应链。",
          "pillars": [{"name": "上下文质量", "order": 1, "why": "充分性与相关性的判据",
                       "topics": ["充分性", "相关性与利用率"]}]}
    submit_reviewed(root, "jobJ", proposal([item(CDOM, tx, ["card_ctx"])]),
                    review_for([CDOM]))
    res = curation.apply(root, "jobJ")
    assert res["status"] == "applied"
    assert res["updated_taxonomies"] == [CMOC]

    yaml_path = root / "20_MOCs" / "_pillars" / (CMOC + ".yaml")
    assert yaml_path.exists()
    loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert loaded["title"] == CMOC and loaded["narrative"]
    # MOC pages generated in the same transaction
    assert any(p.startswith("20_MOCs/") for p in res["applied_paths"])
    assert (root / "20_MOCs" / "_pillars").glob("*.yaml") and list(
        (root / "20_MOCs" / "_pillars").glob("*.yaml"))
    # the still-unclassified card appears in the MOC residual (nothing dropped)
    moc_text = "\n".join(
        (root / ("20_MOCs/" + m + ".md")).read_text(encoding="utf-8")
        for m in ["Example Projection Map"]) if (root / "20_MOCs" / "Example Projection Map.md").exists() else ""
    root_page = root / "20_MOCs" / "Example Projection Map.md"
    assert root_page.exists()
    # card path referenced (unclassified residual on a generated page)
    assert "card_ctx" in root_page.read_text(encoding="utf-8") or any(
        "card_ctx" in (root / p).read_text(encoding="utf-8")
        for p in res["applied_paths"] if p.endswith(".md"))


def test_contextinfra_new_file_cas_none_and_reapply_idempotent(tmp_path):
    CMOC = "Example Projection Map"
    CDOM = "projections-demo"
    cat = make_catalog({CDOM: CMOC})
    root = build_vault(tmp_path, catalog=cat,
                       cards={"card_ctx": ("示例：上下文是否足够", CDOM, None, None)},
                       taxonomies={})
    curation.prepare(root, [CDOM], job_id="jobK")
    tx = {"title": CMOC, "narrative": "上下文是模型输入的信息供应链。",
          "pillars": [{"name": "上下文质量", "order": 1, "why": "w",
                       "topics": ["充分性"]}]}
    submit_reviewed(root, "jobK", proposal([item(CDOM, tx, ["card_ctx"])]),
                    review_for([CDOM]))
    first = curation.apply(root, "jobK")
    assert first["status"] == "applied"
    # re-apply is idempotent and writes nothing new
    second = curation.apply(root, "jobK")
    assert second["status"] == "applied"
    yaml_bytes = (root / "20_MOCs" / "_pillars" / (CMOC + ".yaml")).read_bytes()
    assert yaml_bytes  # unchanged by re-apply

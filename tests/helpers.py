"""Shared fixtures: a tiny original vault and a reviewed candidate helper."""
from __future__ import annotations

import json
from pathlib import Path

from secondbrain import jobs
from secondbrain import layout as layouts

MOC = "Example Attention Map"
DOMAIN = "attention"
PILLAR = "Token routing"
TOPIC = "Overview"
EXISTING = "card_exist"
EXISTING_BODY = "## 本质\n已有卡正文，保持逐字不动。\n"

#: Producer and reviewer models of different families (see DEFAULT_MODEL_FAMILIES).
PRODUCER_MODEL = "gpt-test"
REVIEWER_MODEL = "claude-test"

CATALOG = {
    "schema": "secondbrain-catalog/1",
    "tracks": [{"id": "ml", "title": "ML", "domains": [DOMAIN, "transformer"]}],
    "domains": {DOMAIN: {"moc": MOC, "question": "?"},
                "transformer": {"moc": MOC, "question": "?"}},
    "relation_types": ["similar_to", "part_of", "depends_on"],
    "inverse_relations": {"similar_to": "similar_to", "part_of": "part_of",
                          "depends_on": "depends_on"},
    "domain_relations": {},
}
TAXONOMY = {"title": MOC, "narrative": "n",
            "pillars": [{"name": PILLAR, "order": 1, "why": "w", "topics": [TOPIC]}]}


#: A complete non-default layout used to prove folder names are configuration.
CUSTOM_LAYOUT = layouts.VaultLayout(knowledge="Cards", mocs="Maps", taxonomy="Maps/taxonomy",
                                    sources="Library/sources", inbox="Inbox", reviews="Daily",
                                    data="meta", home="Start.md", entry_docs=(), extra_dirs=())


def build_vault(root: Path, *, catalog: dict | None = None,
                lay: layouts.VaultLayout | None = None) -> Path:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    lay = lay or layouts.DEFAULT
    if lay != layouts.DEFAULT:
        layouts.register(root, lay)
    kdom = root / lay.knowledge / DOMAIN
    kdom.mkdir(parents=True)
    (kdom / (EXISTING + ".md")).write_text(
        "---\nid: %s\ntitle: 已有卡\ndomain: %s\npillar: %s\ntopic: %s\n---\n%s"
        % (EXISTING, DOMAIN, PILLAR, TOPIC, EXISTING_BODY), encoding="utf-8")
    (root / lay.taxonomy).mkdir(parents=True)
    (root / lay.taxonomy / (MOC + ".yaml")).write_text(
        json.dumps(TAXONOMY, ensure_ascii=False), encoding="utf-8")
    (root / lay.catalog).parent.mkdir(parents=True)
    (root / lay.catalog).write_text(json.dumps(catalog or CATALOG, ensure_ascii=False), encoding="utf-8")
    (root / lay.registry).write_text(
        json.dumps({"notes": [{"id": EXISTING, "path": lay.card_path(DOMAIN, EXISTING + ".md"),
                               "title": "已有卡", "domain": DOMAIN}]},
                   ensure_ascii=False), encoding="utf-8")
    return root


def submit_as(root: Path, job_id: str, payload: dict, role: str, *, chunk: str | None = None,
              model: str | None = None, attestation: str = "test", step: str | None = None) -> dict:
    """Record a run and submit ``payload`` through the real gate path."""
    model = model or (PRODUCER_MODEL if role == "producer" else REVIEWER_MODEL)
    state = jobs.load(root, job_id)
    slot = state if chunk is None else (state.get("chunks") or {}).get(chunk, {})
    if step is None:
        step = "review" if role == "reviewer" else ("repair" if slot.get("candidate") else "produce")
    run = jobs.start_run(root, job_id, role=role, step=step, executor="fake",
                         model_requested=model, chunk=chunk)
    return jobs.submit(root, job_id, payload, role=role, run_id=run["run_id"],
                       model_reported=model, attestation=attestation)


def submit_reviewed(root: Path, job_id: str, proposal: dict, review: dict, **kwargs):
    submit_as(root, job_id, proposal, "producer", **kwargs)
    return submit_as(root, job_id, review, "reviewer", **kwargs)

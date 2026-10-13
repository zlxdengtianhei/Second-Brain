"""Curation packets: propose/verify/persist topic–pillar–MOC taxonomy changes.

The curation closure updates the per-MOC pillar taxonomy
(``20_MOCs/_pillars/<MOC title>.yaml``) and the generated MOC pages for
*existing* catalog domains only.  It never changes card classifications, never
creates a new domain, and never touches the registry.

Flow (mirrors classification/ingest):

* :func:`prepare` freezes a full, content-addressed packet for the requested
  domains.  Shared-MOC domains (e.g. ``attention``/``transformer``) are
  automatically expanded so the whole MOC is covered; the packet carries the
  complete catalog, the full per-MOC taxonomy, a content hash binding that
  taxonomy, and every card of the relevant domains (id/title/pillar/topic,
  plus full body for unclassified cards).
* A model produces ``{items:[{domain, taxonomy, reason, coverage_ids}]}``
  (one item per domain, mechanical validation via :func:`validate`).  No
  semantic approval happens here — only structural rules.
* An independent reviewer produces ``{items:[{domain, decision, reason}]}``
  covering every domain.  Only when **all** decisions are ``accept`` **and**
  :func:`jobs.require_independent_review` proves distinct session/model family
  does :func:`apply` write, in one store transaction, the taxonomy YAML and the
  rebuilt MOC.  ``review.approved`` is never used.

Only Python standard library + PyYAML.  All vault writes go through store.
"""
from __future__ import annotations

import yaml
from dataclasses import replace
from pathlib import Path

from . import jobs
from .classification import snapshot_checks, taxonomy_fingerprint, writable_snapshot
from .store import Change, Conflict, digest, read_bytes
from .vault import read_snapshot

# Top-level taxonomy keys that curation is allowed to drive.  Any other key in
# Unknown YAML values are preserved structurally; serialization is reported.
OWNED_KEYS = {"title", "narrative", "pillars", "bridges"}



class CurationError(ValueError):
    """Structural curation errors (never a semantic judgement)."""


# ---------------------------------------------------------------------------
# Domain / MOC helpers
# ---------------------------------------------------------------------------
def _domain_moc(catalog, domain):
    meta = (catalog or {}).get("domains", {}).get(domain)
    if not isinstance(meta, dict) or not isinstance(meta.get("moc"), str) or not meta["moc"]:
        raise CurationError(f"domain {domain!r} is not in the catalog; curation does not add domains")
    return meta["moc"]


def _moc_to_domains(catalog):
    out = {}
    domains = catalog.get("domains", {}) if isinstance(catalog, dict) else {}
    for slug, meta in domains.items():
        if isinstance(meta, dict) and isinstance(meta.get("moc"), str) and meta["moc"]:
            out.setdefault(meta["moc"], []).append(slug)
    for lst in out.values():
        lst.sort()
    return out


def _actual_domains(snapshot, requested):
    """Expand ``requested`` to every domain sharing a MOC (shared-MOC coverage)."""
    if not requested:
        raise CurationError("Name at least one existing domain")
    if len(set(requested)) != len(requested):
        raise CurationError("Domains must not repeat")
    moc_to_domains = _moc_to_domains(snapshot.catalog)
    actual = set()
    for dom in requested:
        moc = _domain_moc(snapshot.catalog, dom)
        actual.update(moc_to_domains.get(moc, ()))
    return sorted(actual)


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------
def _cards_for_packet(snapshot, domains, selected_ids):
    """Every relevant card; unclassified cards carry their full body.

    ``selected_ids`` narrows which unclassified bodies are included (default:
    all).  Classified cards are represented by id/title/pillar/topic only — this
    job curates the taxonomy, it never re-classifies cards.
    """
    domain_set = set(domains)
    result = []
    for card in sorted(snapshot.cards.values(), key=lambda c: (c.domain, c.id)):
        if card.domain not in domain_set:
            continue
        entry = {
            "id": card.id,
            "path": card.path,
            "title": card.title,
            "domain": card.domain,
            "pillar": card.fm.get("pillar"),
            "topic": card.fm.get("topic"),
            "sha256": card.sha256,
        }
        if (selected_ids is not None and card.id in selected_ids) or (selected_ids is None and not (entry['pillar'] and entry['topic'])):
            entry['body'] = card.body
        result.append(entry)
    return result


def prepare(root: Path, domains: list[str], *, job_id: str,
            selected_ids: list[str] | None = None) -> dict:
    """Freeze a content-bound curation packet for ``domains`` (expanded)."""
    from .catalog import taxonomy_packet
    snapshot = read_snapshot(root)
    writable_snapshot(snapshot)
    actual = _actual_domains(snapshot, domains)
    if selected_ids is not None:
        if isinstance(selected_ids, (str, bytes)) or not isinstance(selected_ids, list):
            raise CurationError("selected_ids must be a list of card ids")
        eligible = {c.id for c in snapshot.cards.values() if c.domain in actual}
        if len(set(selected_ids)) != len(selected_ids) or not set(selected_ids) <= eligible:
            raise CurationError('selected_ids repeat, do not exist or are outside these domains')
    cards = _cards_for_packet(snapshot, actual, selected_ids)
    packet = {
        "kind": "curation",
        "method_version": "curation/1",
        "domains": actual,
        "cards": cards,
        "catalog": snapshot.catalog,
        "taxonomy": taxonomy_packet(snapshot, actual),
        "taxonomy_sha256": taxonomy_fingerprint(snapshot, actual),
        "taxonomy_scope": actual,
        "snapshot_fingerprint": snapshot.fingerprint,
    }
    state = jobs.create(root, job_id, "curation", packet)
    return {**state, "domains": actual, "card_count": len(cards)}


# ---------------------------------------------------------------------------
# mechanical proposal validation
# ---------------------------------------------------------------------------


def _normalise_taxonomy(tx, domain, moc_title):
    if not isinstance(tx, dict):
        raise CurationError(f"{domain}: the taxonomy must be a mapping")
    title = tx.get("title")
    if title != moc_title:
        raise CurationError(
            f"{domain}: taxonomy.title={title!r} must equal the catalog map name {moc_title!r}")
    narrative = tx.get("narrative")
    if not isinstance(narrative, str) or not narrative.strip():
        raise CurationError(f"{domain}: the taxonomy has no narrative")
    bridges = tx.get("bridges")
    if bridges is not None and not isinstance(bridges, list):
        raise CurationError(f"{domain}: bridges must be a list")
    pillars = tx.get("pillars")
    if not isinstance(pillars, list) or not pillars:
        raise CurationError(f"{domain}: the taxonomy needs at least one pillar")
    seen_names = set()
    seen_orders = set()
    for p in pillars:
        name = p.get("name") if isinstance(p, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise CurationError(f"{domain}: a pillar has no name")
        if name in seen_names:
            raise CurationError(f"{domain}: pillar name repeated: {name!r}")
        seen_names.add(name)
        why = p.get("why")
        if not isinstance(why, str) or not why.strip():
            raise CurationError(f"{domain}: pillar {name!r} has no why")
        order = p.get("order")
        if not isinstance(order, int) or isinstance(order, bool) or order <= 0:
            raise CurationError(f"{domain}: the order of pillar {name!r} must be a positive integer")
        if order in seen_orders:
            raise CurationError(f"{domain}: pillar order repeated: {order}")
        seen_orders.add(order)
        topics = p.get("topics")
        if not isinstance(topics, list) or not topics:
            raise CurationError(f"{domain}: pillar {name!r} needs topics")
        if any(not isinstance(t, str) or not t.strip() for t in topics):
            raise CurationError(f"{domain}: the topics of pillar {name!r} must be non-empty strings")
        if len({t for t in topics}) != len(topics):
            raise CurationError(f"{domain}: pillar {name!r} repeats a topic name")


def _deep_keys_equal_preserved(current, proposed, domain):
    """Unknown (non-owned) top-level keys must survive and be value-identical."""
    owned = set(OWNED_KEYS)
    missing = []
    for k in current:
        if k in owned:
            continue
        if k not in proposed:
            missing.append(f"unknown field {k!r} was removed")
        elif proposed[k] != current[k]:
            missing.append(f"unknown field {k!r} was changed")
    for collection, identity, known in [
        ('pillars', 'name', {'name', 'order', 'why', 'topics'}),
        ('bridges', 'moc', {'moc', 'why', 'links'}),
    ]:
        old_rows = current.get(collection) or []
        new_rows = proposed.get(collection, old_rows) or []
        if not isinstance(old_rows, list) or not isinstance(new_rows, list):
            continue
        indexed = {x.get(identity): x for x in new_rows if isinstance(x, dict)}
        for old in old_rows:
            if not isinstance(old, dict):
                continue
            new = indexed.get(old.get(identity), {})
            for key, value in old.items():
                if key not in known and (key not in new or new[key] != value):
                    missing.append(f'unknown field {collection}.{old.get(identity)}.{key} was removed or changed')
    if missing:
        raise CurationError(f"{domain}: " + "; ".join(sorted(missing)))


def _moc_card_ids(packet, domain, catalog):
    """Card IDs belonging to this domain or to any domain sharing its MOC."""
    moc = _domain_moc(catalog, domain)
    domains_here = set(_moc_to_domains(catalog).get(moc, ()))
    return {c["id"] for c in packet["cards"] if c["domain"] in domains_here}


def validate(packet: dict, proposal: dict) -> list[dict]:
    """Mechanical checks only — never a semantic self-approval."""
    rows = proposal.get("items")
    if not isinstance(rows, list):
        raise CurationError("A curation proposal must be {items:[...]}")
    expected = set(packet["domains"])
    ids = [r.get("domain") for r in rows if isinstance(r, dict)]
    if len(ids) != len(rows) or len(ids) != len(set(ids)) or set(ids) != expected:
        raise CurationError("A curation proposal must cover every prepared domain exactly once")
    taxon = packet.get("taxonomy") or {}
    packet_tax = taxon.get("taxonomies") or {}
    by_moc = {}
    for row in rows:
        domain = row["domain"]
        moc = _domain_moc(packet.get("catalog"), domain)
        _normalise_taxonomy(row.get("taxonomy"), domain, moc)
        reason = row.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise CurationError(f"{domain}: the reason must be a specific string")
        coverage = row.get("coverage_ids")
        if not isinstance(coverage, list) or not coverage:
            raise CurationError(f"{domain}: cite existing cards of this map in coverage_ids as evidence")
        if row.get('source_quotes'):
            raise CurationError(f"{domain}: curation takes existing cards as evidence; source_quotes are not accepted")
        known = _moc_card_ids(packet, domain, packet.get("catalog"))
        # id 分隔符（-/_）转抄错误是本系统已知撞车族：先按去分隔符归一，
        # 唯一匹配则替换成规范 id；多义或无匹配再报错并给出最接近候选。
        import difflib
        norm_map: dict[str, list[str]] = {}
        for cid in known:
            norm_map.setdefault(cid.replace('-', '').replace('_', ''), []).append(cid)
        bad = []
        for i, cid in enumerate(coverage):
            if cid in known:
                continue
            candidates = norm_map.get(str(cid).replace('-', '').replace('_', ''), [])
            if len(candidates) == 1:
                coverage[i] = candidates[0]
            else:
                close = difflib.get_close_matches(str(cid), sorted(known), n=3, cutoff=0.6)
                bad.append(f'{cid} (closest: {", ".join(close) or "none"})')
        if bad:
            raise CurationError(f"{domain}: coverage_ids that are not existing cards of this map: {'; '.join(bad)}")
        # 非策展顶层字段（如既有 YAML 的 source）由服务端按现状补齐，与
        # _merge_taxonomy 的保留语义一致；模型只需设计策展字段，不转抄元数据。
        current_tax = packet_tax.get(moc) or {}
        tx = row["taxonomy"]
        for k in current_tax:
            if k not in OWNED_KEYS and k not in tx:
                tx[k] = current_tax[k]
        _deep_keys_equal_preserved(current_tax, tx, domain)
        by_moc.setdefault(moc, []).append(row["taxonomy"])
    # Shared MOC: every domain in the same MOC must carry the identical taxonomy.
    for moc, txs in by_moc.items():
        base = txs[0]
        if any(tx != base for tx in txs[1:]):
            raise CurationError(f"The domains of map {moc!r} propose different taxonomies; rejected")
    return rows


def review_rows(rows: list[dict], review: dict) -> dict[str, dict]:
    findings = review.get("items")
    if not isinstance(findings, list):
        raise CurationError("A review must be {items:[{domain,decision,reason}]}")
    doms = [x.get("domain") for x in findings if isinstance(x, dict)]
    if len(doms) != len(findings) or len(doms) != len(set(doms)) or \
            set(doms) != {r["domain"] for r in rows}:
        raise CurationError("The independent review does not cover every candidate domain")
    out = {}
    for x in findings:
        if x.get("decision") not in {"accept", "revise"}:
            raise CurationError(f"{x.get('domain')}: the review decision must be accept or revise")
        if not isinstance(x.get("reason"), str) or not x["reason"].strip():
            raise CurationError(f"{x.get('domain')}: the review reason must be specific")
        out[x["domain"]] = x
    return out


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------
def _taxonomy_path(root, moc_title):
    from . import layout as layouts
    return layouts.of(root).taxonomy_path(moc_title)


def _serialise_taxonomy(merged):
    text = yaml.safe_dump(merged, allow_unicode=True, sort_keys=False,
                          default_flow_style=False)
    return text.encode("utf-8")


def _merge_taxonomy(current, proposed_tx):
    """Proposed known keys + every unknown key preserved from the current dict."""
    base = dict(current or {})
    base["title"] = proposed_tx["title"]
    base["narrative"] = proposed_tx["narrative"]
    base["pillars"] = proposed_tx["pillars"]
    if "bridges" in proposed_tx:
        base["bridges"] = proposed_tx["bridges"]
    for k in current or {}:
        if k in OWNED_KEYS:
            continue
        if k not in base or base[k] != current[k]:
            raise CurationError(f"unknown field {k!r} was lost or changed in the merge")
    return base


def _occupied_removed(snapshot, packet, rows_by_domain_moc):
    """Reject any change that would leave an existing classified card illegal.

    Curation never re-classifies cards, so every pillar/topic occupied by a
    current card must survive in the proposed taxonomy (per-MOC).
    """
    rows = packet["domains"]
    # map domain -> its proposed taxonomy (identical for shared MOC)
    tax_by_domain = {r["domain"]: r["taxonomy"] for r in rows_by_domain_moc}
    # Build the proposed pair set per MOC.
    pairs_by_moc = {}
    for domain in rows:
        moc = _domain_moc(packet.get("catalog"), domain)
        pairs = set()
        for p in tax_by_domain[domain]["pillars"]:
            for t in p["topics"]:
                pairs.add((p["name"], t))
        pairs_by_moc[moc] = pairs
        prior = snapshot.taxonomies.get(moc) or {}
        prior_pairs = {(p['name'], t) for p in prior.get('pillars', []) for t in p.get('topics', [])}
        if not prior_pairs <= pairs:
            raise CurationError(f'Extending a taxonomy cannot remove existing categories: {moc}')
    for card in snapshot.cards.values():
        moc = None
        meta = (snapshot.catalog or {}).get("domains", {}).get(card.domain)
        if isinstance(meta, dict):
            moc = meta.get("moc")
        if moc not in pairs_by_moc:
            continue
        pillar, topic = card.fm.get("pillar"), card.fm.get("topic")
        prior = snapshot.taxonomies.get(moc) or {}
        prior_pairs = {(p['name'], t) for p in prior.get('pillars', []) for t in p.get('topics', [])}
        if (pillar, topic) in prior_pairs and (pillar, topic) not in pairs_by_moc[moc]:
            raise CurationError(
                f"Removing this would make an existing card invalid: {card.id} pillar={pillar!r} "
                f"topic={topic!r}; keep the old category")


def _taxonomy_changes(root, snapshot, packet, rows, moc_taxonomies):
    """Compose Change objects for each updated MOC taxonomy YAML."""
    changes = []
    written = []
    rows_by_domain = {r["domain"]: r for r in rows}
    for domain in packet["domains"]:
        moc = _domain_moc(packet["catalog"], domain)
        if moc in written:
            continue
        written.append(moc)
        current = snapshot.taxonomies.get(moc)
        merged = _merge_taxonomy(current, rows_by_domain[domain]["taxonomy"])
        rel = _taxonomy_path(root, moc)
        before_raw = read_bytes(root, rel)
        before = None if before_raw is None else digest(before_raw)
        changes.append(Change(rel, before, _serialise_taxonomy(merged)))
        moc_taxonomies[moc] = merged
    return changes, sorted(written)


def _projection_changes(root, projection):
    result = []
    for rel, data in sorted(projection.files.items()):
        before = read_bytes(root, rel)
        if before != data:
            result.append(Change(rel, None if before is None else digest(before), data))
    return result


def apply(root: Path, job_id: str) -> dict:
    from .moc import build_mocs
    state = jobs.load(root, job_id)
    if state["kind"] != "curation":
        raise CurationError("This job is not a curation job")
    if state["status"] in {"applied", "published", "partial", "rolled_back"}:
        return state
    resumed = jobs.resume_transaction(root, job_id)
    if resumed is not None:
        return resumed
    packet = jobs.read_input(root, job_id)
    proposal = jobs.artifact(root, state, "candidate")
    rows = validate(packet, proposal)
    # Real independent review required; a fabricated/same-family review aborts here.
    review = jobs.require_independent_review(root, state)
    findings = review_rows(rows, review)
    rejects = [x for x in findings.values() if x["decision"] != "accept"]
    if rejects:
        pending = [{"domain": r["domain"], "decision": r["decision"], "reason": r["reason"],
                    "proposal": rows_by_id(r, rows)} for r in rejects]
        return jobs.update(root, job_id, status="needs_revision", pending=pending,
                           applied_paths=[])
    if not all(x["decision"] == "accept" for x in findings.values()):
        raise CurationError("Internal inconsistency: not every review item is accept")

    snapshot = read_snapshot(root)
    writable_snapshot(snapshot)
    if taxonomy_fingerprint(snapshot, packet.get("taxonomy_scope")) != packet["taxonomy_sha256"]:
        raise Conflict("The catalog or taxonomy changed after preparation; prepare the job again")

    _occupied_removed(snapshot, packet, rows)

    moc_taxonomies = {}
    changes, written = _taxonomy_changes(root, snapshot, packet, rows, moc_taxonomies)
    preserved = {}
    reserialized = []
    for moc in written:
        current = snapshot.taxonomies.get(moc)
        if current is not None:
            reserialized.append(moc)
            preserved[moc] = sorted(k for k in current if k not in OWNED_KEYS)

    # Rebuild the MOC from a snapshot whose taxonomies carry the curated dicts.
    projected_snapshot = replace(
        snapshot, taxonomies={**snapshot.taxonomies, **moc_taxonomies})
    projection = build_mocs(projected_snapshot, affected_domains=set(packet["domains"]))
    changes.extend(_projection_changes(root, projection))

    if not changes:
        return jobs.update(root, job_id, status="applied", pending=[],
                           updated_taxonomies=written, projection_metrics=projection.metrics)

    return jobs.finish_transaction(root, job_id,
        {"status": "applied", "pending": [], "updated_taxonomies": written,
         "reserialized_files": reserialized, "preserved_unknown_keys": preserved,
         "projection_metrics": projection.metrics}, changes, checks=snapshot_checks(snapshot, changes))


def rows_by_id(reject: dict, rows: list[dict]) -> dict:
    for r in rows:
        if r["domain"] == reject["domain"]:
            return r
    return {}

"""Tests for tools/secondbrain/vault.py (read-only snapshot + frontmatter).

Everything runs against temp directories / in-memory bytes; the real vault is
never written.
"""
import hashlib
import json
import os

import pytest

from secondbrain import vault as v


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _write(path, text, newline="\n"):
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    data = text.replace("\n", newline)
    with open(path, "wb") as fh:
        fh.write(data.encode("utf-8"))
    return data.encode("utf-8")


def _basic_card_raw():
    return (
        "---\n"
        "id: card_block\n"
        "title: 示例：模块数据流\n"
        "kind: concept\n"
        "domain: transformer\n"
        "pillar: 示例支柱：信息路由\n"
        "topic: 示例主题：张量数据流\n"
        "tags: [review, flashcards/transformer]\n"
        "sources:\n"
        "  - path: 40_Sources/repo/a.json\n"
        "    sha256: abc\n"
        "relations:\n"
        "  - type: similar_to\n"
        "    target: card_other\n"
        "---\n"
        "## 本质\n"
        "body 中文内容\n"
        "## 复习卡\n"
        "q1::a1\n"
    ).encode("utf-8")


def _build_vault(root, cards, registry_notes, taxonomies=None, catalog=None):
    """Materialize a minimal vault in ``root`` and return the raw registry."""
    for cid, path, title, domain, extra_fm in cards:
        text = "---\nid: %s\ntitle: %s\ndomain: %s\n" % (cid, title, domain)
        for k, val in extra_fm.items():
            text += "%s: %s\n" % (k, val)
        text = text + "---\n## 本质\nbody-for-%s\n" % cid
        _write(str(root / path), text)
    reg = {"notes": registry_notes}
    os.makedirs(str(root / "_data"), exist_ok=True)
    with open(str(root / "_data" / "notes_registry.json"), "w", encoding="utf-8") as fh:
        json.dump(reg, fh, ensure_ascii=False)
    if taxonomies:
        for title, payload in taxonomies.items():
            d = {"title": title}
            d.update(payload)
            _write(str(root / "20_MOCs" / "_pillars" / ("%s.yaml" % title)),
                   json.dumps(d, ensure_ascii=False).replace(",", ", "))
    if catalog is not None:
        os.makedirs(str(root / "_data" / "secondbrain"), exist_ok=True)
        with open(str(root / "_data" / "secondbrain" / "catalog.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(catalog, fh)
    return reg


# --------------------------------------------------------------------------
# parse_note
# --------------------------------------------------------------------------
def test_parse_note_full_body_and_sr_kept():
    raw = _basic_card_raw()
    fm, body = v.parse_note(raw)
    assert fm["id"] == "card_block"
    assert fm["title"] == "示例：模块数据流"
    assert fm["domain"] == "transformer"
    assert fm["tags"] == ["review", "flashcards/transformer"]
    assert fm["relations"] == [{"type": "similar_to", "target": "card_other"}]
    expected_body = "## 本质\nbody 中文内容\n## 复习卡\nq1::a1\n"
    assert body == expected_body


def test_parse_note_flow_value_and_multiline():
    raw = (
        "---\ntitle: 中文题名\nkind: concept\n"
        "tags: [a, b]\n"
        'note: |\n  line one\n  line two\n---\nbody\n'
    ).encode("utf-8")
    fm, body = v.parse_note(raw)
    assert fm["title"] == "中文题名"
    assert fm["tags"] == ["a", "b"]
    assert fm["note"] == "line one\nline two\n"
    assert body == "body\n"


def test_parse_note_preserves_crlf_body():
    raw = (
        "---\r\nid: x\r\ntitle: 中文\r\n---\r\n"
        "## 复习卡\r\nq1::a1\r\n"
    ).encode("utf-8")
    fm, body = v.parse_note(raw)
    assert fm["id"] == "x"
    # body must keep exact bytes (including CRLF and trailing newline)
    assert body == "## 复习卡\r\nq1::a1\r\n"


def test_parse_note_rejects_duplicate_keys():
    with pytest.raises(v.VaultError):
        v.parse_note(b"---\na: 1\na: 2\n---\nbody\n")


def test_parse_note_rejects_missing_opener():
    with pytest.raises(v.VaultError):
        v.parse_note(b"id: x\n---\nbody\n")


def test_parse_note_rejects_missing_closer():
    with pytest.raises(v.VaultError):
        v.parse_note(b"---\na: 1\nbody\n")


def test_parse_note_rejects_non_mapping():
    # frontmatter that is a list, not a mapping
    with pytest.raises(v.VaultError):
        v.parse_note(b"---\n- a\n- b\n---\nbody\n")


def test_parse_note_rejects_non_bytes():
    with pytest.raises(v.VaultError):
        v.parse_note("---\na: 1\n---\n")


# --------------------------------------------------------------------------
# patch_frontmatter
# --------------------------------------------------------------------------
def test_patch_only_touches_target_key():
    raw = _basic_card_raw()
    patched = v.patch_frontmatter(raw, {"topic": "新主题块"})
    fm, body = v.parse_note(patched)
    assert fm["topic"] == "新主题块"
    # untouched fields
    assert fm["id"] == "card_block"
    assert fm["title"] == "示例：模块数据流"
    assert fm["tags"] == ["review", "flashcards/transformer"]
    assert fm["relations"] == [{"type": "similar_to", "target": "card_other"}]
    # body (incl. SR) byte-for-byte unchanged
    _fm, ob = v.parse_note(raw)
    assert body == ob
    # raw bytes surrounding the frontmatter are unchanged
    nl = raw.find(b"\n")
    assert patched[raw.find(b"---", nl):] is not None


def test_patch_preserves_verbatim_around_target():
    raw = (
        "---\n"
        "# 这是注释 逐字保留\n"
        "\n"
        "title: 旧题名\n"
        "unknown_field: {\"flow\": \"value\"}\n"
        "another: x\n"
        "\n"
        "---\n"
        "## 复习卡\nq::a\n"
    ).encode("utf-8")
    patched = v.patch_frontmatter(raw, {"title": "新题名"})
    assert "# 这是注释 逐字保留".encode("utf-8") in patched
    assert b'unknown_field: {"flow": "value"}' in patched
    assert b"another: x" in patched
    assert b"\n\n" in patched  # blank lines preserved
    assert patched.endswith("## 复习卡\nq::a\n".encode("utf-8"))
    fm, body = v.parse_note(patched)
    assert fm["title"] == "新题名"
    assert fm["unknown_field"] == {"flow": "value"}
    assert body == "## 复习卡\nq::a\n"


def test_patch_adds_new_key_and_null():
    raw = _basic_card_raw()
    patched = v.patch_frontmatter(raw, {"category": "新增域", "approved": None})
    fm, body = v.parse_note(patched)
    assert fm["category"] == "新增域"
    assert fm["approved"] is None
    assert body == "## 本质\nbody 中文内容\n## 复习卡\nq1::a1\n"


def test_patch_multiline_value():
    raw = _basic_card_raw()
    new_val = "第一行\n第二行\n"
    patched = v.patch_frontmatter(raw, {"topic": new_val})
    fm, _ = v.parse_note(patched)
    assert fm["topic"] == new_val


def test_patch_crlf_mixed_preserved():
    raw = (
        "---\r\ntitle: 中文\r\nkind: concept\r\n"
        "domain: transformer\r\n---\r\n## 复习卡\r\nq::a\r\n"
    ).encode("utf-8")
    patched = v.patch_frontmatter(raw, {"title": "更新后"})
    assert b"\r\n" in patched
    # untouched CRLF fields keep CRLF (split and re-check presence)
    assert b"kind: concept\r\n" in patched
    assert b"domain: transformer\r\n" in patched
    fm, body = v.parse_note(patched)
    assert fm["title"] == "更新后"
    assert body == "## 复习卡\r\nq::a\r\n"


def test_patch_rejects_duplicate_target_key():
    raw = b"---\na: 1\nb: 2\na: 3\n---\nbody\n"
    with pytest.raises(v.VaultError):
        v.patch_frontmatter(raw, {"a": "x"})


def test_patch_rejects_empty_updates():
    raw = _basic_card_raw()
    with pytest.raises(v.VaultError):
        v.patch_frontmatter(raw, {})
    with pytest.raises(v.VaultError):
        v.patch_frontmatter(raw, {123: "v"})


# --------------------------------------------------------------------------
# read_snapshot
# --------------------------------------------------------------------------
def test_read_snapshot_basic(tmp_path):
    notes = [
        {"id": "card_a", "path": "10_Knowledge/transformer/A.md",
         "title": "A卡", "domain": "transformer", "seeded_from": "/src", "extra_unknown": "keep"},
        {"id": "card_b", "path": "10_Knowledge/attention/B.md",
         "title": "B卡", "domain": "attention"},
    ]
    cards = [
        ("card_a", "10_Knowledge/transformer/A.md", "A卡", "transformer", {"pillar": "p1", "topic": "t1"}),
        ("card_b", "10_Knowledge/attention/B.md", "B卡", "attention", {"pillar": "p2"}),
    ]
    tax = {"transformer 域": {"narrative": "n", "source": "human",
                              "pillars": [{"name": "p", "order": 1, "why": "w", "topics": ["t"]}],
                              "bridges": []}}
    _build_vault(tmp_path, cards, notes, taxonomies=tax)
    snap = v.read_snapshot(tmp_path)
    assert set(snap.cards) == {"card_a", "card_b"}
    assert snap.cards["card_a"].domain == "transformer"
    assert snap.cards["card_a"].body == "## 本质\nbody-for-card_a\n"
    # registry unknown fields retained
    assert snap.registry["notes"][0]["extra_unknown"] == "keep"
    # taxonomy keyed by MOC title
    assert "transformer 域" in snap.taxonomies
    assert snap.taxonomies["transformer 域"]["narrative"] == "n"
    # default catalog missing -> explicit issue, empty catalog
    codes = {i["code"] for i in snap.issues}
    assert "catalog_missing" in codes
    assert snap.catalog == {}
    assert isinstance(snap.fingerprint, str) and len(snap.fingerprint) == 64


def test_read_snapshot_sha256_matches_file(tmp_path):
    raw = _basic_card_raw()
    _write(str(tmp_path / "10_Knowledge" / "d" / "a.md"), raw.decode("utf-8"))
    _write(str(tmp_path / "_data" / "notes_registry.json"),
           json.dumps({"notes": [{"id": "card_block",
                                  "path": "10_Knowledge/d/a.md",
                                  "title": "示例：模块数据流",
                                  "domain": "transformer"}]}))
    snap = v.read_snapshot(tmp_path, catalog={})
    card = snap.cards["card_block"]
    assert card.sha256 == hashlib.sha256(raw).hexdigest()
    assert card.raw == raw


def test_read_snapshot_issues_unregistered_dropped_mismatch(tmp_path):
    # three cards: one unregistered-in-registry, one identity mismatch,
    # one dropped (registry says file missing)
    cards = [
        ("card_ok", "10_Knowledge/d/ok.md", "OK卡", "transformer", {}),
        ("card_unreg", "10_Knowledge/d/unreg.md", "未登记卡", "transformer", {}),
        ("card_mismatch", "10_Knowledge/d/mm.md", "A名称", "transformer", {}),
    ]
    notes = [
        {"id": "card_ok", "path": "10_Knowledge/d/ok.md", "title": "OK卡", "domain": "transformer"},
        {"id": "card_mismatch", "path": "10_Knowledge/d/mm.md", "title": "应是B名称", "domain": "transformer"},
        {"id": "card_gone", "path": "10_Knowledge/d/gone.md", "title": "丢了", "domain": "transformer"},
    ]
    _build_vault(tmp_path, cards, notes)
    snap = v.read_snapshot(tmp_path, catalog={})
    codes = {i["code"] for i in snap.issues}
    assert "unregistered" in codes
    assert "dropped_card" in codes
    assert "identity_mismatch" in codes
    # the unregistered and mismatch cards are still physically present in snapshot
    assert set(snap.cards) == {"card_ok", "card_unreg", "card_mismatch"}


def test_read_snapshot_duplicate_id_and_parse_failure(tmp_path):
    cards = [
        ("card_dup", "10_Knowledge/d/one.md", "一份", "transformer", {}),
        ("card_dup", "10_Knowledge/d/two.md", "两份", "transformer", {}),
    ]
    notes = [
        {"id": "card_dup", "path": "10_Knowledge/d/one.md", "title": "一份", "domain": "transformer"},
    ]
    _build_vault(tmp_path, cards, notes)
    # add a file with broken frontmatter (no closer)
    _write(str(tmp_path / "10_Knowledge" / "d" / "_broken.md"),
           "---\nid: x\nno closer here\n")
    snap = v.read_snapshot(tmp_path, catalog={})
    codes = {i["code"] for i in snap.issues}
    assert "duplicate_id" in codes
    assert "parse_failure" in codes
    # duplicate card not silently present twice; still one entry + explicit issue
    assert snap.cards["card_dup"].path == "10_Knowledge/d/one.md"


def test_read_snapshot_rejects_symlink_and_escapes(tmp_path):
    _write(str(tmp_path / "10_Knowledge" / "d" / "real.md"), "---\nid: card_r\ntitle: R\ndomain: d\n---\nb\n")
    _write(str(tmp_path / "_data" / "notes_registry.json"),
           json.dumps({"notes": [{"id": "card_r", "path": "10_Knowledge/d/real.md",
                                  "title": "R", "domain": "d"}]}))
    # symlink to a real file must not be read into the snapshot
    try:
        os.symlink(str(tmp_path / "10_Knowledge" / "d" / "real.md"),
                   str(tmp_path / "10_Knowledge" / "d" / "link.md"))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    # registry entry with path traversal must not resolve
    notes = json.load(open(str(tmp_path / "_data" / "notes_registry.json")))
    notes["notes"].append({"id": "card_evil", "path": "../10_Knowledge/other.md",
                           "title": "E", "domain": "d"})
    with open(str(tmp_path / "_data" / "notes_registry.json"), "w") as fh:
        json.dump(notes, fh)
    snap = v.read_snapshot(tmp_path, catalog={})
    assert "card_r" in snap.cards
    assert not any(c.path.endswith("link.md") for c in snap.cards.values())
    codes = {i["code"] for i in snap.issues}
    assert "unsafe_symlink" in codes
    # traversal path just never resolves to a real file -> dropped_card-ish; no crash
    assert all(i["code"] != "unserializable" for i in snap.issues)


def test_read_snapshot_duplicate_registry_path(tmp_path):
    cards = [("card_a", "10_Knowledge/d/a.md", "A", "d", {})]
    notes = [
        {"id": "card_a", "path": "10_Knowledge/d/a.md", "title": "A", "domain": "d"},
        {"id": "card_a2", "path": "10_Knowledge/d/a.md", "title": "A2", "domain": "d"},
    ]
    _build_vault(tmp_path, cards, notes)
    snap = v.read_snapshot(tmp_path, catalog={})
    codes = {i["code"] for i in snap.issues}
    assert "duplicate_registry_path" in codes


def test_read_snapshot_explicit_catalog_and_missing_taxonomy(tmp_path):
    cards = [("card_a", "10_Knowledge/d/a.md", "A", "mydom", {})]
    notes = [{"id": "card_a", "path": "10_Knowledge/d/a.md", "title": "A", "domain": "mydom"}]
    _build_vault(tmp_path, cards, notes)
    catalog = {
        "schema": "secondbrain-catalog/1",
        "tracks": [{"id": "t", "title": "T", "domains": ["mydom"]}],
        "domains": {"mydom": {"moc": "我的 MOC", "question": "Q?"}},
        "relation_types": ["related_to"],
        "inverse_relations": {"related_to": "related_to"},
        "domain_relations": {"mydom": ["related_to"]},
    }
    snap = v.read_snapshot(tmp_path, catalog=catalog)
    assert snap.catalog["domains"]["mydom"]["moc"] == "我的 MOC"
    assert "catalog_missing" not in {i["code"] for i in snap.issues}
    # no taxonomy for the catalog domain's MOC -> issue, but card retained
    codes = {i["code"] for i in snap.issues}
    assert "missing_taxonomy" in codes
    assert snap.cards["card_a"].domain == "mydom"


def test_read_snapshot_reads_default_catalog_file(tmp_path):
    cards = [("card_a", "10_Knowledge/d/a.md", "A", "d", {})]
    notes = [{"id": "card_a", "path": "10_Knowledge/d/a.md", "title": "A", "domain": "d"}]
    _build_vault(tmp_path, cards, notes)
    catalog = {
        "schema": "secondbrain-catalog/1",
        "tracks": [{"id": "t", "title": "T", "domains": ["d"]}],
        "domains": {"d": {"moc": "M", "question": "Q"}},
        "relation_types": [],
        "inverse_relations": {},
        "domain_relations": {},
    }
    os.makedirs(str(tmp_path / "_data" / "secondbrain"), exist_ok=True)
    with open(str(tmp_path / "_data" / "secondbrain" / "catalog.json"), "w") as fh:
        json.dump(catalog, fh)
    snap = v.read_snapshot(tmp_path)
    assert snap.catalog["schema"] == "secondbrain-catalog/1"
    assert "catalog_missing" not in {i["code"] for i in snap.issues}


def test_read_snapshot_fingerprint_deterministic_and_sensitive(tmp_path):
    cards = [("card_a", "10_Knowledge/d/a.md", "A", "d", {})]
    notes = [{"id": "card_a", "path": "10_Knowledge/d/a.md", "title": "A", "domain": "d"}]
    _build_vault(tmp_path, cards, notes)
    catalog = {
        "schema": "secondbrain-catalog/1",
        "tracks": [{"id": "t", "title": "T", "domains": ["d"]}],
        "domains": {"d": {"moc": "M", "question": "Q"}},
        "relation_types": [],
        "inverse_relations": {},
        "domain_relations": {},
    }
    s1 = v.read_snapshot(tmp_path, catalog=catalog)
    s2 = v.read_snapshot(tmp_path, catalog=catalog)
    assert s1.fingerprint == s2.fingerprint
    # changing a card changes the fingerprint
    _write(str(tmp_path / "10_Knowledge" / "d" / "a.md"),
           "---\nid: card_a\ntitle: A\ndomain: d\n---\ntweaked\n")
    s3 = v.read_snapshot(tmp_path, catalog=catalog)
    assert s1.fingerprint != s3.fingerprint


def test_read_snapshot_rejects_non_directory_root():
    with pytest.raises(v.VaultError):
        v.read_snapshot("/nonexistent/vault/root/xyz")


def test_card_packet_shape(tmp_path):
    raw = _basic_card_raw()
    fm, body = v.parse_note(raw)
    card = v.Card(id="card_block", title=fm["title"], domain=fm["domain"],
                  path="10_Knowledge/transformer/x.md", raw=raw, fm=fm, body=body,
                  sha256=hashlib.sha256(raw).hexdigest())
    p = v.card_packet(card)
    assert p["id"] == "card_block"
    assert p["path"] == "10_Knowledge/transformer/x.md"
    assert p["pillar"] == "示例支柱：信息路由"
    assert p["topic"] == "示例主题：张量数据流"
    assert p["relations"] == [{"type": "similar_to", "target": "card_other"}]
    assert p["body"] == body  # full body, not truncated

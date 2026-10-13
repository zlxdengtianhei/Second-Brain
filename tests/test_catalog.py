"""Tests for tools/secondbrain/catalog.py (structural catalog validation)."""
import json
import os

import pytest
import yaml

from secondbrain import catalog as cmod
from secondbrain import vault as v


# --------------------------------------------------------------------------
# a complete, valid catalog (models the real seed: domain-specific relation
# words may be absent from relation_types but still legal via domain_relations,
# and symmetric relations like similar_to need no inverse entry)
# --------------------------------------------------------------------------
def _valid_catalog():
    return {
        "schema": "secondbrain-catalog/1",
        "tracks": [
            {"id": "track-arch", "title": "架构", "domains": ["attention", "transformer"]},
            {"id": "track-sec", "title": "安全", "domains": ["security", "privacy"]},
            {"id": "track-app", "title": "创作", "domains": ["astrophotography"]},
        ],
        "domains": {
            "attention": {"moc": "Example Attention Map", "question": "注意机制怎么工作"},
            "transformer": {"moc": "Example Attention Map", "question": "Transformer 是什么"},
            "security": {"moc": "Example Security Map", "question": "保护什么"},
            "privacy": {"moc": "Example Privacy Map", "question": "能不能关联到人"},
            "astrophotography": {"moc": "Example Photo Map", "question": "怎么拍"},
        },
        "relation_types": ["similar_to", "depends_on", "required_by", "tool_for", "used_by"],
        "inverse_relations": {
            "depends_on": "required_by",
            "required_by": "depends_on",
            "tool_for": "used_by",
            "used_by": "tool_for",
        },
        "domain_relations": {
            "attention": ["similar_to", "depends_on"],
            "security": ["similar_to"],
            "astrophotography": ["tool_for", "used_by"],
        },
        "relation_gloss": {"similar_to": "相似", "depends_on": "依赖"},
    }


def test_validate_ok():
    data = _valid_catalog()
    out = cmod.validate_catalog(data)
    assert out is data  # returns the data (or canonical copy) unchanged
    # unknown fields (e.g. relation_gloss) are preserved, not dropped
    assert out["relation_gloss"] == {"similar_to": "相似", "depends_on": "依赖"}


def test_validate_domain_specific_inverses_are_legal():
    # a relation that appears only in a domain_relations list (not in
    # relation_types) still closes correctly over the union
    data = _valid_catalog()
    data["domain_relations"]["astrophotography"] = ["step_of", "has_step"]
    data["inverse_relations"].update({"step_of": "has_step", "has_step": "step_of"})
    cmod.validate_catalog(data)  # must not raise


def test_validate_preserves_unknown_field(tmp_path):
    catalog = _valid_catalog()
    catalog['relation_gloss'] = {'depends_on': {'label': '依赖', 'unknown_nested': [1, 2]}}
    out = cmod.validate_catalog(catalog)
    # the real seed carries relation_gloss - keep it intact
    assert "relation_gloss" in out
    assert isinstance(out["relation_gloss"], dict)
    assert out['relation_gloss'] == catalog['relation_gloss']


@pytest.mark.parametrize("mutator,detail", [
    (lambda d: d.update({"schema": "other"}), "schema"),
    (lambda d: d.__setitem__("domains", {"bad/slug": {"moc": "M", "question": "Q"}}), "slug"),
    (lambda d: d.__setitem__("domains", {"d": {"moc": "../../evil", "question": "Q"}}), "moc"),
    (lambda d: d.__setitem__("domains", {"d": {"moc": "M"}}), "question missing"),
    (lambda d: d.__setitem__("tracks", [{"id": "t", "title": "T",
                                         "domains": ["attention", "attention"]}]), "track dup"),
    (lambda d: d.__setitem__("tracks", [{"id": "t", "title": "T", "domains": ["nope"]}]), "unknown"),
    (lambda d: d.__setitem__("tracks", []), "uncovered"),
    (lambda d: d.__setitem__("domain_relations", {"missing-slug": ["similar_to"]}), "unknown rel"),
    (lambda d: d.update({"inverse_relations": {"similar_to": "similar_to",
                                               "depends_on": "follows",
                                               "follows": "similar_to"}}), "closure broken"),
    (lambda d: d.update({"inverse_relations": {"similar_to": "bogus_word"}}), "inverse value unknown"),
])
def test_validate_rejects_invalid(mutator, detail):
    data = _valid_catalog()
    mutator(data)
    with pytest.raises(cmod.CatalogError):
        cmod.validate_catalog(data)
    _ = detail


def test_validate_rejects_non_dict():
    with pytest.raises(ValueError):
        cmod.validate_catalog([1, 2])
    with pytest.raises(cmod.CatalogError):
        cmod.validate_catalog("nope")


# --------------------------------------------------------------------------
# snapshot helper (build a real vault, read_snapshot it)
# --------------------------------------------------------------------------
def _snapshot(tmp_path, domains, moc_of, taxonomies):
    cards = []
    notes = []
    for i, slug in enumerate(domains):
        cid = "card_%d" % i
        path = "10_Knowledge/%s/c%d.md" % (slug, i)
        cards.append((cid, path, "卡%d" % i, slug,
                      {"pillar": "柱%d" % i, "topic": "题%d" % i}))
        notes.append({"id": cid, "path": path, "title": "卡%d" % i, "domain": slug})
    os.makedirs(str(tmp_path / "_data"), exist_ok=True)
    with open(str(tmp_path / "_data" / "notes_registry.json"), "w", encoding="utf-8") as fh:
        json.dump({"notes": notes}, fh)
    for slug in domains:
        path = str(tmp_path / "10_Knowledge" / slug / ("c%d.md" % domains.index(slug)))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("---\nid: %s\ntitle: 卡%d\ndomain: %s\npillar: 柱%d\ntopic: 题%d\n---\nbody\n"
                     % ("card_%d" % domains.index(slug), domains.index(slug), slug,
                        domains.index(slug), domains.index(slug)))
    catalog = {
        "schema": "secondbrain-catalog/1",
        "tracks": [{"id": "t", "title": "T", "domains": list(domains)}],
        "domains": {slug: {"moc": moc_of[slug], "question": "Q" + slug} for slug in domains},
        "relation_types": ["similar_to", "depends_on", "follows"],
        "inverse_relations": {"similar_to": "similar_to", "depends_on": "follows",
                              "follows": "depends_on"},
        "domain_relations": {slug: ["similar_to"] for slug in domains},
    }
    for title, payload in taxonomies.items():
        d = {"title": title}
        d.update(payload)
        tpath = str(tmp_path / "20_MOCs" / "_pillars" / ("%s.yaml" % title))
        os.makedirs(os.path.dirname(tpath), exist_ok=True)
        with open(tpath, "w", encoding="utf-8") as fh:
            fh.write(yaml.safe_dump(d, allow_unicode=True, sort_keys=False))
    return v.read_snapshot(tmp_path, catalog=catalog)


def test_category_pairs_reads_curated_unoccupied_categories(tmp_path):
    snap = _snapshot(tmp_path, ['attention'], {'attention': 'A轴'},
        {'A轴': {'pillars': [{'name': 'P1', 'topics': ['t1', 't2']}]}})
    assert cmod.category_pairs(snap, 'attention') == {('P1', 't1'), ('P1', 't2')}
    assert (None, None) not in cmod.category_pairs(snap, 'attention')
    assert cmod.category_pairs(snap, 'absent') == set()


def test_taxonomy_packet_full_and_untruncated(tmp_path):
    moc_of = {"attention": "A轴", "transformer": "A轴"}
    tax = {"A轴": {"narrative": "叙事很长不截断" * 5, "source": "human",
                   "pillars": [{"name": "P1", "order": 1, "why": "原因",
                                "topics": ["t1", "t2"]}],
                   "bridges": [{"moc": "OTHER", "why": "为什么", "links": ["l1"]}]}}
    snap = _snapshot(tmp_path, ["attention", "transformer"], moc_of, tax)
    packet = cmod.taxonomy_packet(snap, ["attention", "transformer"])
    assert packet["domains"] == ["attention", "transformer"]
    assert packet["domain_mocs"] == {"attention": "A轴", "transformer": "A轴"}
    # same MOC shared by two domains -> one taxonomy entry, full content
    assert list(packet["taxonomies"].keys()) == ["A轴"]
    t = packet["taxonomies"]["A轴"]
    assert t["narrative"] == "叙事很长不截断" * 5
    assert t["pillars"][0]["why"] == "原因"
    assert t["bridges"][0]["links"] == ["l1"]

    # explicit None for a domain whose MOC has no loaded taxonomy
    packet2 = cmod.taxonomy_packet(snap, ["nope"])
    assert packet2["domain_mocs"]["nope"] is None
    assert packet2["taxonomies"] == {}


def test_taxonomy_packet_none_for_missing_taxonomy(tmp_path):
    moc_of = {"attention": "A轴", "unknown": "无此轴"}
    snap = _snapshot(tmp_path, ["attention"], moc_of, {})
    # a domain whose catalog MOC has no taxonomy file
    packet = cmod.taxonomy_packet(snap, ["known-not-in-catalog"])
    assert packet["domain_mocs"]["known-not-in-catalog"] is None

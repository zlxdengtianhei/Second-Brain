"""Tests for secondbrain.moc (MOC projection).

Covered here, per the moc-impl task:

* no-card-lost / full reachability on a synthetic 1781-card pile-up;
* the shared-MOC case (``attention``/``transformer`` share one MOC) including
  ``affected_domains`` expansion;
* a real, valid taxonomy fragment used read-only for classification;
* the four prerequisite edges decide learning order and other relation types
  do **not** affect it; cycles are reported but never drop a card;
* page size / topic size / visual-line budgets;
* pagination navigation is reachable for every generated page and card;
* stable (deterministic) bytes across repeated builds.

Only vault.Card/Snapshot + standard library are used (no vault writes).
"""
from __future__ import annotations

import re

from secondbrain.vault import Card, Snapshot
from secondbrain import moc


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def make_card(i, domain, pillar=None, topic=None, rels=None, path=None):
    fm = {"id": "c%d" % i, "title": "卡%d" % i, "domain": domain}
    if pillar is not None:
        fm["pillar"] = pillar
    if topic is not None:
        fm["topic"] = topic
    if rels:
        fm["relations"] = rels
    p = path or "10_Knowledge/%s/c%d.md" % (domain, i)
    return Card(id="c%d" % i, title="卡%d" % i, domain=domain, path=p,
                raw=b"", fm=fm, body="", sha256="s%05d" % i)


def catalog(domain_mocs):
    domains = {d: {"moc": m, "question": "?"} for d, m in domain_mocs.items()}
    return {
        "schema": "secondbrain-catalog/1",
        "tracks": [{"id": "t1", "title": "所有", "domains": list(domains)}],
        "domains": domains,
        "relation_types": ["depends_on", "follows", "required_by", "precedes",
                           "similar_to", "contrasts_with", "part_of",
                           "generalizes", "specializes"],
        "inverse_relations": {
            "depends_on": "required_by", "required_by": "depends_on",
            "follows": "precedes", "precedes": "follows",
            "similar_to": "similar_to", "contrasts_with": "contrasts_with",
            "part_of": "generalizes", "generalizes": "part_of",
            "specializes": "specializes",
        },
        "domain_relations": {},
    }


def snapshot(cards, taxonomies, cat):
    return Snapshot(root=None, cards=cards, registry={}, catalog=cat,
                    taxonomies=taxonomies, issues=[], fingerprint="fp")


def page_texts(proj):
    return {k: v.decode("utf-8") for k, v in proj.files.items()}


def placements_in(texts, path):
    """Count list-entry occurrences ``- [[path|...`` across all pages."""
    count = 0
    for t in texts.values():
        count += len(re.findall(re.escape("\n- [[" + path + "|"), t))
    return count


# Real, valid taxonomy fragment (names taken from 20_MOCs/_pillars/<title>.yaml).
REAL_FRAG = {
    "title": "Example Attention Map",
    "narrative": "从最小单位 token 出发，自注意力做信息路由，前馈网络做知识加工。",
    "pillars": [
        {"name": "示例支柱：位置之间的信息流", "order": 2,
         "why": "承接 QKV 与打分。",
         "topics": ["示例主题：自注意力", "示例主题：投影矩阵"]},
        {"name": "示例支柱：信息路由", "order": 1,
         "why": "承接输入表示与层堆叠。",
         "topics": ["示例主题：总览", "示例主题：残差连接"]},
    ],
}
REAL_PILLARS = [p["name"] for p in REAL_FRAG["pillars"]]
REAL_TOPICS = {p["name"]: list(p["topics"]) for p in REAL_FRAG["pillars"]}


# --------------------------------------------------------------------------
# learning_order
# --------------------------------------------------------------------------
def _cards_with_edges(edges, domains=("d",)):
    """edges is list of (src_id, dst_id, type); src learned before dst."""
    rels = {}
    for i, (src, dst, typ) in enumerate(edges):
        rels.setdefault(src, []).append({"target": dst, "type": typ})
    return [make_card(n, domains[0], rels=rels.get("c%d" % n))
            for n in range(1, max((int(a[1:]) for a, _, _ in edges), default=1) + 1)]


def test_learning_order_four_prerequisite_edges():
    # depends_on: c2 depends on c1 -> c1 first
    # follows:    c4 follows c3 -> c3 first
    # required_by:c6 is required by c5 -> c6 before c5
    # precedes:   c8 precedes c7 -> c8 before c7
    cards = _cards_with_edges([
        ("c2", "c1", "depends_on"),
        ("c4", "c3", "follows"),
        ("c6", "c5", "required_by"),
        ("c8", "c7", "precedes"),
    ])
    order, issues = moc.learning_order(cards)
    assert issues == []
    pos = {c.id: i for i, c in enumerate(order)}
    assert {c.id for c in order} == {"c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"}
    assert pos["c1"] < pos["c2"]
    assert pos["c3"] < pos["c4"]
    assert pos["c6"] < pos["c5"]
    assert pos["c8"] < pos["c7"]


def test_learning_order_other_relations_do_not_change_order():
    base = _cards_with_edges([("c2", "c1", "depends_on"), ("c4", "c3", "follows")])
    noisy = _cards_with_edges([
        ("c2", "c1", "depends_on"),
        ("c4", "c3", "follows"),
        ("c9", "c1", "similar_to"),   # unrelated target c9 (no prereq chain)
    ])
    order_base, _ = moc.learning_order(base)
    order_noisy, _ = moc.learning_order(noisy)
    # The relative order of the prerequisite-affected cards is identical despite
    # the added non-prerequisite (similar_to) edge.
    base_ids = [c.id for c in order_base]
    noisy_ids = [c.id for c in order_noisy]
    assert sorted(noisy_ids) == ["c%d" % i for i in range(1, 10)]
    for x in ("c1", "c2", "c3", "c4"):
        assert base_ids.index(x) == noisy_ids.index(x)


def test_learning_order_cycle_keeps_every_card():
    cards = _cards_with_edges([
        ("c1", "c2", "depends_on"),   # c2 before c1
        ("c2", "c1", "depends_on"),   # c1 before c2  -> 2-cycle
        ("c3", "c2", "follows"),      # c2 before c3
    ])
    order, issues = moc.learning_order(cards)
    assert sorted(c.id for c in order) == ["c1", "c2", "c3"]
    codes = [i["code"] for i in issues]
    assert "learning_order_cycle" in codes
    # cycle members retained
    cyc = [i for i in issues if i["code"] == "learning_order_cycle"][0]
    assert set(cyc["card_ids"]) == {"c1", "c2"}


# --------------------------------------------------------------------------
# build_mocs — shared MOC, real fragment
# --------------------------------------------------------------------------
def test_shared_moc_is_rebuilt_atomically():
    cat = catalog({"attention": "Example Attention Map",
                   "transformer": "Example Attention Map"})
    cards = {}
    cards.update({id(c) if False else "c%d" % i: make_card(
        i, "attention", REAL_PILLARS[0], REAL_TOPICS[REAL_PILLARS[0]][0])
        for i in range(1, 6)})
    for i in range(6, 9):
        cards["c%d" % i] = make_card(i, "transformer",
                                     REAL_PILLARS[1], REAL_TOPICS[REAL_PILLARS[1]][0])
    snap = snapshot(cards, {REAL_FRAG["title"]: REAL_FRAG}, cat)

    # Rebuild only the "attention" domain; the shared MOC must come back whole,
    # with both domains' cards covered.
    proj = moc.build_mocs(snap, affected_domains={"attention"})
    mocs = proj.metrics["mocs"]
    assert set(mocs) == {"Example Attention Map"}
    assert set(mocs["Example Attention Map"]["domains"]) == {"attention", "transformer"}
    covered = proj.metrics["covered"]
    assert len(covered) == 8
    assert all(cid in covered for cid in cards)


def test_mixed_domain_moc_covers_both_domains():
    cat = catalog({"attention": "Example Attention Map",
                   "transformer": "Example Attention Map"})
    cards = {}
    for i in range(1, 5):
        cards["c%d" % i] = make_card(i, "attention", REAL_PILLARS[0], "示例主题：自注意力")
    for i in range(5, 8):
        cards["c%d" % i] = make_card(i, "transformer", REAL_PILLARS[1], "示例主题：总览")
    snap = snapshot(cards, {REAL_FRAG["title"]: REAL_FRAG}, cat)
    proj = moc.build_mocs(snap)
    text = page_texts(proj)
    root = text["20_MOCs/Example Attention Map.md"]
    # Both domains appear on the root page and every card is placed once.
    assert "attention" in root and "transformer" in root
    for cid, c in cards.items():
        assert placements_in(text, c.path) == 1, cid


def test_real_taxonomy_fragment_read_only_classification():
    cat = catalog({"attention": "Example Attention Map",
                   "transformer": "Example Attention Map"})
    cards = {
        "c1": make_card(1, "attention", REAL_PILLARS[0], REAL_TOPICS[REAL_PILLARS[0]][0]),
        "c2": make_card(2, "attention", REAL_PILLARS[1], REAL_TOPICS[REAL_PILLARS[1]][1]),
        "c3": make_card(3, "attention", "不存在的支柱", "某主题"),  # illegal pillar
        "c4": make_card(4, "transformer"),                        # unclassified
    }
    snap = snapshot(cards, {REAL_FRAG["title"]: REAL_FRAG}, cat)
    proj = moc.build_mocs(snap)
    m = proj.metrics["mocs"]["Example Attention Map"]
    assert m["classified"] == 2
    assert m["invalid"] == 1
    assert m["unclassified"] == 1
    text = page_texts(proj)
    # The illegal card is listed in a residual page (its pillar is unknown).
    residual_text = "".join(t for k, t in text.items()
                            if k.endswith("/00待分类--01.md") or k.endswith("/00待分类--02.md"))
    assert "不存在的支柱" in residual_text
    # Illegal classifications are surfaced as issues (and never dropped).
    codes = [i["code"] for i in proj.issues]
    assert "invalid_classification" in codes
    assert "unclassified" in codes
    # 4 cards all reachable.
    assert sorted(proj.metrics["covered"]) == ["c1", "c2", "c3", "c4"]


# --------------------------------------------------------------------------
# build_mocs — 1781-card pile-up, no card lost, budgets, navigation
# --------------------------------------------------------------------------
def test_1781_card_pileup_no_card_lost_and_pagination():
    N = 1781
    cat = catalog({"big": "大 MOC", "attention": "Example Attention Map"})
    tax = {
        "大 MOC": {
            "title": "大 MOC",
            "narrative": "大堆积叙事。",
            "pillars": [{"name": "支柱A", "order": 1, "why": "w",
                         "topics": ["主题甲", "主题乙"]}]},
        REAL_FRAG["title"]: REAL_FRAG,
    }
    cards = {}
    # ~1500 unclassified, ~281 classified into 大 MOC's 支柱A.
    classified = 281
    for i in range(1, N + 1):
        if i <= classified:
            pillar, topic = "支柱A", "主题甲" if i % 2 else "主题乙"
            cards["c%d" % i] = make_card(i, "big", pillar, topic)
        else:
            cards["c%d" % i] = make_card(i, "big")
    snap = snapshot(cards, tax, cat)
    proj = moc.build_mocs(snap)
    text = page_texts(proj)

    # No card lost: everything covered and each card placed exactly once.
    assert len(proj.metrics["covered"]) == N
    assert len(set(proj.metrics["covered"])) == N
    for cid, c in cards.items():
        assert placements_in(text, c.path) == 1, cid

    big = proj.metrics["mocs"]["大 MOC"]
    assert big["unclassified"] == N - classified
    # Root page must NOT list all 1781 cards individually.
    root = text["20_MOCs/大 MOC.md"]
    for i in range(N - classified):
        cid = "c%d" % (classified + 1 + i)
        assert ("10_Knowledge/big/" + cid + ".md") not in root, "root must not list unclassified cards"

    # Page budgets on every page.
    for path, pg in zip(
            [p["path"] for p in big["pages"]],
            big["pages"]):
        assert pg["cards"] <= 120, path
        assert pg["max_topic_len"] <= 25, path
        assert pg["visual_lines"] <= 300, path

    # Every generated page is reachable from its own root's 分页索引.
    for mname, mm in proj.metrics["mocs"].items():
        root_text = text[mm["root"]]
        for pg in mm["pages"]:
            assert pg["path"] in root_text, "page %s missing from %s root index" % (pg["path"], mname)

    # Unclassified cards spread over several residual pages (page cap honoured).
    residual_pages = [pg for pg in big["pages"] if pg["label"].startswith("待分类")]
    assert len(residual_pages) >= (N - classified + 119) // 120


def test_stable_output_across_runs():
    cat = catalog({"attention": "Example Attention Map"})
    cards = {id(c) if False else "c%d" % i: make_card(
        i, "attention", REAL_PILLARS[0], REAL_TOPICS[REAL_PILLARS[0]][0])
        for i in range(1, 30)}
    for i in range(30, 60):
        cards["c%d" % i] = make_card(i, "attention")
    snap = snapshot(cards, {REAL_FRAG["title"]: REAL_FRAG}, cat)
    a = moc.build_mocs(snap)
    b = moc.build_mocs(snapshot(cards, {REAL_FRAG["title"]: REAL_FRAG}, catalog(
        {"attention": "Example Attention Map"})))
    assert a.files == b.files
    assert a.metrics == b.metrics


# --------------------------------------------------------------------------
# issues
# --------------------------------------------------------------------------
def test_non_prereq_relation_types_are_rendered_not_omitted():
    cat = catalog({"attention": "Example Attention Map"})
    cards = {
        "c1": make_card(1, "attention", REAL_PILLARS[0], "示例主题：自注意力"),
        "c2": make_card(2, "attention", REAL_PILLARS[0], "示例主题：自注意力",
                        [{"target": "c1", "type": "similar_to"},
                         {"target": "c1", "type": "contrasts_with"},
                         {"target": "c1", "type": "depends_on"}]),
    }
    snap = snapshot(cards, {REAL_FRAG["title"]: REAL_FRAG}, cat)
    proj = moc.build_mocs(snap)
    text = page_texts(proj)
    pill_page = [t for k, t in text.items()
                 if k.endswith("/02示例支柱：位置之间的信息流--01.md")]
    assert pill_page
    body = pill_page[0]
    for typ in ("similar_to", "contrasts_with", "depends_on"):
        assert ("%s→" % typ) in body, typ


def test_cycle_and_unknown_target_are_issues_without_losing_cards():
    cat = catalog({"attention": "Example Attention Map"})
    cards = {
        "c1": make_card(1, "attention", REAL_PILLARS[0], "示例主题：自注意力",
                        [{"target": "c2", "type": "depends_on"}]),
        "c2": make_card(2, "attention", REAL_PILLARS[0], "示例主题：自注意力",
                        [{"target": "c1", "type": "depends_on"}]),
        "c3": make_card(3, "attention", REAL_PILLARS[0], "示例主题：自注意力",
                        [{"target": "c999", "type": "relates_to"}]),
    }
    snap = snapshot(cards, {REAL_FRAG["title"]: REAL_FRAG}, cat)
    proj = moc.build_mocs(snap)
    codes = [i["code"] for i in proj.issues]
    assert "learning_order_cycle" in codes
    assert "unknown_relation_target" in codes
    # all three cards still reachable
    assert sorted(proj.metrics["covered"]) == ["c1", "c2", "c3"]


def test_affected_domains_unknown_reports_issue():
    cat = catalog({"attention": "Example Attention Map"})
    cards = {"c1": make_card(1, "attention", REAL_PILLARS[0], "示例主题：自注意力")}
    snap = snapshot(cards, {REAL_FRAG["title"]: REAL_FRAG}, cat)
    proj = moc.build_mocs(snap, affected_domains={"不存在的域"})
    codes = [i["code"] for i in proj.issues]
    assert "domain_not_in_catalog" in codes

"""Tests for tools/secondbrain/search.py (deterministic candidate retrieval).

Retrieval is lexical (BM25 + CJK unigrams/bigrams) and never claims semantic
dedup: a top score means token overlap, not that two notes are the same.
"""
from __future__ import annotations

import pytest

from secondbrain.search import SearchError, neighbors, search
from secondbrain.vault import Card, Snapshot


def card(cid, title, domain, body, rels=None, path=None):
    fm = {"id": cid, "title": title, "domain": domain}
    if rels is not None:
        fm["relations"] = rels
    p = path or "10_Knowledge/%s/%s.md" % (domain, cid)
    return Card(id=cid, title=title, domain=domain, path=p, raw=b"", fm=fm,
                body=body, sha256="s" + cid)


def snapshot(cards):
    return Snapshot(root=None, cards={c.id: c for c in cards}, registry={},
                    catalog={}, taxonomies={}, issues=[], fingerprint="fp")


BODIES = {
    "a": "Transformer 的示例主题：自注意力通过 QKV 投影完成信息路由，把 token 上下文互相传递。\n",
    "b": "The residual connection stabilizes the gradient across deep layers.\n",
    "c": "attention heads attend to different positional and semantic roles.\n",
}


def test_search_is_bounded_deterministic_and_dup_free():
    cards = [card("k%d" % i, ("卡%d" % i), "transformer", BODIES["a"] + " 变体" + str(i))
             for i in range(1, 40)]
    snap = snapshot(cards)
    out = search(snap, "自注意力 QKV", limit=12)
    assert len(out) <= 12
    ids = [r["id"] for r in out]
    assert len(ids) == len(set(ids))
    for r in out:
        assert set(r) == {"id", "title", "domain", "path", "score", "snippet"}
        assert len(r["snippet"]) <= 300
        assert isinstance(r["score"], float)
    # deterministic: same call twice, identical bytes
    assert search(snap, "自注意力 QKV", limit=12) == out


def test_search_zh_query_matches_body_and_titles():
    snap = snapshot([
        card("k1", "示例主题：自注意力", "transformer",
             "注意力把 token 的上下文信息路由到相关位置。\n", rels=[]),
        card("k2", "残差连接", "transformer",
             "残差连接稳定深层网络的梯度。\n", rels=[]),
    ])
    out = search(snap, "自注意力 信息路由")
    top = out[0] if out else None
    assert top is not None
    assert top["id"] == "k1"
    assert "信息路由" in top["snippet"]


def test_search_en_word_match():
    snap = snapshot([
        card("k1", "Residual connection", "transformer", BODIES["b"], rels=[]),
        card("k2", "多头注意力", "transformer", BODIES["a"], rels=[]),
    ])
    out = search(snap, "residual connection gradient")
    assert out and out[0]["id"] == "k1"


def test_search_no_match_returns_empty_and_rejects_bad_args():
    snap = snapshot([card("k1", "只卡", "transformer", BODIES["a"], rels=[])])
    assert search(snap, "甲乙丙丁戊") == []
    with pytest.raises(SearchError):
        search(snap, "q", limit=0)
    with pytest.raises(SearchError):
        search(snap, "q", limit="3")
    with pytest.raises(SearchError):
        search(None, "q")


def test_search_returns_body_evidence_via_snippet():
    body = ("第一章 概述。\n" * 3 + "这是唯一出现的关键片段：attention 把 query 和 key 做点积打分。\n"
            + "后续内容。" * 40)
    snap = snapshot([card("k1", "概述", "transformer", body, rels=[])])
    out = search(snap, "attention query key 点积")
    assert out and out[0]["id"] == "k1"
    snip = out[0]["snippet"]
    assert "attention" in snip and len(snip) <= 300


def test_neighbors_returns_full_packet_with_all_relation_edges():
    rels = [{"type": "depends_on", "target": "k0"},
            {"type": "similar_to", "target": "k2"}]
    snap = snapshot([
        card("k1", "主卡", "transformer", BODIES["a"], rels=rels),
        card("k0", "先修", "transformer", BODIES["b"], rels=[]),
        card("k2", "相似", "transformer", BODIES["c"], rels=[]),
    ])
    pkts = neighbors(snap, ["k1"])
    assert len(pkts) == 1
    assert pkts[0]["id"] == "k1"
    assert pkts[0]["body"] == BODIES["a"]
    assert set((e["type"], e["target"]) for e in pkts[0]["relations"]) == {
        ("depends_on", "k0"), ("similar_to", "k2")}


def test_neighbors_preserves_order_dedups_and_rejects_missing():
    snap = snapshot([card("k1", "A", "d", BODIES["a"], rels=[]),
                     card("k2", "B", "d", BODIES["b"], rels=[])])
    assert [p["id"] for p in neighbors(snap, ["k2", "k1", "k2"])] == ["k2", "k1"]
    with pytest.raises(SearchError):
        neighbors(snap, ["k1", "missing"])
    with pytest.raises(SearchError):
        neighbors(snap, "k1")

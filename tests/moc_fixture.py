"""A small in-memory vault that reaches every piece of generated MOC text.

Used by ``tests/test_page_language.py``. It imports only ``secondbrain.vault``
so the same snapshot can be built against older versions of the package when
the golden Chinese pages are recorded again (``tests/fixtures/pages/``).
"""
from __future__ import annotations

from secondbrain.vault import Card, Snapshot

ZH = {
    "map_a": "示例图谱甲", "map_b": "示例图谱乙", "track": "示例轨",
    "narrative": "从最小的例子出发，先看结构，再看用法。",
    "pillars": [("示例支柱：结构", 1, "先认识部件。", ["示例主题：部件", "示例主题：连接"]),
                ("示例支柱：用法", 2, "再看怎样组合。", ["示例主题：组合", "示例主题：拆分", "示例主题：组合"])],
    "bridge_why": "两张图共享部件。",
    "card": "示例卡%d", "pipe_card": "甲|乙 对照",
}
EN = {
    "map_a": "Example Map A", "map_b": "Example Map B", "track": "Example track",
    "narrative": "Start from the smallest example, look at the structure, then at its use.",
    "pillars": [("Structure", 1, "Know the parts first.", ["Parts", "Connections"]),
                ("Use", 2, "Then see how they combine.", ["Combining", "Splitting", "Combining"])],
    "bridge_why": "Both maps share the parts.",
    "card": "Example card %d", "pipe_card": "A|B compared",
}


def _card(n, domain, title, pillar=None, topic=None, rels=None):
    fm = {"id": "c%03d" % n, "title": title, "domain": domain}
    if pillar is not None:
        fm["pillar"] = pillar
    if topic is not None:
        fm["topic"] = topic
    if rels:
        fm["relations"] = rels
    return Card(id="c%03d" % n, title=title, domain=domain,
                path="10_Knowledge/%s/c%03d.md" % (domain, n),
                raw=b"", fm=fm, body="", sha256="s%05d" % n)


def snapshot(words=ZH):
    """Two maps: A (domains ``da`` and ``db``, with a taxonomy) and B (``dc``, without one).

    Map A has a pillar whose first topic needs two pages (30 cards > 25 per
    topic), a pillar that lists a topic twice with another between (the second
    heading is marked as continued), cards with missing and invalid classification, a title with
    ``|``, relations inside A and across to B, and bridges with and without a
    map name.
    """
    (p1, _, _, (t1, t2)), (p2, _, _, (t3, t4, _)) = words["pillars"]
    cards = []
    n = 0

    def add(domain, pillar=None, topic=None, rels=None, title=None):
        nonlocal n
        n += 1
        cards.append(_card(n, domain, title or words["card"] % n, pillar, topic, rels))

    for _ in range(30):
        add("da", p1, t1)
    add("db", p1, t2, rels=[{"type": "depends_on", "target": "c001"},
                            {"type": "similar_to", "target": "c002"},
                            {"type": "similar_to", "target": "c040"}])
    add("db", p1, t2, title=words["pipe_card"])
    add("da")                                   # pillar and topic missing
    add("da", topic=t1)                         # pillar missing
    add("db", pillar=p1)                        # topic missing
    add("da", "no such pillar", t1)             # invalid pillar
    add("db", p1, t3)                           # topic of another pillar
    add("db", p2, t3)                           # t3 is listed twice: its second
    add("db", p2, t4)                           # heading is marked as continued
    add("dc", rels=[{"type": "part_of", "target": "c031"}])
    add("dc")
    add("dc", title=words["pipe_card"] + " 2")
    taxonomy = {
        "title": words["map_a"], "narrative": words["narrative"],
        "pillars": [{"name": name, "order": order, "why": why, "topics": topics}
                    for name, order, why, topics in words["pillars"]],
        "bridges": [{"moc": words["map_b"], "why": words["bridge_why"],
                     "links": ["[[%s/%s]]" % ("20_MOCs", words["map_b"])]},
                    {"why": words["bridge_why"]}],
    }
    domains = {"da": {"moc": words["map_a"], "question": "?"},
               "db": {"moc": words["map_a"], "question": "?"},
               "dc": {"moc": words["map_b"], "question": "?"}}
    catalog = {"schema": "secondbrain-catalog/1",
               "tracks": [{"id": "t1", "title": words["track"], "domains": list(domains)}],
               "domains": domains,
               "relation_types": ["depends_on", "similar_to", "part_of", "generalizes"],
               "inverse_relations": {"depends_on": "required_by", "similar_to": "similar_to",
                                     "part_of": "generalizes", "generalizes": "part_of"},
               "domain_relations": {}}
    return Snapshot(root=None, cards={c.id: c for c in cards}, registry={}, catalog=catalog,
                    taxonomies={words["map_a"]: taxonomy}, issues=[], fingerprint="fp")

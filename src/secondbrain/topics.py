"""Topic sizes for the owner: which topics are past one map page, which are empty, and how a large one could split.

The limit is the map page's own (:data:`secondbrain.moc.MAX_TOPIC`, 25 cards), defined once. A split proposal
groups a large topic's cards by the relations among them (Louvain communities, networkx, fixed seed: the method
the H12 experiment judged reasonable for 9 of the old vault's 10 largest topics) and is shaped as decision D-08
asks: a group of fewer than :data:`MIN_GROUP` cards, or a card linked to no other card of the topic, goes to the
large group it has most links to, else it is listed as scattered; a group still past the limit says it needs a
second round. The proposal is a hint for `sb curate`; nothing here changes a card or the taxonomy.
"""
from __future__ import annotations

from collections import Counter

from .moc import MAX_TOPIC

#: Groups smaller than this are not proposed as topics of their own.
MIN_GROUP = 5
#: The seed of the Louvain run (the H12 experiment's first seed); the same vault gives the same proposal.
SEED = 20261108


def sizes(snapshot) -> Counter:
    """``{(domain, pillar, topic): cards}`` over the classified cards."""
    return Counter((c.domain, c.fm.get('pillar'), c.fm.get('topic')) for c in snapshot.cards.values()
                   if c.fm.get('pillar') and c.fm.get('topic'))


def _curated(snapshot) -> dict:
    """``{(domain, pillar, topic): moc}`` of every topic the taxonomies list."""
    out = {}
    for domain, meta in ((snapshot.catalog or {}).get('domains') or {}).items():
        moc = meta.get('moc') if isinstance(meta, dict) else None
        tax = snapshot.taxonomies.get(moc) or {}
        for pillar in tax.get('pillars') or []:
            if isinstance(pillar, dict):
                for topic in pillar.get('topics') or []:
                    if isinstance(topic, str):
                        out[(domain, pillar.get('name'), topic)] = moc
    return out


def hints(snapshot) -> dict:
    """What `sb status` says about topic sizes: topics past :data:`MAX_TOPIC` cards (largest first, with the
    command that curates their domain), curated topics no card uses, and how many cards are filed under a
    topic their taxonomy does not list."""
    counts = sizes(snapshot)
    curated = _curated(snapshot)
    oversize = [{'domain': d, 'pillar': p, 'topic': t, 'cards': n, 'limit': MAX_TOPIC,
                 'command': f'sb curate --domain {d}'}
                for (d, p, t), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if n > MAX_TOPIC]
    empty = [{'domain': d, 'pillar': p, 'topic': t} for (d, p, t) in sorted(curated) if not counts.get((d, p, t))]
    outside = sum(n for key, n in counts.items() if key not in curated)
    return {'oversize': oversize, 'empty': empty, 'outside_taxonomy': outside}


def _graph(snapshot, ids: set):
    import networkx as nx
    g = nx.Graph()
    g.add_nodes_from(sorted(ids))
    for cid in sorted(ids):
        for edge in snapshot.cards[cid].fm.get('relations') or []:
            target = edge.get('target') if isinstance(edge, dict) else None
            if target in ids and target != cid:
                g.add_edge(cid, target)
    return g


def split_proposal(snapshot, domain: str, pillar: str, topic: str, *, seed: int = SEED) -> dict:
    """How the cards of one topic could split, by their relations (see the module text).

    ``{"domain", "pillar", "topic", "cards", "seed", "groups": [{"size", "cards": [{id, title}],
    "needs_second_round"}], "scattered": [{id, title}]}``; ``groups`` is empty when no group reaches
    :data:`MIN_GROUP` cards. Without networkx: ``{"unavailable": ...}`` with the install hint.
    """
    try:
        from networkx.algorithms.community import louvain_communities
    except ImportError:
        from .labels import install_hint
        return {'domain': domain, 'pillar': pillar, 'topic': topic,
                'unavailable': f'split proposals need networkx ({install_hint("curate", "en")})'}
    ids = {cid for cid, c in snapshot.cards.items()
           if (c.domain, c.fm.get('pillar'), c.fm.get('topic')) == (domain, pillar, topic)}
    g = _graph(snapshot, ids)
    communities = sorted((sorted(c) for c in louvain_communities(g, seed=seed)), key=lambda c: (-len(c), c[0]))
    large = [c for c in communities if len(c) >= MIN_GROUP]
    small = [c for c in communities if len(c) < MIN_GROUP]
    members = [set(c) for c in large]
    scattered = []
    for group in small:  # to the large group it has most links to; ties go to the larger, earlier group
        links = [sum(1 for cid in group for other in g.neighbors(cid) if other in m) for m in members]
        best = max(range(len(members)), key=lambda i: (links[i], -i), default=None)
        if best is None or links[best] == 0:
            scattered += group
        else:
            members[best] |= set(group)

    def shown(cid):
        return {'id': cid, 'title': snapshot.cards[cid].title}
    groups = sorted(([shown(c) for c in sorted(m)] for m in members), key=lambda g: (-len(g), g[0]['id']))
    return {'domain': domain, 'pillar': pillar, 'topic': topic, 'cards': len(ids), 'seed': seed,
            'groups': [{'size': len(g), 'cards': g, 'needs_second_round': len(g) > MAX_TOPIC} for g in groups],
            'scattered': [shown(c) for c in sorted(scattered)]}


def proposals(snapshot, domains) -> list[dict]:
    """A split proposal for every topic of ``domains`` past :data:`MAX_TOPIC` cards, largest first."""
    wanted = set(domains)
    return [split_proposal(snapshot, h['domain'], h['pillar'], h['topic'])
            for h in hints(snapshot)['oversize'] if h['domain'] in wanted]

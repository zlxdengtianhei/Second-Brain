"""Cheap estimate of a diagram's rendered width, for use without a browser.

Obsidian's Mermaid lays flowcharts out with dagre (top to bottom, 50 px
between neighbours) and does not shrink flowcharts or sequence diagrams to
the reading width (useMaxWidth false), so one wider than about 700 px
overflows. State diagrams are scaled to fit (useMaxWidth true), so neither
this estimate nor the rendered-width gate applies to them (GATED_FORMS).
The real width comes from tools/mermaid_check; this estimate lets the
validator reject clear cases when no browser is available.

Model for flow and structure (fonts: -apple-system / PingFang): nodes get
layers by longest path after breaking cycles; a labelled edge puts its label
in the layer between its ends; an edge passing a layer adds a dummy of no
width; nodes sit 50 px apart, edge labels and dummies 20 px (dagre's
edgesep), 35 px between a node and an edge item; the widest layer sets the
width. Unbroken Latin words do not wrap. Checked on 3,210 vault flowcharts
of 4-9 nodes: median error -2 px, 10th percentile -45 px; of 346 wider than
700 px it misses 36 and it rejects 6 that fit; the batch path measures the
real width instead. Sequence diagrams follow Mermaid's own margin rule
(calculateActorMargins): median error 0 px on 958 rendered diagrams.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .ir import DiagramIR, clean_text

GATED_FORMS = ("flow", "structure", "sequence")  # forms Obsidian draws at full size
GAP = 50          # dagre nodesep in Obsidian's config; also the sequence actorMargin
EDGE_GAP = 20     # dagre edgesep: between edge labels and edges passing a layer
DUMMY = 0.0       # an edge passing a layer (dagre's dummy node has no width)
MARGIN = 16       # SVG margin, both sides
WRAP = 200        # Mermaid wraps label text at this width, between words or CJK characters
GROUP = 55        # subgraph frame and title
_PAD = {"step": 60, "actor": 60, "data": 60, "decision": 54, "store": 31, "input": 70, "output": 70,
        "start": 41, "end": 41}
_WRAP = {"decision": 224}


def text_px(s: str) -> float:
    """Approximate pixel width of a label at 16 px: CJK 16, space 4.4, other 8.6."""
    return sum(16 if ord(ch) > 0x2E80 else (4.4 if ch == " " else 8.6) for ch in clean_text(s))


_UNBROKEN = re.compile(r"[^\s\u2e80-\U0010ffff]+")


def wrapped_px(s: str, wrap: float = WRAP) -> float:
    """Width of a label after Mermaid wraps it at wrap px; a word longer than that does not break."""
    return min(text_px(s), max([wrap] + [text_px(w) for w in _UNBROKEN.findall(clean_text(s))]))


def _node_px(label: str, role: str) -> float:
    return wrapped_px(label, _WRAP.get(role, WRAP)) + _PAD.get(role, 60)


def _layers(ir: DiagramIR) -> dict[int, list[tuple[float, bool]]]:
    """Items per layer as (width, is_node): node layers at even indexes, the layers between at odd ones.

    An edge label sits in the middle layer of its edge; an edge passing a
    layer adds an item of no width (dagre's dummy node).
    """
    keys = [n.key for n in ir.nodes]
    outs: dict[str, list[str]] = defaultdict(list)
    for e in ir.edges:
        outs[e.src].append(e.dst)
    color: dict[str, int] = {}
    order: list[str] = []
    back: set[tuple[str, str]] = set()

    def visit(u: str) -> None:
        color[u] = 1
        for v in outs[u]:
            if color.get(v) == 1:
                back.add((u, v))
            elif v not in color:
                visit(v)
        color[u] = 2
        order.append(u)

    for k in keys:
        if k not in color:
            visit(k)
    rank = {k: 0 for k in keys}
    for u in reversed(order):
        for v in outs[u]:
            if (u, v) not in back and u != v:
                rank[v] = max(rank[v], rank[u] + 1)
    layer: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for n in ir.nodes:
        layer[2 * rank[n.key]].append((_node_px(n.label, n.role), True))
    for e in ir.edges:
        if e.src == e.dst:
            continue
        a, b = rank[e.src], rank[e.dst]
        if (e.src, e.dst) in back:
            a, b = b, a
        lo, hi = 2 * a, 2 * b
        mid = (lo + hi) // 2 if hi > lo else lo + 1
        if clean_text(e.label):
            layer[mid].append((wrapped_px(e.label), False))
        for r in range(lo + 1, hi):
            if r != mid or not clean_text(e.label):
                layer[r].append((DUMMY, False))
    return layer


def _row_px(items: list[tuple[float, bool]]) -> float:
    """Width of one layer: dagre puts nodesep between nodes, edgesep between edge items, the mean between a pair."""
    nodes = sum(1 for _, is_node in items if is_node)
    edges = len(items) - nodes
    seps = GAP * max(nodes - 1, 0) + EDGE_GAP * max(edges - 1, 0) + (GAP + EDGE_GAP) / 2 * (nodes > 0 < edges)
    return sum(w for w, _ in items) + seps


def _sequence(ir: DiagramIR) -> float:
    """Mermaid's own sequence layout (calculateActorMargins), with text_px for text widths.

    Participants are at least 150 px wide (label + 20). The gap after a
    participant is 50 px, or wider when the widest message or note between it
    and its right-hand neighbour needs it: text + 20 + 50 - half of each box.
    A note over one participant puts half its width into the gaps on either
    side; a note or self-message at either end can stick out of the boxes.
    Messages that skip a participant do not widen anything.
    """
    idx = {n.key: i for i, n in enumerate(ir.nodes)}
    k = len(ir.nodes)
    box = [max(150.0, text_px(n.label) + 20) for n in ir.nodes]
    need: dict[int, float] = defaultdict(float)  # widest text (+20) between participant i and i + 1
    left = right = 0.0
    for m in ir.messages:
        a, b = idx[m.src], idx[m.dst]
        w = text_px(m.label) + 20
        if m.note:
            if a == b:
                note = max(150.0, w)
                if b > 0:
                    need[b - 1] = max(need[b - 1], note / 2)
                if b < k - 1:
                    need[a] = max(need[a], note / 2)
                if a == 0:
                    left = max(left, (note - box[0]) / 2)
                if a == k - 1:
                    right = max(right, (note - box[-1]) / 2)
            else:
                if b > 0:
                    need[b - 1] = max(need[b - 1], w / 2)
                if b < k - 1:
                    need[a] = max(need[a], w / 2)
        elif a == b:
            need[a] = max(need[a], w / 2)
            if a == 0:
                left = max(left, (w - 20) / 2 - box[0] / 2)
            if a == k - 1:
                right = max(right, (w - 20) / 2 - box[-1] / 2)
        elif abs(a - b) == 1:
            i = min(a, b)
            need[i] = max(need[i], w)
    gaps = [max(float(GAP), need[i] + GAP - box[i] / 2 - box[i + 1] / 2) if i in need else float(GAP)
            for i in range(k - 1)]
    return sum(box) + sum(gaps) + 2 * GAP + max(left, 0.0) + max(right, 0.0)


def estimate_width(ir: DiagramIR) -> float | None:
    """Estimated rendered width in px; None for state diagrams, which Obsidian scales to fit."""
    if ir.form not in GATED_FORMS or not ir.nodes:
        return None
    if ir.form == "sequence":
        return _sequence(ir)
    width = max(_row_px(v) for v in _layers(ir).values())
    return width + MARGIN + (GROUP if ir.groups else 0)

"""Cheap estimate of a diagram's rendered width, for use without a browser.

Obsidian's Mermaid lays flowcharts out with dagre (top to bottom, 50 px
between neighbours) and does not shrink them to the reading width, so a
diagram wider than about 700 px overflows. The real width comes from
tools/mermaid_check; this estimate lets the validator reject clear cases
when no browser is available.

Model, fitted on 1,500 rendered diagrams (fonts: -apple-system / PingFang):
nodes get layers by longest path after breaking cycles; a labelled edge adds
its label in the layer between its ends; a long edge adds a narrow dummy to
every layer it crosses; the widest layer sets the width. Mean error on that
set: about -11 px (flow), -14 px (structure), 0 px (sequence); typical
error 5-6 %. It underestimates more often than it overestimates, so it
rejects few diagrams that would fit.
"""
from __future__ import annotations

from collections import defaultdict

from .ir import DiagramIR, clean_text

GAP = 50          # dagre nodesep and rank label spacing in Obsidian's config
MARGIN = 16       # SVG margin, both sides
WRAP = 200        # Mermaid wraps label text at this width
DUMMY = 20        # a long edge passing through a layer
GROUP = 55        # subgraph frame and title
_PAD = {"step": 60, "actor": 60, "data": 60, "decision": 54, "store": 31, "input": 70, "output": 70,
        "start": 41, "end": 41}
_WRAP = {"decision": 224}


def text_px(s: str) -> float:
    """Approximate pixel width of a label at 16 px: CJK 16, space 4.4, other 8.6."""
    return sum(16 if ord(ch) > 0x2E80 else (4.4 if ch == " " else 8.6) for ch in clean_text(s))


def _node_px(label: str, role: str) -> float:
    return min(text_px(label), _WRAP.get(role, WRAP)) + _PAD.get(role, 60)


def _layers(ir: DiagramIR) -> dict[int, list[float]]:
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
    layer: dict[int, list[float]] = defaultdict(list)
    for n in ir.nodes:
        layer[2 * rank[n.key]].append(_node_px(n.label, n.role))
    for e in ir.edges:
        if e.src == e.dst:
            continue
        a, b = rank[e.src], rank[e.dst]
        if (e.src, e.dst) in back:
            a, b = b, a
        lo, hi = 2 * a, 2 * b
        mid = (lo + hi) // 2 if hi > lo else lo + 1
        if clean_text(e.label):
            layer[mid].append(min(text_px(e.label), WRAP))
        for r in range(lo + 1, hi):
            if r != mid or not clean_text(e.label):
                layer[r].append(DUMMY)
    return layer


def _sequence(ir: DiagramIR) -> float:
    idx = {n.key: i for i, n in enumerate(ir.nodes)}
    actor = [max(150.0, min(text_px(n.label), WRAP) + 30) for n in ir.nodes]
    gaps = [float(GAP)] * max(len(actor) - 1, 0)
    for m in ir.messages:
        if m.note:
            continue
        a, b = sorted((idx[m.src], idx[m.dst]))
        need = text_px(m.label) + 20
        if a == b:
            if a < len(gaps):
                gaps[a] = max(gaps[a], need - actor[a] / 2)
            continue
        span = sum(actor[a + 1:b]) + sum(gaps[a:b]) + actor[a] / 2 + actor[b] / 2
        if need > span:
            for i in range(a, b):
                gaps[i] += (need - span) / (b - a)
    return sum(actor) + sum(gaps) + 90


def estimate_width(ir: DiagramIR) -> float | None:
    """Estimated rendered width in px; None for state diagrams, which Obsidian scales to fit."""
    if ir.form == "sequence":
        return _sequence(ir)
    if ir.form == "state" or not ir.nodes:
        return None
    width = max(sum(v) + GAP * (len(v) - 1) for v in _layers(ir).values())
    return width + MARGIN + (GROUP if ir.groups else 0)

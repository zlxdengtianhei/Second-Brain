"""Staleness marker: the diagram records which card text it was drawn from.

The first line of every generated Mermaid block is a comment such as

    %% diagram v1 props=P1,P2,P3 sha=1a2b3c4d

props lists the propositions the diagram cites; sha is the first 8 hex digits
of a SHA-256 over the card's essence and all of its propositions. When a later
update changes that text (including appending a proposition), the hash no
longer matches and the card goes back to the draw-or-skip step. The marker
travels with the card, so no separate ledger is needed.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .ir import Card, DiagramIR, clean_text

METHOD_VERSION = 1
_MARKER = re.compile(r"^%% diagram v(\d+) props=((?:E|P\d+)(?:,(?:E|P\d+))*|) sha=([0-9a-f]{8})\s*$")


def content_hash(card: Card) -> str:
    """8 hex digits over the essence and the ordered propositions (whitespace-normalised)."""
    parts = [f"E\t{clean_text(card.essence)}"]
    parts += [f"P{i}\t{clean_text(p)}" for i, p in enumerate(card.propositions, 1)]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:8]


def cited_refs(ir: DiagramIR) -> list[str]:
    """Every proposition or essence reference the IR cites, in E, P1, P2... order."""
    refs = {r for n in ir.nodes for r in n.p}
    refs |= {r for e in ir.links for r in e.p}
    refs |= {r for g in ir.groups for r in g.p}
    return sorted(refs, key=lambda r: (0, 0) if r == "E" else (1, int(r[1:])))


def marker(card: Card, ir: DiagramIR) -> str:
    return f"%% diagram v{METHOD_VERSION} props={','.join(cited_refs(ir))} sha={content_hash(card)}"


@dataclass(frozen=True)
class Marker:
    version: int
    props: tuple[str, ...]
    sha: str


def read_marker(mermaid: str) -> Marker | None:
    """The marker on the first non-empty line of a Mermaid block, if any."""
    for line in mermaid.splitlines():
        if line.strip():
            m = _MARKER.match(line.strip())
            if not m:
                return None
            props = tuple(p for p in m.group(2).split(",") if p)
            return Marker(int(m.group(1)), props, m.group(3))
    return None


@dataclass(frozen=True)
class Staleness:
    status: str  # "fresh", "stale" or "unmarked"
    reason: str = ""

    @property
    def needs_redraw(self) -> bool:
        return self.status != "fresh"


def check_staleness(mermaid: str, card: Card) -> Staleness:
    """Compare a diagram's marker with the card's current text.

    unmarked: no marker (a diagram not made by this generator); the caller
    decides whether such diagrams are judged again.
    """
    m = read_marker(mermaid)
    if m is None:
        return Staleness("unmarked", "no diagram marker on the first line")
    if m.version != METHOD_VERSION:
        return Staleness("stale", f"drawn with method v{m.version}, current is v{METHOD_VERSION}")
    missing = [p for p in m.props if p not in card.refs]
    if missing:
        return Staleness("stale", f"cites {', '.join(missing)}, which the card no longer has")
    if m.sha != content_hash(card):
        return Staleness("stale", "essence or propositions changed since the diagram was drawn")
    return Staleness("fresh")

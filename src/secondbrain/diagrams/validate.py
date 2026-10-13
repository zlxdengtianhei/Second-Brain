"""Mechanical checks V2-V8 and warnings W1-W2 on a parsed diagram IR.

V1 (shape) lives in ir.parse_ir. V9 (the emitted Mermaid parses in the target
Mermaid build) is a property of the generator, covered by the regression
corpus run through tools/mermaid_check.

Thresholds are the recommended defaults from the method write-up; they were
set from the distribution of existing diagrams and are not calibrated.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .ir import Card, DiagramIR, Issue, is_ref, parse_ir
from .text import display_width, overlap, title_match

RELATION_WORDS = frozenset({
    "part_of", "has_part", "depends_on", "required_by", "similar_to", "contrasts_with",
    "generalizes", "specializes", "follows", "precedes",
    "依赖", "属于", "相似", "类似", "对比", "推广", "泛化", "特化", "先修", "前置", "组成", "包含", "相关",
})


@dataclass(frozen=True)
class Limits:
    """Validator thresholds. Widths are in full-width character units (Latin counts half)."""

    size: dict[str, tuple[int, int]] = field(default_factory=lambda: {
        "flow": (4, 9), "structure": (4, 9), "state": (3, 8), "sequence": (2, 5)})
    grounding_min: float = 0.5
    node_label_max: float = 20
    decision_label_max: float | None = 12
    edge_label_max: float = 14
    message_max: float = 20
    min_messages: int = 3
    min_links: dict[str, int] = field(default_factory=lambda: {"structure": 3, "state": 3})
    labeled_links: tuple[str, ...] = ("structure", "sequence", "state")
    min_cited_propositions: int = 2
    exempt_labels: frozenset[str] = frozenset({
        "是", "否", "开始", "结束", "成功", "失败",
        "yes", "no", "start", "end", "success", "failure", "true", "false"})


DEFAULT_LIMITS = Limits()


@dataclass
class Report:
    ir: DiagramIR | None
    errors: list[Issue]
    warnings: list[Issue]

    @property
    def ok(self) -> bool:
        return not self.errors


def _items(ir: DiagramIR) -> list[tuple[str, str, str, tuple[str, ...], bool]]:
    """(kind, where, label, refs, citation_required) for every labelled element."""
    out = []
    for i, n in enumerate(ir.nodes):
        out.append(("decision" if n.role == "decision" else "node", f"nodes[{i}].label", n.label, n.p, True))
    if ir.form == "sequence":
        for i, m in enumerate(ir.messages):
            out.append(("message", f"messages[{i}].text", m.label, m.p, True))
    else:
        for i, e in enumerate(ir.edges):
            if e.label:
                out.append(("edge", f"edges[{i}].label", e.label, e.p, True))
    for i, g in enumerate(ir.groups):
        out.append(("group", f"groups[{i}].label", g.label, g.p, bool(g.p)))
    return out


def _has_cycle(keys: list[str], outs: dict[str, list[str]]) -> bool:
    color: dict[str, int] = {}

    def visit(u: str) -> bool:
        color[u] = 1
        for v in outs.get(u, ()):
            if color.get(v) == 1 or (v not in color and visit(v)):
                return True
        color[u] = 2
        return False

    return any(k not in color and visit(k) for k in keys)


def _gain(ir: DiagramIR, limits: Limits) -> str | None:
    """V5: why this diagram says more than a sentence would; None if it does not."""
    if ir.form == "flow":
        outs: dict[str, list] = defaultdict(list)
        indeg: dict[str, int] = defaultdict(int)
        for src, dst in {(e.src, e.dst) for e in ir.edges}:  # a repeated edge is not a merge
            indeg[dst] += 1
        for e in ir.edges:
            outs[e.src].append(e)
        for n in ir.nodes:
            o = outs.get(n.key, [])
            if n.role == "decision" and len({e.dst for e in o}) >= 2 and all(e.label for e in o):
                return None
        if any(v >= 2 for v in indeg.values()):
            return None
        if _has_cycle([n.key for n in ir.nodes], {k: [e.dst for e in v] for k, v in outs.items()}):
            return None
        return "flow is a plain chain: no decision with labelled branches, no merge, no loop; the text already says it"
    if ir.form == "sequence":
        real = [m for m in ir.messages if not m.note]
        if len(real) < limits.min_messages:
            return f"sequence has {len(real)} messages, needs at least {limits.min_messages}"
        return None
    need = limits.min_links.get(ir.form or "", 0)
    if len(ir.edges) < need:
        what = "relations between parts" if ir.form == "structure" else "transitions"
        return f"{ir.form} has {len(ir.edges)} {what}, needs at least {need}; a list says the same"
    return None


def check(ir: DiagramIR, card: Card, limits: Limits = DEFAULT_LIMITS) -> tuple[list[Issue], list[Issue]]:
    """Run V2-V8 and W1-W2 on a parsed draw IR. Returns (errors, warnings)."""
    errors: list[Issue] = []
    warnings: list[Issue] = []
    if ir.decision != "draw":
        return errors, warnings
    form = ir.form or ""

    lo, hi = limits.size[form]
    if not lo <= len(ir.nodes) <= hi:
        what = "participants" if form == "sequence" else ("states" if form == "state" else "nodes")
        errors.append(Issue("V2", f"{len(ir.nodes)} {what}; {form} needs {lo}-{hi} "
                                  f"(fewer restates the text, more should be split or written as text)"))

    valid = card.refs
    cited: set[str] = set()
    for kind, where, label, refs, required in _items(ir):
        if required or refs:
            bad = [r for r in refs if not is_ref(r) or r not in valid]
            if not refs:
                errors.append(Issue("V3", f"«{label}» cites no proposition", where))
            elif bad:
                errors.append(Issue("V3", f"«{label}» cites {bad}; this card has {sorted(valid, key=_ref_order)}", where))
            else:
                cited.update(refs)
                if label.strip().lower() not in limits.exempt_labels:
                    src = " ".join(card.source(r) for r in refs)
                    share = overlap(label, src)
                    if share is None or share < limits.grounding_min:
                        pct = "no" if share is None else f"{share:.0%}"
                        errors.append(Issue("V4", f"«{label}» shares {pct} wording with {list(refs)}; "
                                                  f"needs {limits.grounding_min:.0%}. Reuse the card's words", where))
        cap = {"node": limits.node_label_max, "group": limits.node_label_max,
               "decision": limits.decision_label_max or limits.node_label_max,
               "edge": limits.edge_label_max, "message": limits.message_max}[kind]
        w = display_width(label)
        if w > cap:
            errors.append(Issue("V6", f"«{label}» is {w:g} wide, limit {cap:g} for a {kind} label", where))

    gain = _gain(ir, limits)
    if gain:
        errors.append(Issue("V5", gain + "; consider skip"))

    if form in limits.labeled_links:
        for i, e in enumerate(ir.links):
            if not e.label:
                errors.append(Issue("V7", f"{form} links must say what they mean",
                                    f"{'messages' if form == 'sequence' else 'edges'}[{i}]"))
    roles = {n.key: n.role for n in ir.nodes}
    if form == "flow":
        for i, e in enumerate(ir.edges):
            if roles.get(e.src) == "decision" and not e.label:
                errors.append(Issue("V7", "edges leaving a decision must state their condition", f"edges[{i}]"))

    props_cited = {r for r in cited if r != "E"}
    if len(card.propositions) >= 2 and len(props_cited) < limits.min_cited_propositions:
        errors.append(Issue("V8", f"diagram covers {len(props_cited)} proposition(s) of {len(card.propositions)}; "
                                  f"a one-proposition diagram restates the text, consider skip"))

    for i, n in enumerate(ir.nodes):
        if title_match(n.label, list(card.related_titles)):
            warnings.append(Issue("W1", f"«{n.label}» names a related card; card-to-card links belong "
                                        f"in the relations view, not in this diagram", f"nodes[{i}].label"))
    for i, e in enumerate(ir.links):
        if e.label.strip().lower() in RELATION_WORDS:
            warnings.append(Issue("W2", f"«{e.label}» is a relation type, not what happens on this link",
                                  f"{'messages' if form == 'sequence' else 'edges'}[{i}]"))
    return errors, warnings


def _ref_order(r: str) -> tuple[int, int]:
    return (0, 0) if r == "E" else (1, int(r[1:]))


def validate(data: Any, card: Card | dict, limits: Limits = DEFAULT_LIMITS) -> Report:
    """Parse (V1) and check (V2-V8) one IR against its card."""
    if isinstance(card, dict):
        card = Card.from_dict(card)
    ir, issues = parse_ir(data)
    errors = [i for i in issues if i.is_error]
    warnings = [i for i in issues if not i.is_error]
    if ir is not None:
        e, w = check(ir, card, limits)
        errors += e
        warnings += w
    return Report(ir=ir, errors=errors, warnings=warnings)

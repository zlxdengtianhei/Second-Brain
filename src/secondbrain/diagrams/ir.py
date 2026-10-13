"""Card input, diagram IR and the structural check V1.

The model never writes Mermaid. It returns a small JSON object (the IR): a
draw/skip decision, a diagram form, and nodes and links that each cite the
card propositions they come from. This module defines that object, its JSON
Schema, and the parse step that turns untrusted JSON into typed values.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

FORMS = ("flow", "sequence", "state", "structure")
ROLES: dict[str, tuple[str, ...]] = {
    "flow": ("step", "decision", "store", "input", "output", "start", "end"),
    "structure": ("step", "store", "input", "output", "actor"),
    "state": ("state", "start", "end"),
    "sequence": ("actor",),
}
DEFAULT_ROLE = {"flow": "step", "structure": "step", "state": "state", "sequence": "actor"}
# What the arrows of a flow or structure diagram mean. A data diagram must show
# where data enters (role input) and leaves (role output).
ARROWS = ("control", "data", "relation")
DEFAULT_ARROWS = {"flow": "control", "structure": "relation"}
MESSAGE_STYLES = ("sync", "reply", "async")
_REF = re.compile(r"^(E|P[1-9][0-9]*)$")
_TOP_KEYS = {"decision", "reason", "form", "question", "arrows", "groups", "nodes", "edges", "messages"}
_NODE_KEYS = {"key", "label", "role", "p"}
_EDGE_KEYS = {"from", "to", "label", "p"}
_MESSAGE_KEYS = {"from", "to", "text", "p", "style", "note"}
_GROUP_KEYS = {"label", "members", "p"}


def clean_text(s: str) -> str:
    """Collapse all whitespace to single spaces and drop control characters."""
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cc" or ch.isspace())
    return " ".join(s.split())


@dataclass(frozen=True)
class Issue:
    """One validation finding. code is V1..V11 for errors, W0..W2 for warnings."""

    code: str
    message: str
    where: str = ""

    @property
    def is_error(self) -> bool:
        return self.code.startswith("V")

    def __str__(self) -> str:
        loc = f" [{self.where}]" if self.where else ""
        return f"{self.code}{loc} {self.message}"


@dataclass(frozen=True)
class Card:
    """The parts of a card the diagram step reads. Plain data, no vault objects."""

    title: str
    essence: str
    propositions: tuple[str, ...]
    kind: str | None = None
    domain: str | None = None
    related_titles: tuple[str, ...] = ()
    ref: str | None = None
    old_diagram: str | None = None  # "renders" or "broken" when the card already has a diagram

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Card":
        props = d.get("propositions", d.get("props")) or []
        return cls(
            title=str(d.get("title") or ""),
            essence=str(d.get("essence") or ""),
            propositions=tuple(str(p) for p in props),
            kind=d.get("kind"),
            domain=d.get("domain"),
            related_titles=tuple(str(t) for t in d.get("related_titles") or ()),
            ref=d.get("ref") or d.get("id") or d.get("path"),
            old_diagram=d.get("old_diagram") if d.get("old_diagram") in ("renders", "broken") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref, "title": self.title, "kind": self.kind, "domain": self.domain,
            "essence": self.essence, "propositions": list(self.propositions),
            "related_titles": list(self.related_titles),
            **({"old_diagram": self.old_diagram} if self.old_diagram else {}),
        }

    @property
    def refs(self) -> set[str]:
        """Citable references: E for the essence, P1..Pn for propositions."""
        return {"E"} | {f"P{i}" for i in range(1, len(self.propositions) + 1)}

    def source(self, ref: str) -> str:
        if ref == "E":
            return self.essence
        return self.propositions[int(ref[1:]) - 1]


@dataclass(frozen=True)
class Node:
    key: str
    label: str
    role: str
    p: tuple[str, ...] = ()


@dataclass(frozen=True)
class Link:
    """An edge (flow, structure, state) or a message (sequence)."""

    src: str
    dst: str
    label: str = ""
    p: tuple[str, ...] = ()
    style: str = "sync"
    note: bool = False


@dataclass(frozen=True)
class Group:
    label: str
    members: tuple[str, ...]
    p: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiagramIR:
    decision: str
    reason: str = ""
    form: str | None = None
    question: str = ""
    arrows: str | None = None
    nodes: tuple[Node, ...] = ()
    edges: tuple[Link, ...] = ()
    messages: tuple[Link, ...] = ()
    groups: tuple[Group, ...] = ()
    extra_keys: tuple[str, ...] = field(default=(), compare=False)

    @property
    def links(self) -> tuple[Link, ...]:
        return self.messages if self.form == "sequence" else self.edges


def _refs(v: Any, where: str, issues: list[Issue]) -> tuple[str, ...]:
    if v is None:
        return ()
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        issues.append(Issue("V1", "p must be a list of strings such as [\"P1\", \"E\"]", where))
        return ()
    return tuple(x.strip() for x in v)


def _text(v: Any, where: str, issues: list[Issue], required: bool) -> str:
    if v is None or v == "":
        if required:
            issues.append(Issue("V1", "missing text", where))
        return ""
    if not isinstance(v, str):
        issues.append(Issue("V1", "must be a string", where))
        return ""
    t = clean_text(v)
    if required and not t:
        issues.append(Issue("V1", "empty text", where))
    return t


def _extra(d: dict, allowed: set[str], where: str, warnings: list[Issue]) -> None:
    for k in sorted(set(d) - allowed):
        warnings.append(Issue("W0", f"unknown key {k!r} ignored", where))


def parse_ir(data: Any) -> tuple[DiagramIR | None, list[Issue]]:
    """V1: check the JSON shape and node references; return the typed IR or None.

    Returns (ir, issues). ir is None when any V1 error was found; issues then
    lists every V1 error found in one pass, plus W0 warnings for unknown keys.
    """
    issues: list[Issue] = []
    if not isinstance(data, dict):
        return None, [Issue("V1", "IR must be a JSON object")]
    _extra(data, _TOP_KEYS, "", issues)
    decision = data.get("decision")
    if decision not in ("draw", "skip"):
        return None, issues + [Issue("V1", 'decision must be "draw" or "skip"', "decision")]
    if decision == "skip":
        reason = _text(data.get("reason"), "reason", issues, required=True)
        if any(i.is_error for i in issues):
            return None, issues
        return DiagramIR(decision="skip", reason=reason), issues

    form = data.get("form")
    if form not in FORMS:
        return None, issues + [Issue("V1", f"form must be one of {', '.join(FORMS)}", "form")]
    question = _text(data.get("question"), "question", issues, required=True)
    arrows = data.get("arrows")
    if arrows is not None and form not in DEFAULT_ARROWS:
        issues.append(Issue("V1", f"arrows applies to flow and structure, not {form}", "arrows"))
    elif arrows is not None and (not isinstance(arrows, str) or arrows not in ARROWS):
        issues.append(Issue("V1", f"arrows must be one of {', '.join(ARROWS)}", "arrows"))
    arrows = arrows if isinstance(arrows, str) and arrows in ARROWS else DEFAULT_ARROWS.get(form)

    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        issues.append(Issue("V1", "nodes must be a non-empty list", "nodes"))
        raw_nodes = []
    nodes: list[Node] = []
    keys: set[str] = set()
    for i, n in enumerate(raw_nodes):
        w = f"nodes[{i}]"
        if not isinstance(n, dict):
            issues.append(Issue("V1", "node must be an object", w))
            continue
        _extra(n, _NODE_KEYS, w, issues)
        key = n.get("key")
        if not isinstance(key, str) or not key.strip():
            issues.append(Issue("V1", "key must be a non-empty string", w + ".key"))
            continue
        if key in keys:
            issues.append(Issue("V1", f"duplicate key {key!r}", w + ".key"))
        keys.add(key)
        role = n.get("role") or DEFAULT_ROLE[form]
        if role == "data" and form in ("flow", "structure"):
            issues.append(Issue("W0", "role data is retired: use store for stored data, input or output for "
                                      "data entering or leaving; drawn as a step", w + ".role"))
            role = "step"
        if not isinstance(role, str) or role not in ROLES[form]:
            issues.append(Issue("V1", f"role {role!r} not allowed in {form}; use one of {', '.join(ROLES[form])}", w + ".role"))
        label = _text(n.get("label"), w + ".label", issues, required=True)
        nodes.append(Node(key=key, label=label, role=role, p=_refs(n.get("p"), w + ".p", issues)))

    own, other = ("messages", "edges") if form == "sequence" else ("edges", "messages")
    if data.get(other):
        issues.append(Issue("V1", f"{form} uses {own}, not {other}", other))
    raw_links = data.get(own) or []
    if not isinstance(raw_links, list):
        issues.append(Issue("V1", f"{own} must be a list", own))
        raw_links = []
    links: list[Link] = []
    for i, e in enumerate(raw_links):
        w = f"{own}[{i}]"
        if not isinstance(e, dict):
            issues.append(Issue("V1", "link must be an object", w))
            continue
        _extra(e, _MESSAGE_KEYS if form == "sequence" else _EDGE_KEYS, w, issues)
        src, dst = e.get("from"), e.get("to")
        for end, v in (("from", src), ("to", dst)):
            if not isinstance(v, str):
                issues.append(Issue("V1", f"{end} must be one node key (a string)", f"{w}.{end}"))
            elif v not in keys:
                issues.append(Issue("V1", f"{end} refers to unknown node {v!r}", f"{w}.{end}"))
        if form == "sequence":
            label = _text(e.get("text"), w + ".text", issues, required=True)
            style = e.get("style") or "sync"
            if not isinstance(style, str) or style not in MESSAGE_STYLES:
                issues.append(Issue("V1", f"style must be one of {', '.join(MESSAGE_STYLES)}", w + ".style"))
            note = e.get("note", False)
            if not isinstance(note, bool):
                issues.append(Issue("V1", "note must be true or false", w + ".note"))
                note = False
            links.append(Link(str(src), str(dst), label, _refs(e.get("p"), w + ".p", issues), style, note))
        else:
            label = _text(e.get("label"), w + ".label", issues, required=False)
            links.append(Link(str(src), str(dst), label, _refs(e.get("p"), w + ".p", issues)))
            # Mermaid drops the labels when one node has several self-loops.
            if src == dst and sum(1 for x in links if x.src == x.dst == src) == 2:
                issues.append(Issue("V1", f"node {src!r} has more than one self-loop; merge them into one", w))

    groups: list[Group] = []
    raw_groups = data.get("groups") or []
    if raw_groups and form not in ("flow", "structure"):
        issues.append(Issue("V1", f"groups are only allowed in flow and structure, not {form}", "groups"))
        raw_groups = []
    if not isinstance(raw_groups, list):
        issues.append(Issue("V1", "groups must be a list", "groups"))
        raw_groups = []
    grouped: set[str] = set()
    for i, g in enumerate(raw_groups):
        w = f"groups[{i}]"
        if not isinstance(g, dict):
            issues.append(Issue("V1", "group must be an object", w))
            continue
        _extra(g, _GROUP_KEYS, w, issues)
        label = _text(g.get("label"), w + ".label", issues, required=True)
        members = g.get("members")
        if not isinstance(members, list) or not members:
            issues.append(Issue("V1", "members must be a non-empty list of node keys", w + ".members"))
            continue
        for m in members:
            if not isinstance(m, str):
                issues.append(Issue("V1", "members must be node keys (strings)", w + ".members"))
            elif m not in keys:
                issues.append(Issue("V1", f"member {m!r} is not a node key", w + ".members"))
            elif m in grouped:
                issues.append(Issue("V1", f"node {m!r} is in more than one group", w + ".members"))
            if isinstance(m, str):
                grouped.add(m)
        groups.append(Group(label, tuple(str(m) for m in members), _refs(g.get("p"), w + ".p", issues)))

    if any(i.is_error for i in issues):
        return None, issues
    ir = DiagramIR(
        decision="draw", form=form, question=question, arrows=arrows, nodes=tuple(nodes),
        edges=tuple(links) if form != "sequence" else (),
        messages=tuple(links) if form == "sequence" else (),
        groups=tuple(groups),
        extra_keys=tuple(sorted(set(data) - _TOP_KEYS)),
    )
    return ir, issues


def is_ref(s: str) -> bool:
    return bool(_REF.match(s))


_REFS_SCHEMA = {
    "type": "array",
    "items": {"type": "string", "pattern": "^(E|P[1-9][0-9]*)$"},
    "description": "Propositions this item rests on: P1..Pn, or E for the essence sentence.",
}

IR_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Card diagram IR",
    "description": "One card's diagram decision. Mermaid is generated from this by code.",
    "type": "object",
    "required": ["decision"],
    "properties": {
        "decision": {"enum": ["draw", "skip"]},
        "reason": {"type": "string", "description": "Required when decision is skip: which rule applied."},
        "form": {"enum": list(FORMS)},
        "question": {"type": "string", "description": "The one question this diagram answers."},
        "arrows": {"enum": list(ARROWS),
                   "description": "flow and structure only: what arrows mean. Default control for flow, "
                                  "relation for structure. data requires input and output nodes."},
        "groups": {
            "type": "array",
            "description": "Containment boxes (flow and structure only). A node is in at most one group.",
            "items": {
                "type": "object", "required": ["label", "members"], "additionalProperties": False,
                "properties": {"label": {"type": "string"},
                               "members": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                               "p": _REFS_SCHEMA},
            },
        },
        "nodes": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object", "required": ["key", "label", "p"], "additionalProperties": False,
                "properties": {"key": {"type": "string", "minLength": 1},
                               "label": {"type": "string", "minLength": 1},
                               "role": {"enum": sorted({r for rs in ROLES.values() for r in rs})},
                               "p": _REFS_SCHEMA},
            },
        },
        "edges": {
            "type": "array", "description": "flow, structure and state only.",
            "items": {
                "type": "object", "required": ["from", "to"], "additionalProperties": False,
                "properties": {"from": {"type": "string"}, "to": {"type": "string"},
                               "label": {"type": "string"}, "p": _REFS_SCHEMA},
            },
        },
        "messages": {
            "type": "array", "description": "sequence only. note: true draws a note over from (and to).",
            "items": {
                "type": "object", "required": ["from", "to", "text", "p"], "additionalProperties": False,
                "properties": {"from": {"type": "string"}, "to": {"type": "string"},
                               "text": {"type": "string", "minLength": 1}, "p": _REFS_SCHEMA,
                               "style": {"enum": list(MESSAGE_STYLES)}, "note": {"type": "boolean"}},
            },
        },
    },
    "allOf": [
        {"if": {"properties": {"decision": {"const": "skip"}}},
         "then": {"required": ["reason"]}},
        {"if": {"properties": {"decision": {"const": "draw"}}},
         "then": {"required": ["form", "question", "nodes"]}},
    ],
    "additionalProperties": False,
}

"""Deterministic Mermaid generation from a validated diagram IR.

The generator, not the model, owns Mermaid syntax:
- node ids are rewritten to n1..nk (group ids g1..gk), so no label or key can
  collide with a Mermaid keyword such as end, call, graph or style;
- every label goes through escape(): whitespace collapsed to one line, and
  characters that Mermaid treats as syntax replaced by numeric entities;
- flowcharts are always top to bottom, because short chains are skipped and
  long horizontal chains overflow the reading width;
- no colours, classDef or style lines, because Obsidian inverts diagrams in
  dark themes.
"""
from __future__ import annotations

import re

from .ir import DiagramIR, clean_text

# Characters that break or change parsing in at least one label position:
# '"' ends a quoted label, ';' ends a sequence or state statement, '<' and '>'
# start HTML, '`' and '$' switch on markdown and math labels, '\\' starts an
# escape such as '\\n' (a line break), '#' starts a Mermaid entity, '&' before
# a name and ';' is an HTML entity, and '%%' starts a comment or, as '%%{', a
# config directive anywhere in the source. State labels also apply markdown
# emphasis ('*', '_') and treat '::' as class syntax, so they escape more.
# One regex pass over the original text, so replacements are never re-escaped.
_ENTITIES = {ch: f"#{ord(ch)};" for ch in "#;`$\\&%*_:"}
_ENTITIES.update({'"': "#quot;", "<": "#lt;", ">": "#gt;"})
_HAZARD = re.compile(r'[#"<>;`$\\]|&(?=#?\w+;)|%(?=%)|(?<=%)%')
_HAZARD_STATE = re.compile(r'[#"<>;`$\\*_:]|&(?=#?\w+;)|%(?=%)|(?<=%)%')
# Before parsing, Mermaid drops the last character of any line matching
# /style.*:\S*#.*;/ or /classDef.*:\S*#.*;/, which would cut an entity short.
# Each emitted line carries at most one label and no ':', '#' or ';' outside
# it, so encoding the keyword's first letter in a label that matches keeps the
# line from matching.
_STYLE_BUG = re.compile(r"(style|classDef).*:\S*#.*;")
_KEYWORDS = re.compile(r"style|classDef")

# store is a cylinder (stored data only); input and output are parallelograms;
# the retired role data is drawn as a plain step.
_FLOW_SHAPES = {
    "step": ("[", "]"), "actor": ("[", "]"), "data": ("[", "]"), "decision": ("{", "}"),
    "store": ("[(", ")]"), "input": ("[/", "/]"), "output": ("[\\", "\\]"),
    "start": ("([", "])"), "end": ("([", "])"),
}
_ARROWS = {"sync": "->>", "reply": "-->>", "async": "-)"}


def escape(label: str, state: bool = False) -> str:
    """One-line, syntax-free label text that renders as the original characters.

    state=True escapes the extra characters that state diagram labels interpret.
    """
    pattern = _HAZARD_STATE if state else _HAZARD
    s = pattern.sub(lambda m: _ENTITIES[m.group()], clean_text(label))
    if _STYLE_BUG.search(s):
        s = _KEYWORDS.sub(lambda m: f"#{ord(m.group()[0])};{m.group()[1:]}", s)
    return s


def _require(ir: DiagramIR) -> None:
    if ir.decision != "draw" or ir.form is None:
        raise ValueError("only a draw IR can be emitted")
    for n in ir.nodes:
        if not clean_text(n.label):
            raise ValueError(f"node {n.key!r} has an empty label")


def _flow(ir: DiagramIR, ids: dict[str, str]) -> list[str]:
    nodes = {n.key: n for n in ir.nodes}

    def node_line(key: str, indent: str) -> str:
        n = nodes[key]
        a, b = _FLOW_SHAPES[n.role]
        return f'{indent}{ids[key]}{a}"{escape(n.label)}"{b}'

    lines = ["flowchart TB"]
    grouped: set[str] = set()
    for gi, g in enumerate(ir.groups, 1):
        lines.append(f'  subgraph g{gi} ["{escape(g.label)}"]')
        # Mermaid lays out a group with no edge to the outside on its own, left to right unless told
        lines.append("    direction TB")
        for k in g.members:
            lines.append(node_line(k, "    "))
            grouped.add(k)
        lines.append("  end")
    for n in ir.nodes:
        if n.key not in grouped:
            lines.append(node_line(n.key, "  "))
    for e in ir.edges:
        label = f'|"{escape(e.label)}"|' if clean_text(e.label) else ""
        lines.append(f"  {ids[e.src]} -->{label} {ids[e.dst]}")
    return lines


def _sequence(ir: DiagramIR, ids: dict[str, str]) -> list[str]:
    lines = ["sequenceDiagram"]
    lines += [f"  participant {ids[n.key]} as {escape(n.label)}" for n in ir.nodes]
    for m in ir.messages:
        if m.note:
            over = ids[m.src] if m.src == m.dst else f"{ids[m.src]},{ids[m.dst]}"
            lines.append(f"  Note over {over}: {escape(m.label)}")
        else:
            lines.append(f"  {ids[m.src]}{_ARROWS[m.style]}{ids[m.dst]}: {escape(m.label)}")
    return lines


def _state(ir: DiagramIR, ids: dict[str, str]) -> list[str]:
    lines = ["stateDiagram-v2"]
    lines += [f'  state "{escape(n.label, state=True)}" as {ids[n.key]}' for n in ir.nodes]
    lines += [f"  [*] --> {ids[n.key]}" for n in ir.nodes if n.role == "start"]
    for e in ir.edges:
        label = f" : {escape(e.label, state=True)}" if clean_text(e.label) else ""
        lines.append(f"  {ids[e.src]} --> {ids[e.dst]}{label}")
    lines += [f"  {ids[n.key]} --> [*]" for n in ir.nodes if n.role == "end"]
    return lines


def emit(ir: DiagramIR, marker: str | None = None) -> str:
    """Mermaid source for a draw IR. marker, if given, becomes the first line (a %% comment)."""
    _require(ir)
    ids = {n.key: f"n{i}" for i, n in enumerate(ir.nodes, 1)}
    if ir.form in ("flow", "structure"):
        lines = _flow(ir, ids)
    elif ir.form == "sequence":
        lines = _sequence(ir, ids)
    else:
        lines = _state(ir, ids)
    if marker:
        if "\n" in marker or not marker.startswith("%% "):
            raise ValueError("marker must be a single %% comment line")
        lines.insert(0, marker)
    return "\n".join(lines)


def fenced(mermaid: str) -> str:
    """The Markdown code block that goes under the card's diagram heading."""
    return f"```mermaid\n{mermaid}\n```"

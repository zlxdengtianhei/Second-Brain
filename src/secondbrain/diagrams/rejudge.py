"""The re-judge pool for existing diagrams, and writing the model's outcomes back.

Pool (from audit tiers): the review tier, the redraw tier, and delete-tier
cards that prune leaves for review (by default mechanism cards with three or
more propositions). Keep-tier cards are never in the pool, and the rest of
the delete tier and the LR to TB flips belong to prune.

Write-back rule for a pool card, given its final outcome (each action row
names the rule that applied):
- draw: the diagram section is replaced by the new Mermaid block;
- skip, card in a removed domain (the audit tiered it "domain", or the domain
  is listed in WritebackPolicy.remove_domains): the old diagram is removed;
- skip, delete_review card: the old diagram is removed, because the
  deterministic tier already judged it a restatement and the pool only gave
  the card a chance at a new one (WritebackPolicy.delete_review_on_skip,
  "remove" by default, "keep" treats these cards like the rest);
- skip, any other card: an old diagram that parses stays as it is; one that
  does not parse is removed;
- rewrite still pending, or no outcome: nothing changes.

Two refinements after the second pilot:
- a new diagram replaces an old one that renders only if it carries the
  card's key relation: it cites E and, on a card with 2+ propositions, at
  least 2 of them (read from its marker; the validator's V12 asks for the
  same, so the model gets its rewrite first). A weaker one is not written and
  the card follows the skip rules above, under a rule name that keeps the
  draw:weak prefix (draw:weak/skip:old_parses, draw:weak/skip:delete_review...);
- an old diagram that stays can be audited (see oldcheck): only remove +
  restates or remove + misleading removes it (rules audit:restates,
  audit:misleading, draw:weak/audit:...); any other row, including one
  without a category, keeps it (audit:unsure).

Two more after the third pilot:
- a new flowchart with the old flowchart's shape (same node and edge counts
  and degrees) and largely its words only redraws it: it is not written and
  the card follows the skip rules, rule draw:same_as_old/... (needs the
  graph that audit-tier records for each old flowchart);
- an old flowchart that stays and renders wider than the reading width with
  direction LR or RL is turned upright as prune does for the keep tier
  (action direction_tb, rule ...+tb); one that stays wide but cannot be
  turned is marked ...+wide.

Only the diagram section changes, byte for byte. Every write is guarded by the
file hash recorded at audit time, and nothing is written without write=True.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .audit import DEFAULT_FORMAT, CardFormat
from .oldcheck import REMOVE_CATEGORIES
from .prune import PrunePolicy, _diffstat, delete_section, direction_tb
from .staleness import read_marker
from .text import tokens
from .validate import strong_enough as _strong_refs


def select_pool(tiers: dict[str, dict[str, Any]], policy: PrunePolicy = PrunePolicy()) -> list[dict[str, Any]]:
    """Pool rows in ref order: ref, pool_reason, domain, tier_why, n_props, old_parses, sha256."""
    rows = []
    for ref in sorted(tiers):
        t = tiers[ref]
        if t["tier"] == "review":
            reason = "review"
        elif t["tier"] == "redraw":
            reason = "redraw"
        elif t["tier"] == "delete" and t["kind"] in policy.review_kinds and t["n_props"] >= policy.review_min_props:
            reason = "delete_review"
        else:
            continue
        rows.append({"ref": ref, "pool_reason": reason, "domain": t.get("domain"), "tier_why": t.get("why"),
                     "n_props": t.get("n_props", 0), "has_essence": t.get("has_essence", True),
                     "old_parses": bool(t["ok"]), "sha256": t["sha256"],
                     "old_direction": t.get("direction"), "old_width": t.get("width"), "old_graph": t.get("graph")})
    return rows


def pool_cards(pool: list[dict[str, Any]], cards: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Card records (the packets input format) for the pool, in pool order.

    old_diagram ("renders" or "broken") goes to the draw packet; old_mermaid
    is only for the old-diagram audit packet.
    """
    by_ref = {c["ref"]: c for c in cards}
    fields = ("ref", "title", "kind", "domain", "essence", "propositions", "related_titles")
    return [{**p, **{k: by_ref[p["ref"]][k] for k in fields},
             "old_diagram": "renders" if p["old_parses"] else "broken",
             "old_mermaid": (by_ref[p["ref"]].get("blocks") or [""])[0]} for p in pool]


def _section_bounds(lines: list[str], heading: str) -> tuple[int, int] | None:
    """[start, end) line range of '## <heading>' up to the next second-level heading; fences respected."""
    start, in_code = None, False
    for i, line in enumerate(lines):
        if not in_code and line.startswith("## "):
            if start is not None:
                return start, i
            if line[3:].strip() == heading:
                start = i
        if line.startswith("```"):
            in_code = not in_code
    return (start, len(lines)) if start is not None else None


def replace_section(text: str, mermaid: str, fmt: CardFormat = DEFAULT_FORMAT) -> str:
    """Put mermaid under the diagram heading, keeping every other byte.

    An existing section is replaced and keeps its trailing blank lines; a card
    without one gets it right after the propositions section.
    """
    lines = text.split("\n")
    block = [f"## {fmt.diagram}", "```mermaid", *mermaid.strip("\n").split("\n"), "```"]
    bounds = _section_bounds(lines, fmt.diagram)
    if bounds:
        start, end = bounds
        trailing = 0
        while end - trailing - 1 > start and not lines[end - trailing - 1].strip():
            trailing += 1
        return "\n".join(lines[:start] + block + [""] * trailing + lines[end:])
    after = _section_bounds(lines, fmt.propositions)
    if not after:
        raise ValueError(f"no '## {fmt.propositions}' section to put the diagram after")
    end = after[1]
    if end == len(lines):
        return "\n".join(lines + ([] if lines[-1] == "" else [""]) + block + [""])
    return "\n".join(lines[:end] + block + [""] + lines[end:])


@dataclass(frozen=True)
class WritebackPolicy:
    """What a skip does to an old diagram, beyond 'keep it if it parses'; when a kept one is turned upright."""

    delete_review_on_skip: str = "remove"  # or "keep"
    remove_domains: tuple[str, ...] = ()
    width_limit: int = 700
    same_words_min: float = 0.3  # share of label words a same-shape redraw keeps (pilots: 0.33-1.0)

    def __post_init__(self) -> None:
        if self.delete_review_on_skip not in ("remove", "keep"):
            raise ValueError("delete_review_on_skip must be 'remove' or 'keep'")


def _skip_rule(p: dict[str, Any], policy: WritebackPolicy) -> tuple[str, str]:
    """(action, rule) for a pool card whose final outcome is skip."""
    if p.get("tier_why") == "domain" or p.get("domain") in policy.remove_domains:
        return "remove", "skip:domain"
    if p["pool_reason"] == "delete_review" and policy.delete_review_on_skip == "remove":
        return "remove", "skip:delete_review"
    return ("keep_old", "skip:old_parses") if p["old_parses"] else ("remove", "skip:old_broken")


def strong_enough(mermaid: str, n_props: int, has_essence: bool = True) -> bool:
    """Whether a new diagram may replace an old one that renders.

    It must cite E (when the card has an essence) and at least 2 propositions
    (or all of them, if the card has fewer), read from its marker line; the
    rule is validate.strong_enough, which V12 applies before this.
    """
    m = read_marker(mermaid)
    return m is not None and _strong_refs(m.props, n_props, has_essence)


_NODE = re.compile(r'^\s+(n\d+)(?:\[\(|\(\[|\[/|\[\\|\[|\{)"(.*)"')
_EDGE = re.compile(r'^\s+(n\d+) -->(?:\|".*"\|)? (n\d+)\s*$')


def graph_of_mermaid(mermaid: str) -> dict[str, Any] | None:
    """Node labels and edges of a flowchart written by emit.emit(); None for other forms."""
    lines = mermaid.splitlines()
    if not any(line.startswith("flowchart") for line in lines):
        return None
    index: dict[str, int] = {}
    labels: list[str] = []
    edges = []
    for line in lines:
        e = _EDGE.match(line)
        if e:
            edges.append((e.group(1), e.group(2)))
            continue
        n = _NODE.match(line)
        if n:
            index[n.group(1)] = len(labels)
            labels.append(n.group(2))
    return {"labels": labels, "edges": [[index[a], index[b]] for a, b in edges if a in index and b in index]}


def _shape(graph: dict[str, Any]) -> tuple:
    pairs = {tuple(e) for e in graph["edges"]}
    deg = [[0, 0] for _ in graph["labels"]]
    for a, b in pairs:
        deg[a][1] += 1
        deg[b][0] += 1
    return len(graph["labels"]), len(pairs), sorted(map(tuple, deg))


def same_structure(mermaid: str, old_graph: dict[str, Any] | None, min_words: float = 0.3) -> bool:
    """Whether a new flowchart only redraws the old one.

    Same shape (node and distinct edge counts, and the in/out degree of every
    node, which an isomorphic redraw keeps) and at least min_words of the two
    diagrams' label words shared (Jaccard over tokens()), so a diagram with a
    common small shape but other content is not caught.
    """
    new = graph_of_mermaid(mermaid)
    if new is None or not old_graph or not old_graph.get("labels") or _shape(new) != _shape(old_graph):
        return False
    a, b = tokens(" ".join(new["labels"])), tokens(" ".join(old_graph["labels"]))
    return bool(a | b) and len(a & b) / len(a | b) >= min_words


def audit_candidates(pool: list[dict[str, Any]], outcomes: Iterable[dict[str, Any]] | None = None,
                     policy: WritebackPolicy = WritebackPolicy()) -> list[dict[str, Any]]:
    """Pool rows whose old diagram would stay: the cards the old-diagram audit is for.

    With outcomes: the rows planned as keep_old (or direction_tb, which keeps
    it upright). Without: every row that a skip would leave as keep_old, so
    the audit can run beside the draw step.
    """
    if outcomes is not None:
        keep = {a["ref"] for a in plan(pool, outcomes, policy) if a["action"] in ("keep_old", "direction_tb")}
        return [p for p in pool if p["ref"] in keep]
    return [p for p in pool if _skip_rule(p, policy)[0] == "keep_old"]


def plan(pool: list[dict[str, Any]], outcomes: Iterable[dict[str, Any]],
         policy: WritebackPolicy = WritebackPolicy(),
         audits: Iterable[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """One action per pool card plus a refusal per outcome outside the pool.

    Later outcomes for the same ref replace earlier ones, so pass round 0
    before the rewrite round. audits are old-diagram audit outcomes; they only
    act on cards whose old diagram would otherwise stay.
    """
    final: dict[str, dict[str, Any]] = {}
    for o in outcomes:
        final[o["ref"]] = o
    verdicts = {v["ref"]: v for v in audits}
    in_pool = {p["ref"] for p in pool}
    actions = []
    for p in pool:
        o = final.get(p["ref"])
        row = {"ref": p["ref"], "pool_reason": p["pool_reason"], "sha256": p["sha256"]}
        if o is None or o.get("status") not in ("draw", "skip"):
            actions.append({**row, "action": "pending", "rule": "pending",
                            "status_in": o.get("status") if o else None})
        elif o["status"] == "draw" and o.get("mermaid") and (not p["old_parses"] or (
                strong_enough(o["mermaid"], p.get("n_props", 0), p.get("has_essence", True))
                and not same_structure(o["mermaid"], p.get("old_graph"), policy.same_words_min))):
            actions.append({**row, "action": "replace", "rule": "draw", "mermaid": o["mermaid"]})
        else:
            action, rule = _skip_rule(p, policy)
            prefix = ""
            if o["status"] == "draw":
                weak = not strong_enough(o.get("mermaid") or "", p.get("n_props", 0), p.get("has_essence", True))
                prefix = "draw:weak/" if weak else "draw:same_as_old/"
            v = verdicts.get(p["ref"])
            if action == "keep_old" and v:
                category = v.get("category") if v.get("category") in REMOVE_CATEGORIES + ("useful",) else "unsure"
                rule = f"audit:{category}"
                if v.get("verdict") == "remove" and category in REMOVE_CATEGORIES:
                    action = "remove"
            if action == "keep_old" and (p.get("old_width") or 0) > policy.width_limit:
                if p.get("old_direction") in ("LR", "RL"):
                    action, rule = "direction_tb", rule + "+tb"
                else:
                    rule += "+wide"
            actions.append({**row, "action": action, "rule": prefix + rule, "reason": (v or o).get("reason", "")})
    for ref in sorted(set(final) - in_pool):
        actions.append({"ref": ref, "action": "not_in_pool", "rule": "not_in_pool",
                        "status_in": final[ref].get("status")})
    return actions


def summary(actions: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Counts of rule (outcome and what it does) per pool reason, for the plan output."""
    out: dict[str, Counter] = {}
    for a in actions:
        out.setdefault(a.get("pool_reason", "not_in_pool"), Counter())[f"{a['rule']}→{a['action']}"] += 1
    return {reason: dict(sorted(c.items())) for reason, c in sorted(out.items())}


WRITES = ("replace", "remove", "direction_tb")  # actions that change a card


def apply(vault: Path, actions: list[dict[str, Any]], write: bool = False,
          fmt: CardFormat = DEFAULT_FORMAT) -> list[dict[str, Any]]:
    """Carry out (or, with write=False, preview) replace, remove and direction_tb actions; one manifest row each."""
    rows = []
    for a in actions:
        row = {k: a[k] for k in ("ref", "pool_reason", "rule", "action") if k in a}
        if a["action"] not in WRITES:
            rows.append({**row, "status": "unchanged"})
            continue
        path = Path(vault) / a["ref"]
        if not path.is_file():
            rows.append({**row, "status": "missing"})
            continue
        before = path.read_bytes().decode("utf-8")
        if hashlib.sha256(before.encode("utf-8")).hexdigest() != a["sha256"]:
            rows.append({**row, "status": "conflict"})
            continue
        try:
            if a["action"] == "replace":
                after = replace_section(before, a["mermaid"], fmt)
            elif a["action"] == "remove":
                after = delete_section(before, fmt.diagram)
            else:
                after = direction_tb(before, fmt.diagram)
        except ValueError as e:
            rows.append({**row, "status": "error", "error": str(e)})
            continue
        added, removed = _diffstat(before, after)
        if write:
            path.write_bytes(after.encode("utf-8"))
        rows.append({**row, "status": "written" if write else "planned", "lines_added": added,
                     "lines_removed": removed, "sha256_after": hashlib.sha256(after.encode("utf-8")).hexdigest()})
    return rows

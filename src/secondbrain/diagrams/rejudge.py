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

Only the diagram section changes, byte for byte. Every write is guarded by the
file hash recorded at audit time, and nothing is written without write=True.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .audit import DEFAULT_FORMAT, CardFormat
from .prune import PrunePolicy, _diffstat, delete_section


def select_pool(tiers: dict[str, dict[str, Any]], policy: PrunePolicy = PrunePolicy()) -> list[dict[str, Any]]:
    """Pool rows in ref order: ref, pool_reason, domain, tier_why, old_parses, sha256."""
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
                     "old_parses": bool(t["ok"]), "sha256": t["sha256"]})
    return rows


def pool_cards(pool: list[dict[str, Any]], cards: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Card records (the packets input format) for the pool, in pool order."""
    by_ref = {c["ref"]: c for c in cards}
    fields = ("ref", "title", "kind", "domain", "essence", "propositions", "related_titles")
    return [{**{k: by_ref[p["ref"]][k] for k in fields}, "pool_reason": p["pool_reason"]} for p in pool]


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
    """What a skip does to an old diagram, beyond 'keep it if it parses'."""

    delete_review_on_skip: str = "remove"  # or "keep"
    remove_domains: tuple[str, ...] = ()

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


def plan(pool: list[dict[str, Any]], outcomes: Iterable[dict[str, Any]],
         policy: WritebackPolicy = WritebackPolicy()) -> list[dict[str, Any]]:
    """One action per pool card plus a refusal per outcome outside the pool.

    Later outcomes for the same ref replace earlier ones, so pass round 0
    before the rewrite round.
    """
    final: dict[str, dict[str, Any]] = {}
    for o in outcomes:
        final[o["ref"]] = o
    in_pool = {p["ref"] for p in pool}
    actions = []
    for p in pool:
        o = final.get(p["ref"])
        row = {"ref": p["ref"], "pool_reason": p["pool_reason"], "sha256": p["sha256"]}
        if o is None or o.get("status") not in ("draw", "skip"):
            actions.append({**row, "action": "pending", "rule": "pending",
                            "status_in": o.get("status") if o else None})
        elif o["status"] == "draw" and o.get("mermaid"):
            actions.append({**row, "action": "replace", "rule": "draw", "mermaid": o["mermaid"]})
        else:
            action, rule = _skip_rule(p, policy)
            actions.append({**row, "action": action, "rule": rule, "reason": o.get("reason", "")})
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


def apply(vault: Path, actions: list[dict[str, Any]], write: bool = False,
          fmt: CardFormat = DEFAULT_FORMAT) -> list[dict[str, Any]]:
    """Carry out (or, with write=False, preview) replace and remove actions; one manifest row per action."""
    rows = []
    for a in actions:
        row = {k: a[k] for k in ("ref", "pool_reason", "rule", "action") if k in a}
        if a["action"] not in ("replace", "remove"):
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
            after = (replace_section(before, a["mermaid"], fmt) if a["action"] == "replace"
                     else delete_section(before, fmt.diagram))
        except ValueError as e:
            rows.append({**row, "status": "error", "error": str(e)})
            continue
        added, removed = _diffstat(before, after)
        if write:
            path.write_bytes(after.encode("utf-8"))
        rows.append({**row, "status": "written" if write else "planned", "lines_added": added,
                     "lines_removed": removed, "sha256_after": hashlib.sha256(after.encode("utf-8")).hexdigest()})
    return rows

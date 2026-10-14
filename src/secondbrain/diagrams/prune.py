"""Apply an audit to a vault: remove restating diagrams, turn wide kept ones upright.

Actions come from audit tiers and the old-diagram audit (oldcheck):
- delete tier: remove the whole diagram section when the old-diagram audit
  says restates or misleading; keep it when the audit keeps it (then it is
  treated like the keep tier below); without a verdict nothing changes
  (action pending). The tier alone removes only a card in a deleted domain
  and a diagram that does not parse even after audit-repair. Cards the
  method should judge again first (by default mechanism cards with three or
  more propositions) stay for the review pool;
- keep tier: a flowchart wider than the reading width and laid out left to
  right (LR or RL) gets direction TB;
- keep tier, diagram that only renders after audit-repair: the repaired
  source replaces the broken one (turned TB as well when it is wide and LR).

With renders of the turned diagrams (settle), one that TB makes wider than
it was stays as it is: the turn is there to make it narrower.

Every write is guarded by the file hash recorded at audit time: a card edited
since the audit is reported as conflict and left alone. Without apply=True
nothing is written; the manifest shows what would change.
"""
from __future__ import annotations

import difflib
import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .audit import DEFAULT_FORMAT, CardFormat
from .oldcheck import REMOVE_CATEGORIES

_HEADER = re.compile(r"^(\s*(?:flowchart|graph)\s+)(LR|RL)\b")


@dataclass(frozen=True)
class PrunePolicy:
    review_kinds: tuple[str, ...] = ("mechanism",)
    review_min_props: int = 3
    width_limit: int = 700


def plan(tiers: dict[str, dict[str, Any]], policy: PrunePolicy = PrunePolicy(),
         audits: Iterable[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """Actions for the cards in an audit report's 'cards' map, in ref order; audits are old-diagram audit outcomes."""
    verdicts = {v["ref"]: v for v in audits}
    actions = []
    for ref in sorted(tiers):
        t = tiers[ref]
        row = {"ref": ref, "sha256": t["sha256"]}
        why = t["why"]
        if t["tier"] == "delete":
            if t["kind"] in policy.review_kinds and t["n_props"] >= policy.review_min_props:
                continue
            if why == "domain" or not t.get("ok"):  # deleted domain; broken even after audit-repair
                actions.append({**row, "action": "delete_section", "why": why})
                continue
            v = verdicts.get(ref)
            if v is None:  # no diagram is removed without the audit
                actions.append({**row, "action": "pending", "why": f"{why}; no audit"})
                continue
            category = v.get("category") if v.get("category") in REMOVE_CATEGORIES + ("useful",) else "unsure"
            why = f"{why}; audit:{category}"
            if v.get("verdict") == "remove" and category in REMOVE_CATEGORIES:
                actions.append({**row, "action": "delete_section", "why": why})
                continue
        elif t["tier"] != "keep":
            continue
        # the keep tier, and delete-tier diagrams the audit keeps
        kept = f"{why}; " if t["tier"] == "delete" else ""
        wide = t.get("direction") in ("LR", "RL") and (t.get("width") or 0) > policy.width_limit
        if t.get("repair"):
            actions.append({**row, "action": "repair", "why": kept + "repaired: " + ", ".join(t["repair"]["fixes"]),
                            "mermaid": t["repair"]["text"], "flip": wide})
        elif wide:
            actions.append({**row, "action": "direction_tb", "why": f"{kept}width {t['width']}"})
        elif kept:
            actions.append({**row, "action": "keep", "why": why})
    return actions


def settle(actions: list[dict[str, Any]], checks: dict[str, dict[str, Any]], tiers: dict[str, dict[str, Any]],
           width_limit: int = 700) -> tuple[list[dict[str, Any]], Counter]:
    """Settle the actions from renders of the diagrams as they would be written (rejudge.kept_sources).

    A turn that makes the diagram wider than its old width is dropped: direction_tb becomes keep (nothing
    written), repair keeps the old direction. A diagram still wider than width_limit says so in why.
    Returns the actions and counts of not_turned and still_wide.
    """
    out, n = [], Counter()
    for a in actions:
        c = checks.get(a["ref"]) or {}
        turned = a["action"] == "direction_tb" or (a["action"] == "repair" and a.get("flip"))
        old = tiers.get(a["ref"], {}).get("width")
        if c.get("ok") and turned and old and (c.get("width") or 0) > old:
            n["not_turned"] += 1
            a = {**a, "why": f"{a['why']}; TB {c['width']} px is wider than {old} px, not turned"}
            a = {**a, "action": "keep"} if a["action"] == "direction_tb" else {**a, "flip": False}
        elif c.get("ok") and (c.get("width") or 0) > width_limit:
            n["still_wide"] += 1
            a = {**a, "why": f"{a['why']}; still {c['width']} px wide"}
        out.append(a)
    return out, n


def replace_block(text: str, source: str, heading: str, flip: bool = False) -> str:
    """Put source into the first Mermaid block of the section, keeping the fence lines and everything else."""
    lines = text.split("\n")
    in_section = False
    for i, line in enumerate(lines):
        if line.startswith("## "):
            in_section = line[3:].strip() == heading
            continue
        if in_section and line.startswith("```mermaid"):
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("```"):
                    out = "\n".join(lines[:i + 1] + source.strip("\n").split("\n") + lines[j:])
                    return direction_tb(out, heading) if flip else out
            raise ValueError(f"unclosed Mermaid block in '## {heading}'")
    raise ValueError(f"no Mermaid block in '## {heading}'")


def delete_section(text: str, heading: str) -> str:
    """Remove '## <heading>' and everything up to the next second-level heading (fences respected)."""
    lines = text.split("\n")
    out, skipping, in_code, found = [], False, False, False
    for line in lines:
        fence = line.startswith("```")
        if not in_code and line.startswith("## "):
            skipping = line[3:].strip() == heading and not found
            found = found or skipping
        if fence:
            in_code = not in_code
        if not skipping:
            out.append(line)
    if not found:
        raise ValueError(f"no '## {heading}' section")
    return "\n".join(out)


def direction_tb(text: str, heading: str) -> str:
    """Switch the first Mermaid block in the section from LR/RL to TB."""
    lines = text.split("\n")
    in_section = in_block = False
    for i, line in enumerate(lines):
        if not in_block and line.startswith("## "):
            in_section = line[3:].strip() == heading
            continue
        if in_section and line.startswith("```mermaid"):
            in_block = True
            continue
        if in_block:
            if line.startswith("```"):
                break
            if not line.strip() or line.strip().startswith("%%"):
                continue
            m = _HEADER.match(line)
            if not m:
                raise ValueError(f"first Mermaid line is not a LR/RL flowchart header: {line!r}")
            lines[i] = m.group(1) + "TB" + line[m.end():]
            return "\n".join(lines)
    raise ValueError(f"no Mermaid block in '## {heading}'")


def _diffstat(before: str, after: str) -> tuple[int, int]:
    added = removed = 0
    for d in difflib.ndiff(before.split("\n"), after.split("\n")):
        if d.startswith("+ "):
            added += 1
        elif d.startswith("- "):
            removed += 1
    return added, removed


def apply(vault: Path, actions: list[dict[str, Any]], write: bool = False,
          fmt: CardFormat = DEFAULT_FORMAT) -> list[dict[str, Any]]:
    """Carry out (or, with write=False, preview) the actions; one manifest row per action."""
    rows = []
    for a in actions:
        path = Path(vault) / a["ref"]
        row = {"ref": a["ref"], "action": a["action"], "why": a["why"]}
        if a["action"] in ("keep", "pending"):  # kept by the audit or by settle; waiting for the audit
            rows.append({**row, "status": "unchanged"})
            continue
        if not path.is_file():
            rows.append({**row, "status": "missing"})
            continue
        before = path.read_bytes().decode("utf-8")
        if hashlib.sha256(before.encode("utf-8")).hexdigest() != a["sha256"]:
            rows.append({**row, "status": "conflict"})
            continue
        try:
            if a["action"] == "repair":
                after = replace_block(before, a["mermaid"], fmt.diagram, a.get("flip", False))
            else:
                after = (delete_section if a["action"] == "delete_section" else direction_tb)(before, fmt.diagram)
        except ValueError as e:
            rows.append({**row, "status": "error", "error": str(e)})
            continue
        added, removed = _diffstat(before, after)
        if write:
            path.write_bytes(after.encode("utf-8"))
        rows.append({**row, "status": "written" if write else "planned", "lines_added": added,
                     "lines_removed": removed, "sha256_after": hashlib.sha256(after.encode("utf-8")).hexdigest()})
    return rows

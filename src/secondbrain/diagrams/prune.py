"""Apply an audit to a vault: remove restating diagrams, turn wide kept ones upright.

Actions come from audit tiers:
- delete tier: remove the whole diagram section, except on cards that the
  method should judge again first (by default mechanism cards with three or
  more propositions), which stay for the review pool;
- keep tier: a flowchart wider than the reading width and laid out left to
  right (LR or RL) gets direction TB;
- keep tier, diagram that only renders after audit-repair: the repaired
  source replaces the broken one (turned TB as well when it is wide and LR).

Every write is guarded by the file hash recorded at audit time: a card edited
since the audit is reported as conflict and left alone. Without apply=True
nothing is written; the manifest shows what would change.
"""
from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audit import DEFAULT_FORMAT, CardFormat

_HEADER = re.compile(r"^(\s*(?:flowchart|graph)\s+)(LR|RL)\b")


@dataclass(frozen=True)
class PrunePolicy:
    review_kinds: tuple[str, ...] = ("mechanism",)
    review_min_props: int = 3
    width_limit: int = 700


def plan(tiers: dict[str, dict[str, Any]], policy: PrunePolicy = PrunePolicy()) -> list[dict[str, Any]]:
    """Actions for the cards in an audit report's 'cards' map, in ref order."""
    actions = []
    for ref in sorted(tiers):
        t = tiers[ref]
        if t["tier"] == "delete":
            if t["kind"] in policy.review_kinds and t["n_props"] >= policy.review_min_props:
                continue
            actions.append({"ref": ref, "action": "delete_section", "why": t["why"], "sha256": t["sha256"]})
        elif t["tier"] == "keep" and t.get("repair"):
            wide = t.get("direction") in ("LR", "RL") and (t.get("width") or 0) > policy.width_limit
            actions.append({"ref": ref, "action": "repair", "why": "repaired: " + ", ".join(t["repair"]["fixes"]),
                            "sha256": t["sha256"], "mermaid": t["repair"]["text"], "flip": wide})
        elif t["tier"] == "keep" and t.get("direction") in ("LR", "RL") and (t.get("width") or 0) > policy.width_limit:
            actions.append({"ref": ref, "action": "direction_tb", "why": f"width {t['width']}", "sha256": t["sha256"]})
    return actions


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

"""Old-diagram audit: should a renderable old diagram stay when nothing new replaces it?

The draw step can only replace an old diagram. When it draws nothing (or its
diagram is too weak to replace the old one) and the old diagram renders, the
old one stays, even if it only restates the text or says something the card
does not. This step shows the model the card and the old Mermaid and asks for
one verdict per card:

    {"verdict": "keep" | "remove", "category": "restates" | "misleading" | "useful", "reason": "..."}

restates: adds no structure over the text; misleading: contradicts the card or
adds claims it does not make; useful: shows structure the text does not.
The old diagram is removed only on remove + restates or remove + misleading;
every other answer, a malformed one, or none keeps it.
"""
from __future__ import annotations

import json
from importlib import resources
from typing import Any, Iterable, Sequence

PROMPT_FILE = "diagram_audit.md"
CATEGORIES = ("restates", "misleading", "useful")
REMOVE_CATEGORIES = ("restates", "misleading")  # the only categories that remove, with verdict remove


def load_prompt() -> str:
    return resources.files("secondbrain").joinpath("prompts", PROMPT_FILE).read_text(encoding="utf-8")


def audit_payload(card: dict[str, Any]) -> dict[str, Any]:
    """What the model sees: the citable text and the old diagram's source."""
    return {
        "ref": card["ref"], "title": card.get("title"), "kind": card.get("kind"),
        "E": card.get("essence", ""),
        "propositions": {f"P{i}": p for i, p in enumerate(card.get("propositions") or [], 1)},
        "old_mermaid": card["old_mermaid"],
    }


def build_audit_packet(cards: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """One request for a batch of cards (about 10), or None if there are none.

    The model answers {"results": [{"ref", "verdict", "category", "reason"}]}.
    """
    entries = [audit_payload(c) for c in cards]
    return {"prompt": load_prompt(), "input": {"cards": entries}} if entries else None


def read_verdicts(output: str | dict, refs: Sequence[str]) -> dict[str, Any]:
    """Map each requested ref to its answer object; missing or unreadable answers map to None."""
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except ValueError:
            return {r: None for r in refs}
    results = output.get("results") if isinstance(output, dict) else None
    if not isinstance(results, list):
        return {r: None for r in refs}
    by_ref = {x["ref"]: x for x in results if isinstance(x, dict) and isinstance(x.get("ref"), str)}
    return {r: by_ref.get(r) for r in refs}


def judge(ref: str, answer: Any) -> dict[str, Any]:
    """The audit outcome for one card. Removes only on a clear restates or misleading verdict."""
    out = {"ref": ref, "status": "audit"}
    if not isinstance(answer, dict):
        return {**out, "verdict": "keep", "category": "unsure", "reason": "no readable answer"}
    verdict, category = answer.get("verdict"), answer.get("category")
    reason = answer.get("reason") if isinstance(answer.get("reason"), str) else ""
    if verdict == "remove" and category in REMOVE_CATEGORIES:
        return {**out, "verdict": "remove", "category": category, "reason": reason}
    if verdict == "keep" and category == "useful":
        return {**out, "verdict": "keep", "category": "useful", "reason": reason}
    return {**out, "verdict": "keep", "category": "unsure",
            "reason": f"inconsistent answer (verdict {verdict!r}, category {category!r}); kept. {reason}".strip()}

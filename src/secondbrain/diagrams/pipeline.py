"""The diagram step as the ingest chain runs it, built from pure functions.

    cards --pregate--> skipped (no model call)
          --build_packet--> model --IR--> process() --> draw | rewrite | skip

process() validates the IR, emits Mermaid with a staleness marker when it
passes, and otherwise asks fallback() what to do: one rewrite carrying the
error list, then skip. It never falls back to letting the model write Mermaid.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Iterable, Sequence

from .emit import emit
from .ir import Card, Issue
from .staleness import marker
from .validate import DEFAULT_LIMITS, Limits, validate
from .width import GATED_FORMS

PROMPT_FILE = "diagram.md"
_KIND_REASONS = {
    "contrast": "contrast card: compare the sides in a 2-4 row table in the text",
    "fact": "fact card: a single fact, definition or field list reads better as text or a list",
    "derivation": "derivation card: an aligned formula shows the steps better than a diagram",
}


@dataclass(frozen=True)
class Policy:
    """Which cards skip the model entirely, how many rewrites, which thresholds."""

    skip_kinds: tuple[str, ...] = ("contrast", "fact", "derivation")
    skip_domains: tuple[str, ...] = ()
    max_rewrites: int = 1
    limits: Limits = field(default_factory=lambda: DEFAULT_LIMITS)


DEFAULT_POLICY = Policy()


def pregate(card: Card, policy: Policy = DEFAULT_POLICY) -> str | None:
    """Skip reason for cards whose kind or domain already rules a diagram out; None otherwise."""
    if card.kind in policy.skip_kinds:
        return _KIND_REASONS.get(card.kind, f"{card.kind} card: configured not to get diagrams")
    if card.domain in policy.skip_domains:
        return f"domain {card.domain}: configured not to get diagrams"
    if not card.propositions:
        return "card has no propositions to draw from"
    return None


@dataclass(frozen=True)
class Rewrite:
    """Send the IR back once, with the errors that must be fixed."""

    attempt: int
    errors: tuple[str, ...]


@dataclass(frozen=True)
class Skip:
    reason: str


def fallback(errors: Sequence[Issue | str], attempt: int, max_rewrites: int = 1) -> Rewrite | Skip:
    """What to do with an IR that failed validation on the given attempt (0 = first answer).

    While rewrites remain, ask for one with the full error list; after that,
    no diagram, with the errors as the recorded reason.
    """
    msgs = tuple(str(e) for e in errors)
    if not msgs:
        raise ValueError("fallback needs at least one error")
    if attempt < max_rewrites:
        return Rewrite(attempt=attempt + 1, errors=msgs)
    shown = "; ".join(msgs[:3]) + (f"; and {len(msgs) - 3} more" if len(msgs) > 3 else "")
    return Skip(f"no valid IR after {attempt + 1} attempt(s): {shown}")


@dataclass
class Outcome:
    """Result for one card. status is draw, skip or rewrite."""

    ref: str | None
    status: str
    mermaid: str | None = None
    reason: str = ""
    attempt: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rewrite: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {"ref": self.ref, "status": self.status, "attempt": self.attempt}
        if self.mermaid is not None:
            d["mermaid"] = self.mermaid
        if self.reason:
            d["reason"] = self.reason
        if self.errors:
            d["errors"] = self.errors
        if self.warnings:
            d["warnings"] = self.warnings
        if self.rewrite is not None:
            d["rewrite"] = self.rewrite
        return d


def card_payload(card: Card) -> dict[str, Any]:
    """What the model sees of a card: the citable text and the context for the skip rules."""
    return {
        "ref": card.ref, "title": card.title, "kind": card.kind, "domain": card.domain,
        "E": card.essence,
        "propositions": {f"P{i}": p for i, p in enumerate(card.propositions, 1)},
        "related_titles": list(card.related_titles),
        **({"old_diagram": card.old_diagram} if card.old_diagram else {}),
        # the source lets the model see what it would replace, and skip a mere redraw
        **({"old_mermaid": card.old_mermaid} if card.old_diagram == "renders" and card.old_mermaid else {}),
    }


def load_prompt() -> str:
    return resources.files("secondbrain").joinpath("prompts", PROMPT_FILE).read_text(encoding="utf-8")


def build_packet(cards: Iterable[Card | dict], policy: Policy = DEFAULT_POLICY) -> tuple[dict[str, Any] | None, list[Outcome]]:
    """One model request for a batch of cards (about 10), plus outcomes for pregated cards.

    Returns (packet or None if every card was pregated, skipped outcomes).
    The packet is {"prompt": <instructions>, "input": {"cards": [...]}}; the
    model answers {"results": [{"ref", "ir"}]}, read with read_results().
    """
    send, skipped = [], []
    for c in cards:
        card = c if isinstance(c, Card) else Card.from_dict(c)
        reason = pregate(card, policy)
        if reason:
            skipped.append(Outcome(card.ref, "skip", reason=reason))
        else:
            send.append(card_payload(card))
    packet = {"prompt": load_prompt(), "input": {"cards": send}} if send else None
    return packet, skipped


def build_rewrite_packet(card: Card, previous: Any, rewrite: Rewrite) -> dict[str, Any]:
    """The single-card request that carries the failed IR and its errors."""
    entry = card_payload(card)
    entry["previous"] = previous
    entry["errors"] = list(rewrite.errors)
    return {"prompt": load_prompt(), "input": {"cards": [entry]}, "attempt": rewrite.attempt}


def read_results(output: str | dict, refs: Sequence[str | None]) -> dict[str | None, Any]:
    """Map each requested ref to the IR the model returned for it.

    A missing or unparsable answer maps to None, which process() reports as V1.
    A batch answered without refs is matched by position.
    """
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError:
            return {r: None for r in refs}
    results = output.get("results") if isinstance(output, dict) else None
    if not isinstance(results, list):
        return {r: None for r in refs}
    keyed = [x for x in results if isinstance(x, dict) and x.get("ref") is not None]
    by_ref = {x["ref"]: x.get("ir") for x in keyed if isinstance(x["ref"], str)}
    if keyed:
        return {r: by_ref.get(r) for r in refs}
    return {r: (results[i].get("ir") if i < len(results) and isinstance(results[i], dict) else None)
            for i, r in enumerate(refs)}


def process(card: Card | dict, ir_data: Any, attempt: int = 0, policy: Policy = DEFAULT_POLICY) -> Outcome:
    """Validate one IR and turn it into a diagram, a rewrite request, or a skip."""
    card = card if isinstance(card, Card) else Card.from_dict(card)
    report = None
    if ir_data is None:
        report_errors = [Issue("V1", "no IR returned for this card")]
    else:
        try:
            report = validate(ir_data, card, policy.limits)
            report_errors = report.errors
            if report.ok and report.ir.decision == "draw":
                mermaid = emit(report.ir, marker=marker(card, report.ir))
        except Exception as e:  # a malformed answer must cost one card, never the batch
            report = None
            report_errors = [Issue("V1", f"IR could not be checked: {type(e).__name__}: {e}")]
    warnings = [str(w) for w in report.warnings] if report else []
    if report is not None and report.ok:
        if report.ir.decision == "skip":
            return Outcome(card.ref, "skip", reason=report.ir.reason, attempt=attempt, warnings=warnings)
        return Outcome(card.ref, "draw", mermaid=mermaid, attempt=attempt, warnings=warnings)
    action = fallback(report_errors, attempt, policy.max_rewrites)
    errors = [str(e) for e in report_errors]
    if isinstance(action, Rewrite):
        return Outcome(card.ref, "rewrite", attempt=attempt, errors=errors, warnings=warnings,
                       rewrite=build_rewrite_packet(card, ir_data, action))
    return Outcome(card.ref, "skip", reason=action.reason, attempt=attempt, errors=errors, warnings=warnings)


def render_feedback(outcome: Outcome, check: dict[str, Any] | None, card: Card | dict, ir_data: Any,
                    policy: Policy = DEFAULT_POLICY, max_width: float = 700) -> Outcome:
    """Apply the layout gate to a drawn diagram, given its tools/mermaid_check result.

    A diagram that fails to render (V9), renders wider than max_width (V14) or
    has edge labels lying on top of each other (V15) goes back for its one
    rewrite like any other failed check, or is skipped when that is used up.
    The width gate covers the same forms as the estimate (width.GATED_FORMS):
    Obsidian scales state diagrams to fit. Outcomes that are not draws, or
    have no check result, pass unchanged.
    """
    if outcome.status != "draw" or check is None:
        return outcome
    card = card if isinstance(card, Card) else Card.from_dict(card)
    errors: list[Issue] = []
    if not check.get("ok") or check.get("render_error"):
        errors.append(Issue("V9", f"the generated diagram did not render: {check.get('error') or check.get('render_error')}"))
    else:
        width = check.get("width") or 0
        form = ir_data.get("form") if isinstance(ir_data, dict) else None
        if width > max_width and form in GATED_FORMS:
            errors.append(Issue("V14", f"renders {width} px wide, limit {max_width:.0f}: fewer nodes side by side, "
                                       f"shorter labels, or fewer participants"))
        if check.get("label_overlaps"):
            errors.append(Issue("V15", f"{check['label_overlaps']} pair(s) of edge labels overlap after layout: "
                                       f"shorten or drop edge labels that sit side by side"))
    if not errors:
        return outcome
    action = fallback(errors, outcome.attempt, policy.max_rewrites)
    msgs = [str(e) for e in errors]
    if isinstance(action, Rewrite):
        return Outcome(card.ref, "rewrite", attempt=outcome.attempt, errors=msgs, warnings=outcome.warnings,
                       rewrite=build_rewrite_packet(card, ir_data, action))
    return Outcome(card.ref, "skip", reason=action.reason, attempt=outcome.attempt, errors=msgs,
                   warnings=outcome.warnings)

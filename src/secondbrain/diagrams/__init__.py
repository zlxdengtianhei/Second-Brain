"""Card diagrams: decide whether to draw, take an IR from the model, validate it,
and generate Mermaid deterministically. See prompts/diagram.md for the model side.

Entry points for the ingest chain (cards are plain data, see Card.from_dict):

    packet, skipped = build_packet(cards)          # card -> model request
    outcome = process(card, ir, attempt=0)         # IR -> draw | rewrite | skip
    section = fenced(outcome.mermaid)              # text under the diagram heading
    check_staleness(mermaid, card).needs_redraw    # after a card update
"""
from .emit import emit, fenced
from .ir import IR_JSON_SCHEMA, Card, DiagramIR, Issue, parse_ir
from .pipeline import Outcome, Policy, Rewrite, Skip, build_packet, fallback, pregate, process, read_results
from .staleness import check_staleness, content_hash
from .validate import Limits, Report, validate

__all__ = [
    "IR_JSON_SCHEMA", "Card", "DiagramIR", "Issue", "Limits", "Outcome", "Policy", "Report", "Rewrite", "Skip",
    "build_packet", "check_staleness", "content_hash", "emit", "fallback", "fenced", "parse_ir", "pregate",
    "process", "read_results", "validate",
]

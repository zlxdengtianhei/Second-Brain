"""Batch entry points for the diagram step and the vault audit.

    python -m secondbrain.diagrams schema
    python -m secondbrain.diagrams packets CARDS.jsonl OUT_DIR [--batch 10] [--skip-domain D ...]
    python -m secondbrain.diagrams apply CARDS.jsonl ANSWER.json ... --out OUT_DIR [--attempt N]
    python -m secondbrain.diagrams audit-extract VAULT OUT_DIR
    python -m secondbrain.diagrams audit-tier OUT_DIR [--delete-domain D ...]
    python -m secondbrain.diagrams prune VAULT OUT_DIR [--write]
    python -m secondbrain.diagrams pool OUT_DIR
    python -m secondbrain.diagrams writeback VAULT OUT_DIR OUTCOMES.jsonl ... [--write]

CARDS.jsonl holds one card per line: ref, title, kind, domain, essence,
propositions (list), related_titles. audit-extract writes this format, so a
trial run on existing cards can start from its cards.jsonl. Mermaid output and
audit blocks are written as {"id", "text"} lines for tools/mermaid_check.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from . import audit, prune, rejudge
from .ir import IR_JSON_SCHEMA, Card
from .pipeline import Policy, build_packet, pregate, process, read_results


def _jsonl(path: Path):
    return list(audit.read_jsonl(path))


def _write_jsonl(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def cmd_packets(a) -> dict:
    cards = [Card.from_dict(c) for c in _jsonl(a.cards)]
    a.out.mkdir(parents=True, exist_ok=True)
    policy = Policy(skip_domains=tuple(a.skip_domain))
    send = [c for c in cards if not pregate(c, policy)]
    skipped = [s.to_dict() for s in build_packet([c for c in cards if pregate(c, policy)], policy)[1]]
    n = 0
    for i in range(0, len(send), a.batch):  # full batches of cards that need the model
        packet, _ = build_packet(send[i:i + a.batch], policy)
        if packet:
            n += 1
            (a.out / f"packet-{n:03d}.json").write_text(json.dumps(packet, ensure_ascii=False, indent=1),
                                                      encoding="utf-8")
    _write_jsonl(a.out / "pregated.jsonl", skipped)
    return {"cards": len(cards), "packets": n, "pregated": len(skipped)}


def cmd_apply(a) -> dict:
    """Read model answers for packets (or rewrite packets) and decide each card."""
    cards = {c["ref"]: Card.from_dict(c) for c in _jsonl(a.cards)}
    a.out.mkdir(parents=True, exist_ok=True)
    outcomes = []
    policy = Policy(skip_domains=tuple(a.skip_domain))
    for answer in a.answers:
        packet_path = answer.with_name(answer.name.replace(".answer", ""))
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        refs = [c["ref"] for c in packet["input"]["cards"]]
        try:
            text = answer.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""  # every card in this packet then reports "no IR returned"
        irs = read_results(text, refs)
        attempt = packet.get("attempt", a.attempt)
        outcomes += [process(cards[r], irs[r], attempt=attempt, policy=policy) for r in refs]
    for i, o in enumerate(x for x in outcomes if x.status == "rewrite"):
        (a.out / f"rewrite-{i + 1:03d}.json").write_text(json.dumps(o.rewrite, ensure_ascii=False, indent=1),
                                                        encoding="utf-8")
    _write_jsonl(a.out / "outcomes.jsonl", [o.to_dict() for o in outcomes])
    _write_jsonl(a.out / "mermaid.jsonl", [{"id": o.ref, "text": o.mermaid} for o in outcomes if o.mermaid])
    return dict(Counter(o.status for o in outcomes))


def cmd_audit_extract(a) -> dict:
    return audit.extract(a.vault, a.out, audit.CardFormat(knowledge_dir=a.knowledge_dir))


def cmd_audit_tier(a) -> dict:
    report = audit.tier(audit.read_jsonl(a.dir / "cards.jsonl"), audit.read_jsonl(a.dir / "checks.jsonl"),
                        audit.TierRules(delete_domains=tuple(a.delete_domain)))
    (a.dir / "tiers.json").write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=0), encoding="utf-8")
    return {"counts": dict(report.counts), "why": {t: dict(c) for t, c in report.why.items()}}


def cmd_prune(a) -> dict:
    tiers = json.loads((a.dir / "tiers.json").read_text(encoding="utf-8"))["cards"]
    actions = prune.plan(tiers, prune.PrunePolicy(width_limit=a.width_limit))
    rows = prune.apply(a.vault, actions, write=a.write)
    _write_jsonl(a.dir / ("prune_manifest.jsonl" if a.write else "prune_plan.jsonl"), rows)
    summary: dict = {"actions": dict(Counter(r["action"] for r in rows)),
                     "status": dict(Counter(r["status"] for r in rows)),
                     "lines_removed": sum(r.get("lines_removed", 0) for r in rows),
                     "lines_added": sum(r.get("lines_added", 0) for r in rows)}
    return summary


def cmd_pool(a) -> dict:
    tiers = json.loads((a.dir / "tiers.json").read_text(encoding="utf-8"))["cards"]
    pool = rejudge.select_pool(tiers)
    _write_jsonl(a.dir / "pool.jsonl", rejudge.pool_cards(pool, audit.read_jsonl(a.dir / "cards.jsonl")))
    return dict(Counter(p["pool_reason"] for p in pool))


def cmd_writeback(a) -> dict:
    tiers = json.loads((a.dir / "tiers.json").read_text(encoding="utf-8"))["cards"]
    outcomes = [o for path in a.outcomes for o in audit.read_jsonl(path)]
    actions = rejudge.plan(rejudge.select_pool(tiers), outcomes)
    rows = rejudge.apply(a.vault, actions, write=a.write)
    _write_jsonl(a.dir / ("writeback_manifest.jsonl" if a.write else "writeback_plan.jsonl"), rows)
    _write_jsonl(a.dir / "writeback_mermaid.jsonl",
                 [{"id": x["ref"], "text": x["mermaid"]} for x in actions if x["action"] == "replace"])
    return {"actions": dict(Counter(r["action"] for r in rows)), "status": dict(Counter(r["status"] for r in rows))}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m secondbrain.diagrams", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("schema", help="print the IR JSON Schema")
    s = sub.add_parser("packets", help="cards -> model request packets (pregated cards skipped)")
    s.add_argument("cards", type=Path)
    s.add_argument("out", type=Path)
    s.add_argument("--batch", type=int, default=10)
    s.add_argument("--skip-domain", action="append", default=[])
    s = sub.add_parser("apply", help="model answers -> outcomes, rewrite packets, mermaid.jsonl")
    s.add_argument("cards", type=Path)
    s.add_argument("answers", type=Path, nargs="+", help="X.answer.json next to the packet X.json it answers")
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--attempt", type=int, default=0)
    s.add_argument("--skip-domain", action="append", default=[])
    s = sub.add_parser("audit-extract", help="vault -> cards.jsonl, blocks.jsonl (read-only)")
    s.add_argument("vault", type=Path)
    s.add_argument("out", type=Path)
    s.add_argument("--knowledge-dir", default=audit.DEFAULT_FORMAT.knowledge_dir)
    s = sub.add_parser("audit-tier", help="cards.jsonl + checks.jsonl -> tiers.json")
    s.add_argument("dir", type=Path)
    s.add_argument("--delete-domain", action="append", default=[])
    s = sub.add_parser("prune", help="tiers.json -> delete restating diagrams, wide keepers to TB")
    s.add_argument("vault", type=Path)
    s.add_argument("dir", type=Path)
    s.add_argument("--width-limit", type=int, default=700)
    s.add_argument("--write", action="store_true", help="write the changes (default: plan only)")
    s = sub.add_parser("pool", help="tiers.json + cards.jsonl -> pool.jsonl, the cards to judge again")
    s.add_argument("dir", type=Path)
    s = sub.add_parser("writeback", help="pool outcomes -> replace or remove diagram sections (default: plan only)")
    s.add_argument("vault", type=Path)
    s.add_argument("dir", type=Path)
    s.add_argument("outcomes", type=Path, nargs="+",
                   help="outcomes.jsonl and pregated.jsonl files, earlier rounds first")
    s.add_argument("--write", action="store_true", help="write the changes (default: plan only)")
    a = p.parse_args(argv)
    if a.cmd == "schema":
        print(json.dumps(IR_JSON_SCHEMA, ensure_ascii=False, indent=2))
        return 0
    handler = {"packets": cmd_packets, "apply": cmd_apply, "audit-extract": cmd_audit_extract,
               "audit-tier": cmd_audit_tier, "prune": cmd_prune, "pool": cmd_pool,
               "writeback": cmd_writeback}[a.cmd]
    print(json.dumps(handler(a), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

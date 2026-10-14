"""Batch entry points for the diagram step and the vault audit.

    python -m secondbrain.diagrams schema
    python -m secondbrain.diagrams packets CARDS.jsonl OUT_DIR [--batch 10] [--skip-domain D ...]
    python -m secondbrain.diagrams apply CARDS.jsonl ANSWER.json ... --out OUT_DIR [--checker CHECK.mjs]
    python -m secondbrain.diagrams audit-extract VAULT OUT_DIR [--language en|zh] [--knowledge-dir DIR]
    python -m secondbrain.diagrams audit-repair OUT_DIR --checker CHECK.mjs [--checker-modules DIR]
    python -m secondbrain.diagrams audit-tier OUT_DIR [--delete-domain D ...]
    python -m secondbrain.diagrams prune VAULT OUT_DIR [--old-audit FILE ...] [--language en|zh] [--checker CHECK.mjs]
        [--write]
    python -m secondbrain.diagrams pool OUT_DIR [--refs REFS.txt] [--out POOL.jsonl] [--delete-tier]
    python -m secondbrain.diagrams old-audit-packets POOL.jsonl OUT_DIR [--outcomes OUTCOMES.jsonl ...]
    python -m secondbrain.diagrams old-audit-apply ANSWER.json ... --out OUT_DIR
    python -m secondbrain.diagrams writeback VAULT OUT_DIR OUTCOMES.jsonl ... [--old-audit FILE ...]
        [--refs REFS.txt] [--delete-review-on-skip remove|keep] [--remove-domain D ...] [--language en|zh]
        [--checker CHECK.mjs] [--write]

CARDS.jsonl holds one card per line: ref, title, kind, domain, essence,
propositions (list), related_titles. audit-extract writes this format, so a
trial run on existing cards can start from its cards.jsonl. Mermaid output and
audit blocks are written as {"id", "text"} lines for tools/mermaid_check.

The commands that read or write cards in a vault (audit-extract, prune,
writeback) find the section headings by card language: --language, else
the vault's recorded language (`sb language`), else [cards] language of the
config file, else the headings the cards use. pool
reads cards.jsonl, so it follows the language audit-extract used.
audit-extract reads the cards under --knowledge-dir, else [layout] knowledge
of the config file, else 10_Knowledge.

audit-repair (after tools/mermaid_check has written checks.jsonl) rewrites
the old flowcharts that fail to parse (quoted labels, keyword ids renamed,
one statement per line), renders the rewrites and writes repairs.jsonl;
audit-tier then treats a repaired diagram as one that renders, and prune and
writeback write the repaired source where the diagram stays.

No old diagram is removed without the old-diagram audit (oldcheck), except
in a deleted domain or when it does not parse even after audit-repair: prune
removes a delete-tier diagram, and writeback the diagram of a delete_review
card the model skips, only when the audit says restates or misleading; until
there is a verdict nothing changes (prune: pending; writeback: pending,
rule .../no_audit). `pool --delete-tier` writes the delete-tier cards prune
handles as rows for old-audit-packets; the packets for the pool cover the
delete_review cards too.

writeback refuses to run when the rendering old flowcharts in the pool have
no graph (a tiers.json from before the redraw guard): rerun audit-tier.
pool, prune and writeback refuse a tiers.json made without audit-repair
results while some old diagrams do not parse. With --checker, prune and
writeback render the old diagrams they turn TB or repair, as they will be
written. A diagram that TB would make wider than it was is not turned
(writeback: keep_old, +wide instead of +tb). writeback adds +wide to the rule
of those still wider than 700 px; prune names both cases in 'why' and counts
them (not_turned, still_wide). Without --checker they refuse to write a turn,
and warn about it on a plan.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import dataclasses
import shutil
import subprocess

from . import audit, oldcheck, prune, rejudge, repair
from .ir import IR_JSON_SCHEMA, Card
from .pipeline import Policy, build_packet, pregate, process, read_results, render_feedback
from .validate import DEFAULT_LIMITS


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


def _run_checker(a, blocks: list[dict], name: str) -> dict:
    """Run tools/mermaid_check on blocks; results by id. Raises if the checker cannot start."""
    node = shutil.which("node")
    if not node:
        raise SystemExit("--checker needs Node.js on PATH")
    src, out = a.out / f"{name}_in.jsonl", a.out / f"{name}_checks.jsonl"
    _write_jsonl(src, blocks)
    cmd = [node, str(a.checker), str(src), str(out)]
    if a.checker_modules:
        cmd[2:2] = ["--modules", str(a.checker_modules)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode not in (0, 1):  # 1 only means some blocks failed
        raise SystemExit(f"mermaid_check failed: {proc.stderr.strip()}")
    return {r["id"]: r for r in audit.read_jsonl(out)}


def cmd_apply(a) -> dict:
    """Read model answers for packets (or rewrite packets) and decide each card.

    With --checker, every drawn diagram is rendered by tools/mermaid_check and
    the real width and label overlaps are gated (the width estimate is then
    off); without it the validator's width estimate is the gate.
    """
    cards = {c["ref"]: Card.from_dict(c) for c in _jsonl(a.cards)}
    a.out.mkdir(parents=True, exist_ok=True)
    limits = dataclasses.replace(DEFAULT_LIMITS, max_width=None) if a.checker else DEFAULT_LIMITS
    policy = Policy(skip_domains=tuple(a.skip_domain), limits=limits)
    decided = []  # (card, ir, outcome)
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
        decided += [(cards[r], irs[r], process(cards[r], irs[r], attempt=attempt, policy=policy)) for r in refs]
    summary: dict = {}
    if a.checker:
        drawn = [{"id": o.ref, "text": o.mermaid} for _, _, o in decided if o.status == "draw"]
        checks = _run_checker(a, drawn, "render") if drawn else {}
        before = Counter(o.status for _, _, o in decided)
        decided = [(c, ir, render_feedback(o, checks.get(o.ref), c, ir, policy)) for c, ir, o in decided]
        summary["layout_gate"] = {"rendered": len(drawn),
                                  "turned_back": before["draw"] - Counter(o.status for _, _, o in decided)["draw"]}
    outcomes = [o for _, _, o in decided]
    for i, o in enumerate(x for x in outcomes if x.status == "rewrite"):
        (a.out / f"rewrite-{i + 1:03d}.json").write_text(json.dumps(o.rewrite, ensure_ascii=False, indent=1),
                                                        encoding="utf-8")
    _write_jsonl(a.out / "outcomes.jsonl", [o.to_dict() for o in outcomes])
    _write_jsonl(a.out / "mermaid.jsonl", [{"id": o.ref, "text": o.mermaid} for o in outcomes if o.mermaid])
    return {**dict(Counter(o.status for o in outcomes)), **summary}


def _tiers(a) -> dict:
    """The cards of DIR/tiers.json; refuses one made without audit-repair results while some old diagrams are
    broken (an older tiers.json, or audit-tier run before audit-repair): their repairs would be skipped silently."""
    doc = json.loads((a.dir / "tiers.json").read_text(encoding="utf-8"))
    cards = doc["cards"]
    broken = sum(1 for t in cards.values() if not t.get("ok"))
    read = doc.get("repairs_read", any("repair" in t for t in cards.values()))
    if broken and not read:
        raise SystemExit(f"{broken} old diagrams in tiers.json do not parse and it has no audit-repair results: "
                         f"run audit-repair, then audit-tier")
    return cards


def _turns_need_checker(a, actions: list[dict]) -> None:
    """Turning a diagram TB is checked against its render (no turn that makes it wider); without --checker,
    refuse to write any turn and warn on a plan."""
    turns = sum(1 for x in actions if x["action"] == "direction_tb" or x.get("flip"))
    if turns and not a.checker:
        msg = (f"{turns} diagrams would be turned TB without --checker, which drops a turn that makes a diagram "
               f"wider: pass --checker")
        if a.write:
            raise SystemExit(msg)
        print("warning: " + msg, file=sys.stderr)


def _pool(a) -> tuple[list[dict], list[str]]:
    """Pool rows from the audit tiers (with --delete-tier: the delete tier prune handles instead), restricted to
    --refs when given; plus refs not among them."""
    tiers = _tiers(a)
    pool = rejudge.select_delete(tiers) if getattr(a, "delete_tier", False) else rejudge.select_pool(tiers)
    if not a.refs:
        return pool, []
    wanted = [x.strip() for x in a.refs.read_text(encoding="utf-8").splitlines()
              if x.strip() and not x.startswith("#")]
    in_pool = {p["ref"] for p in pool}
    return [p for p in pool if p["ref"] in set(wanted)], [r for r in wanted if r not in in_pool]


def _writeback_policy(a) -> rejudge.WritebackPolicy:
    return rejudge.WritebackPolicy(delete_review_on_skip=a.delete_review_on_skip,
                                   remove_domains=tuple(a.remove_domain))


def cmd_audit_packets(a) -> dict:
    """Old-diagram audit packets for pool cards whose old diagram would stay."""
    rows = _jsonl(a.pool)
    outcomes = [o for path in a.outcomes for o in audit.read_jsonl(path)] if a.outcomes else None
    try:
        cands = rejudge.audit_candidates(rows, outcomes, _writeback_policy(a))
    except ValueError as e:
        raise SystemExit(str(e))
    a.out.mkdir(parents=True, exist_ok=True)
    n = 0
    for i in range(0, len(cands), a.batch):
        packet = oldcheck.build_audit_packet(cands[i:i + a.batch])
        if packet:
            n += 1
            (a.out / f"old-audit-{n:03d}.json").write_text(json.dumps(packet, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")
    return {"pool": len(rows), "audited": len(cands), "packets": n,
            "mode": "after the draw step" if outcomes is not None else "beside the draw step"}


def cmd_audit_apply(a) -> dict:
    """Read old-diagram audit answers into old_audit_outcomes.jsonl."""
    a.out.mkdir(parents=True, exist_ok=True)
    results = []
    for answer in a.answers:
        packet = json.loads(answer.with_name(answer.name.replace(".answer", "")).read_text(encoding="utf-8"))
        refs = [c["ref"] for c in packet["input"]["cards"]]
        try:
            text = answer.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        verdicts = oldcheck.read_verdicts(text, refs)
        results += [oldcheck.judge(r, verdicts[r]) for r in refs]
    _write_jsonl(a.out / "old_audit_outcomes.jsonl", results)
    return dict(Counter(f"{r['verdict']}:{r['category']}" for r in results))


def _card_format(a, refs=None) -> audit.CardFormat:
    """Card headings for a.vault by language, and the knowledge folder; the choices go to stderr."""
    try:
        folder, folder_source = audit.knowledge_dir(getattr(a, "knowledge_dir", None))
        fmt, language, source = audit.card_format(a.vault, a.language, refs, folder)
    except ValueError as e:
        raise SystemExit(str(e))
    note = f"card language {language} (from {source})"
    if refs is None:  # the command reads the whole knowledge folder
        note += f"; knowledge folder {folder} (from {folder_source})"
    print(note, file=sys.stderr)
    return fmt


def cmd_audit_extract(a) -> dict:
    return audit.extract(a.vault, a.out, _card_format(a))


def cmd_audit_repair(a) -> dict:
    """Repair the old diagrams that failed to parse, render the repairs, write repairs.jsonl."""
    checks = {c["id"]: c for c in audit.read_jsonl(a.dir / "checks.jsonl")}
    broken = [b for b in audit.read_jsonl(a.dir / "blocks.jsonl") if not checks.get(b["id"], {}).get("ok")]
    fixed = [(b["id"], repair.repair(b["text"])) for b in broken]
    tried = [(i, r) for i, r in fixed if r is not None]
    a.out = a.dir
    results = _run_checker(a, [{"id": i, "text": r.text} for i, r in tried], "repair") if tried else {}
    rows = [{"id": i, "text": r.text, "fixes": list(r.fixes), "check": results.get(i, {})} for i, r in tried]
    _write_jsonl(a.dir / "repairs.jsonl", rows)
    renders = sum(1 for x in rows if x["check"].get("ok") and not x["check"].get("render_error"))
    return {"broken": len(broken), "rewritten": len(tried), "renders": renders, "unreadable": len(broken) - len(tried)}


def cmd_audit_tier(a) -> dict:
    repairs = a.dir / "repairs.jsonl"
    report = audit.tier(audit.read_jsonl(a.dir / "cards.jsonl"), audit.read_jsonl(a.dir / "checks.jsonl"),
                        audit.TierRules(delete_domains=tuple(a.delete_domain)),
                        list(audit.read_jsonl(repairs)) if repairs.is_file() else None)
    (a.dir / "tiers.json").write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=0), encoding="utf-8")
    return {"counts": dict(report.counts), "why": {t: dict(c) for t, c in report.why.items()},
            **({"repaired": report.repaired} if report.repaired else {})}


def cmd_prune(a) -> dict:
    tiers = _tiers(a)
    audits = [v for path in a.old_audit for v in audit.read_jsonl(path)]
    actions = prune.plan(tiers, prune.PrunePolicy(width_limit=a.width_limit), audits)
    _turns_need_checker(a, actions)
    fmt = _card_format(a, [x["ref"] for x in actions])
    settled = None
    if a.checker:  # render the diagrams turned TB or repaired as they will be written
        a.out = a.dir
        kept = rejudge.kept_sources(a.vault, actions, fmt)
        actions, settled = prune.settle(actions, _run_checker(a, kept, "kept") if kept else {}, tiers, a.width_limit)
    rows = prune.apply(a.vault, actions, write=a.write, fmt=fmt)
    _write_jsonl(a.dir / ("prune_manifest.jsonl" if a.write else "prune_plan.jsonl"), rows)
    summary: dict = {"actions": dict(Counter(r["action"] for r in rows)),
                     "status": dict(Counter(r["status"] for r in rows)),
                     "lines_removed": sum(r.get("lines_removed", 0) for r in rows),
                     "lines_added": sum(r.get("lines_added", 0) for r in rows)}
    if settled is not None:
        summary.update({"not_turned": settled["not_turned"], "still_wide": settled["still_wide"]})
    return summary


def cmd_pool(a) -> dict:
    pool, missing = _pool(a)
    _write_jsonl(a.out or a.dir / "pool.jsonl", rejudge.pool_cards(pool, audit.read_jsonl(a.dir / "cards.jsonl")))
    out = dict(Counter(p["pool_reason"] for p in pool))
    if a.refs:
        out["not_in_pool"] = len(missing)
    return out


def cmd_writeback(a) -> dict:
    pool, _ = _pool(a)
    outcomes = [o for path in a.outcomes for o in audit.read_jsonl(path)]
    audits = [v for path in a.old_audit for v in audit.read_jsonl(path)]
    try:
        actions = rejudge.plan(pool, outcomes, _writeback_policy(a), audits)
    except ValueError as e:
        raise SystemExit(str(e))
    _turns_need_checker(a, actions)
    touched = [x["ref"] for x in actions if x["action"] in rejudge.WRITES]
    fmt = _card_format(a, touched)
    if a.checker:  # render the kept old diagrams as they will be written: no turn that widens, +wide if too wide
        a.out = a.dir
        kept = rejudge.kept_sources(a.vault, actions, fmt)
        actions = rejudge.settle_kept(actions, _run_checker(a, kept, "kept") if kept else {},
                                      {p["ref"]: p.get("old_width") for p in pool}, _writeback_policy(a))
    rows = rejudge.apply(a.vault, actions, write=a.write, fmt=fmt)
    _write_jsonl(a.dir / ("writeback_manifest.jsonl" if a.write else "writeback_plan.jsonl"), rows)
    _write_jsonl(a.dir / "writeback_mermaid.jsonl",
                 [{"id": x["ref"], "text": x["mermaid"]} for x in actions if x["action"] in ("replace", "repair_old")])
    return {"actions": dict(Counter(r["action"] for r in rows)), "status": dict(Counter(r["status"] for r in rows)),
            "by_pool_reason": rejudge.summary(actions)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m secondbrain.diagrams", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    lang = {"choices": audit.LANGUAGES,
            "help": "card heading language (default: the vault's language, else [cards] language of the config "
                    "file, else the cards' headings)"}
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
    s.add_argument("--checker", type=Path, help="tools/mermaid_check/check.mjs: gate on real width and overlaps")
    s.add_argument("--checker-modules", type=Path, help="directory whose node_modules the checker uses")
    s = sub.add_parser("audit-extract", help="vault -> cards.jsonl, blocks.jsonl (read-only)")
    s.add_argument("vault", type=Path)
    s.add_argument("out", type=Path)
    s.add_argument("--knowledge-dir", help="card folder in the vault (default: [layout] knowledge of the "
                                           f"config file, else {audit.DEFAULT_FORMAT.knowledge_dir})")
    s.add_argument("--language", **lang)
    s = sub.add_parser("audit-repair", help="checks.jsonl -> repairs.jsonl: broken old flowcharts rewritten and rendered")
    s.add_argument("dir", type=Path)
    s.add_argument("--checker", type=Path, required=True, help="tools/mermaid_check/check.mjs")
    s.add_argument("--checker-modules", type=Path, help="directory whose node_modules the checker uses")
    s = sub.add_parser("audit-tier", help="cards.jsonl + checks.jsonl (+ repairs.jsonl) -> tiers.json")
    s.add_argument("dir", type=Path)
    s.add_argument("--delete-domain", action="append", default=[])
    s = sub.add_parser("prune", help="tiers.json (+ old-diagram audit) -> delete restating diagrams, wide keepers "
                                     "to TB, repaired keepers written")
    s.add_argument("vault", type=Path)
    s.add_argument("dir", type=Path)
    s.add_argument("--width-limit", type=int, default=700)
    s.add_argument("--old-audit", type=Path, nargs="*", default=[],
                   help="old_audit_outcomes.jsonl from old-audit-apply: a delete-tier diagram is removed only "
                        "when the audit says restates or misleading")
    s.add_argument("--checker", type=Path, help="tools/mermaid_check/check.mjs: render the diagrams turned TB or "
                                                "repaired; those still too wide are counted and named in 'why'")
    s.add_argument("--checker-modules", type=Path, help="directory whose node_modules the checker uses")
    s.add_argument("--language", **lang)
    s.add_argument("--write", action="store_true", help="write the changes (default: plan only)")
    s = sub.add_parser("pool", help="tiers.json + cards.jsonl -> pool.jsonl, the cards to judge again")
    s.add_argument("dir", type=Path)
    s.add_argument("--refs", type=Path, help="only these card refs (one per line)")
    s.add_argument("--out", type=Path, help="where to write the pool (default: DIR/pool.jsonl)")
    s.add_argument("--delete-tier", action="store_true",
                   help="write the delete-tier cards prune handles instead (pool_reason delete), for the "
                        "old-diagram audit")
    s = sub.add_parser("old-audit-packets", help="pool.jsonl -> old-diagram audit packets")
    s.add_argument("pool", type=Path)
    s.add_argument("out", type=Path)
    s.add_argument("--outcomes", type=Path, nargs="*",
                   help="draw-step outcomes: audit only cards whose old diagram stays (default: all that could)")
    s.add_argument("--batch", type=int, default=10)
    s.add_argument("--delete-review-on-skip", choices=("remove", "keep"), default="remove")
    s.add_argument("--remove-domain", action="append", default=[])
    s = sub.add_parser("old-audit-apply", help="old-diagram audit answers -> old_audit_outcomes.jsonl")
    s.add_argument("answers", type=Path, nargs="+", help="old-audit-NNN.answer.json next to old-audit-NNN.json")
    s.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("writeback", help="pool outcomes -> replace or remove diagram sections (default: plan only)")
    s.add_argument("vault", type=Path)
    s.add_argument("dir", type=Path)
    s.add_argument("outcomes", type=Path, nargs="+",
                   help="outcomes.jsonl and pregated.jsonl files, earlier rounds first")
    s.add_argument("--old-audit", type=Path, nargs="*", default=[],
                   help="old_audit_outcomes.jsonl from old-audit-apply")
    s.add_argument("--refs", type=Path, help="only these card refs (one per line)")
    s.add_argument("--delete-review-on-skip", choices=("remove", "keep"), default="remove",
                   help="old diagram of a delete_review card the model skips: remove as the old-diagram audit "
                        "says (default), or keep")
    s.add_argument("--remove-domain", action="append", default=[],
                   help="also remove old diagrams on skip in this domain (cards the audit tiered "
                        "'domain' are removed anyway)")
    s.add_argument("--language", **lang)
    s.add_argument("--checker", type=Path, help="tools/mermaid_check/check.mjs: render the old diagrams turned TB "
                                                "or repaired, and mark those still too wide +wide")
    s.add_argument("--checker-modules", type=Path, help="directory whose node_modules the checker uses")
    s.add_argument("--write", action="store_true", help="write the changes (default: plan only)")
    a = p.parse_args(argv)
    if a.cmd == "schema":
        print(json.dumps(IR_JSON_SCHEMA, ensure_ascii=False, indent=2))
        return 0
    handler = {"packets": cmd_packets, "apply": cmd_apply, "audit-extract": cmd_audit_extract,
               "audit-repair": cmd_audit_repair, "audit-tier": cmd_audit_tier, "prune": cmd_prune, "pool": cmd_pool,
               "old-audit-packets": cmd_audit_packets, "old-audit-apply": cmd_audit_apply,
               "writeback": cmd_writeback}[a.cmd]
    print(json.dumps(handler(a), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

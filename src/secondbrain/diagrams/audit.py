"""Read-only audit of the Mermaid diagrams already in a vault.

Three stages, so the browser-based parse can sit in the middle:

1. extract(): read every card under the knowledge folder; write cards.jsonl
   (card fields plus file hash) and blocks.jsonl (one Mermaid block per line,
   the input format of tools/mermaid_check);
2. tools/mermaid_check parses and renders blocks.jsonl into checks.jsonl,
   which carries the parse result, node and edge structure, direction and size;
3. tier(): join both and sort every diagram into one of four tiers:
     delete  - restates the text, wrong form for the card, or broken on a
               fact/contrast card: remove the diagram section;
     keep    - shows a branch, a merge, a loop, or is a sequence/state diagram,
               and its node labels are grounded in the card;
     review  - neither: judge again with the draw-or-skip method;
     redraw  - does not parse, or a flowchart more than 30 px wider than the
               note that is already upright (a left-to-right one is turned
               first): judge again and draw (rule "wide": a narrower redraw
               is the point).

The rules are the mechanical tiering from the method write-up; they are a
screen, not a verdict on each diagram.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

from .. import config, labels
from ..cards import HEADINGS
from .text import tokens

_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
FENCE = re.compile(r"```mermaid\n(.*?)```", re.S)
_PROP = re.compile(r"^- P(\d+)\.\s*(.+)$", re.M)
_WIKI = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
LANGUAGES = tuple(HEADINGS)


@dataclass(frozen=True)
class CardFormat:
    """Where the card parts live: the knowledge folder and the section headings."""

    knowledge_dir: str = "10_Knowledge"
    essence: str = HEADINGS["zh"]["essence"]
    propositions: str = HEADINGS["zh"]["propositions"]
    diagram: str = HEADINGS["zh"]["diagram"]
    related: str = HEADINGS["zh"]["related"]

    @classmethod
    def for_language(cls, language: str, knowledge_dir: str = "10_Knowledge") -> "CardFormat":
        """The headings cards.render_card writes for ``language`` (en or zh)."""
        h = HEADINGS[language]
        return cls(knowledge_dir, h["essence"], h["propositions"], h["diagram"], h["related"])


# The library default is the Chinese vault these tools were first run on; the
# command line picks the format by language (card_format).
DEFAULT_FORMAT = CardFormat()


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end > 0:
            try:
                fm = yaml.load(text[4:end], Loader=_Loader) or {}
            except yaml.YAMLError:
                fm = {}
            return (fm if isinstance(fm, dict) else {}), text[end + 5:]
    return {}, text


def sections(body: str) -> dict[str, str]:
    """Second-level sections by heading; headings inside code fences do not count."""
    out: dict[str, str] = {}
    cur, buf, in_code = "", [], False
    for line in body.split("\n"):
        if line.startswith("```"):
            in_code = not in_code
        if not in_code and line.startswith("## "):
            out[cur] = "\n".join(buf)
            cur, buf = line[3:].strip(), []
            continue
        buf.append(line)
    out[cur] = "\n".join(buf)
    return out


def heading_counts(paths: Iterable[Path]) -> Counter:
    """Per language, how many of these cards have that language's essence heading."""
    counts: Counter = Counter()
    for p in paths:
        try:
            secs = sections(split_frontmatter(Path(p).read_bytes().decode("utf-8"))[1])
        except (OSError, UnicodeDecodeError):
            continue
        counts.update(lang for lang in LANGUAGES if HEADINGS[lang]["essence"] in secs)
    return counts


def knowledge_dir(flag: str | None = None) -> tuple[str, str]:
    """The knowledge folder and where it came from: the --knowledge-dir flag,
    else ``[layout] knowledge`` of a loaded config file, else the default."""
    if flag:
        return flag, "--knowledge-dir"
    settings = config.current()
    if settings.source is not None:
        return settings.layout.knowledge, f"[layout] knowledge in {settings.source}"
    return DEFAULT_FORMAT.knowledge_dir, "default"


def card_format(vault: Path, language: str | None = None, refs: Iterable[str] | None = None,
                knowledge_dir: str = DEFAULT_FORMAT.knowledge_dir) -> tuple[CardFormat, str, str]:
    """The card format for a vault, its language, and where the language came from.

    The language is ``language`` when given (the --language flag); else the
    vault's own language (labels.vault_language: the catalog record, else the
    generated map pages); else ``[cards] language`` when the config file sets
    it; else the headings the cards use. ``refs`` are the vault-relative cards
    a command will touch (default: every card under the knowledge folder).
    Raises ValueError when those cards use another language's headings and
    none of the chosen one's, so a wrong setting cannot read every card as
    empty.
    """
    root = Path(vault)
    paths = [root / r for r in refs] if refs is not None else sorted((root / knowledge_dir).rglob("*.md"))
    counts = heading_counts(paths)
    settings = config.current()
    vault_language = None if language else labels.vault_language(root.absolute())
    if language:
        source = "--language"
    elif vault_language:
        language, source = vault_language, "the vault's language record or map pages"
    elif settings.card_language_set:
        language, source = settings.card_language, f"[cards] language in {settings.source}"
    elif counts:
        language, source = max(LANGUAGES, key=lambda x: counts[x]), "card headings"
    else:
        language, source = settings.card_language, "default"
    if counts and not counts[language]:
        found = "; ".join(f"{n} have '## {HEADINGS[lang]['essence']}' ({lang})" for lang, n in counts.most_common())
        raise ValueError(f"language {language} (from {source}) does not match the cards: none has "
                         f"'## {HEADINGS[language]['essence']}', {found}. Pass --language "
                         f"{counts.most_common(1)[0][0]}")
    return CardFormat.for_language(language, knowledge_dir), language, source


def read_card(text: str, ref: str, fmt: CardFormat = DEFAULT_FORMAT) -> dict[str, Any]:
    """Card fields the diagram step uses, plus every Mermaid block in the body."""
    fm, body = split_frontmatter(text)
    secs = sections(body)
    return {
        "ref": ref,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "title": str(fm.get("title") or Path(ref).stem),
        "kind": fm.get("kind"),
        "domain": fm.get("domain"),
        "essence": secs.get(fmt.essence, "").strip(),
        "propositions": [m[1].strip() for m in _PROP.findall(secs.get(fmt.propositions, ""))],
        "related_titles": _WIKI.findall(secs.get(fmt.related, "")),
        "has_diagram_section": fmt.diagram in secs,
        "blocks": FENCE.findall(body),
    }


def scan(vault: Path, fmt: CardFormat = DEFAULT_FORMAT) -> Iterator[dict[str, Any]]:
    root = Path(vault) / fmt.knowledge_dir
    for p in sorted(root.rglob("*.md")):
        # bytes, not read_text: no newline translation, so the hash is the file's own
        yield read_card(p.read_bytes().decode("utf-8"), p.relative_to(vault).as_posix(), fmt)


def extract(vault: Path, out_dir: Path, fmt: CardFormat = DEFAULT_FORMAT) -> dict[str, int]:
    """Write cards.jsonl and blocks.jsonl under out_dir. Reads the vault only."""
    out_dir.mkdir(parents=True, exist_ok=True)
    n_cards = n_blocks = 0
    with (out_dir / "cards.jsonl").open("w", encoding="utf-8") as fc, \
            (out_dir / "blocks.jsonl").open("w", encoding="utf-8") as fb:
        for card in scan(vault, fmt):
            n_cards += 1
            for j, text in enumerate(card["blocks"]):
                n_blocks += 1
                fb.write(json.dumps({"id": f"{card['ref']}#{j}", "text": text}, ensure_ascii=False) + "\n")
            fc.write(json.dumps(card, ensure_ascii=False) + "\n")
    return {"cards": n_cards, "blocks": n_blocks}


def shape_of(nodes: list[dict], edges: list[dict]) -> str:
    """chain, star, cyclic, tree, dag or empty."""
    ids = {n["id"] for n in nodes}
    indeg, outdeg, deg = Counter(), Counter(), Counter()
    for e in edges:
        outdeg[e["from"]] += 1
        indeg[e["to"]] += 1
        deg[e["from"]] += 1
        deg[e["to"]] += 1
    n, m = len(ids), len(edges)
    if n == 0:
        return "empty"
    if m and all(indeg[i] <= 1 and outdeg[i] <= 1 for i in ids) and m == n - 1:
        return "chain"
    if m >= 3 and (max(deg.values()) if deg else 0) == m:
        return "star"
    adj = defaultdict(list)
    for e in edges:
        adj[e["from"]].append(e["to"])
    color: dict[str, int] = {}

    def cyclic(u: str) -> bool:
        color[u] = 1
        for v in adj[u]:
            if color.get(v) == 1 or (v not in color and cyclic(v)):
                return True
        color[u] = 2
        return False

    if any(cyclic(i) for i in ids if i not in color):
        return "cyclic"
    return "tree" if m == n - 1 else "dag"


def grounding(card: dict[str, Any], nodes: list[dict]) -> float | None:
    """Share of nodes whose label tokens are at least half found in title, essence and propositions."""
    source = tokens(" ".join([card["title"], card["essence"], " ".join(card["propositions"])]))
    scored = grounded = 0
    for n in nodes:
        t = tokens(n.get("label") or "")
        if not t:
            continue
        scored += 1
        if len(t & source) / len(t) >= 0.5:
            grounded += 1
    return round(grounded / scored, 2) if scored else None


def metrics(card: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    nodes, edges = check.get("nodes") or [], check.get("edges") or []
    return {
        "type": check.get("type"), "direction": check.get("direction"),
        "width": check.get("width"), "height": check.get("height"),
        "n_nodes": len(nodes), "n_edges": len(edges),
        "decision_nodes": sum(1 for n in nodes if n.get("shape") == "diamond"),
        "shape": shape_of(nodes, edges),
        "grounding": grounding(card, nodes),
        "edge_labels": [(e.get("label") or "").strip() for e in edges],
        "max_indegree": max(Counter(e["to"] for e in edges).values(), default=0),
    }


def _gain(m: dict[str, Any]) -> str | None:
    if m["type"] in ("sequence", "stateDiagram"):
        return "type"
    if m["decision_nodes"]:
        return "branch"
    if m["shape"] == "cyclic":
        return "loop"
    if m["n_nodes"] >= 5 and m["max_indegree"] >= 2:
        return "merge"
    return None


@dataclass(frozen=True)
class TierRules:
    delete_domains: tuple[str, ...] = ()
    weak_kinds: tuple[str, ...] = ("fact", "contrast")
    max_restating_edges: int = 2
    max_short_chain_nodes: int = 4
    min_grounding: float = 0.5
    max_width: int = 700  # px, the note's reading width; applied to old flowcharts (rule wide)
    # px an upright old flowchart may run over max_width and still not be redrawn: of the 17 redraws judge J9a
    # read, the 6 whose old diagram ran over by 4-20 px came out 4 worse, 2 the same, none better (they lost
    # content to become narrower); the width gate for new diagrams stays max_width
    wide_tolerance: int = 30


def tier_one(card: dict[str, Any], check: dict[str, Any], rules: TierRules = TierRules()) -> tuple[str, str]:
    """(tier, rule) for one card's first diagram."""
    if card["domain"] in rules.delete_domains:
        return "delete", "domain"
    if not check.get("ok"):
        return ("delete", "broken+fact/contrast") if card["kind"] in rules.weak_kinds else ("redraw", "broken")
    if check.get("type") == "flowchart-v2" and (check.get("width") or 0) > rules.max_width + rules.wide_tolerance \
            and check.get("direction") not in ("LR", "RL"):
        return "redraw", "wide"  # turning cannot narrow it; kept as it is, it stays too wide (J8)
    m = metrics(card, check)
    labels = m["edge_labels"]
    same_labels = len(labels) >= 3 and len(set(labels)) == 1
    gain = _gain(m)
    if gain == "type":
        return "keep", "sequence/state"
    if m["n_edges"] <= rules.max_restating_edges:
        return "delete", "le2_edges"
    if m["shape"] == "chain" and m["n_nodes"] <= rules.max_short_chain_nodes:
        return "delete", "short_chain"
    if card["kind"] in rules.weak_kinds and not gain:
        return "delete", "fact/contrast_no_gain"
    if same_labels and not gain:
        return "delete", "star_same_label"
    if gain and (m["grounding"] or 0) >= rules.min_grounding:
        return "keep", gain
    return "review", "gain_low_grounding" if gain else "no_gain"


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


@dataclass
class TierReport:
    counts: Counter = field(default_factory=Counter)
    why: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    cards: dict[str, dict[str, Any]] = field(default_factory=dict)
    repaired: int = 0
    repairs_read: bool = False  # whether audit-repair's results were given (pool, prune, writeback check it)

    def to_dict(self) -> dict[str, Any]:
        return {"counts": dict(self.counts), "why": {t: dict(c) for t, c in self.why.items()},
                **({"repaired": self.repaired} if self.repaired else {}), "repairs_read": self.repairs_read,
                "cards": self.cards}


def graph_of_check(check: dict[str, Any]) -> dict[str, Any]:
    """A flowchart's node labels and edges (as node positions), for comparing it with a new diagram."""
    nodes = check.get("nodes") or []
    index = {n["id"]: i for i, n in enumerate(nodes)}
    return {"labels": [n.get("label") or "" for n in nodes],
            "edges": [[index[e["from"]], index[e["to"]]] for e in check.get("edges") or []
                      if e.get("from") in index and e.get("to") in index]}


def tier(cards: Iterable[dict[str, Any]], checks: Iterable[dict[str, Any]],
         rules: TierRules = TierRules(), repairs: Iterable[dict[str, Any]] | None = None) -> TierReport:
    """Tier every card that has a diagram; checks are mermaid_check results keyed '<ref>#<j>'.

    repairs are audit-repair rows ({"id", "text", "fixes", "check"}); None
    means audit-repair has not run (the report says so). A broken
    diagram whose repair renders is tiered by the repaired check, like any
    diagram that renders, and its entry carries the repair ({"text",
    "fixes"}) so the later steps can write it back where it stays.
    """
    by_id = {c["id"]: c for c in checks}
    fixed = {r["id"]: r for r in repairs or () if (r.get("check") or {}).get("ok")
             and not (r.get("check") or {}).get("render_error")}
    report = TierReport(repairs_read=repairs is not None)
    for card in cards:
        if not card["blocks"]:
            continue
        bid = f"{card['ref']}#0"
        check = by_id.get(bid)
        if check is None:
            raise KeyError(f"no mermaid_check result for {bid}")
        repair = None
        if not check.get("ok") and bid in fixed:
            repair = {"text": fixed[bid]["text"], "fixes": list(fixed[bid].get("fixes") or [])}
            check = fixed[bid]["check"]
        t, why = tier_one(card, check, rules)
        report.counts[t] += 1
        report.why[t][why] += 1
        entry = {"tier": t, "why": why, "kind": card["kind"], "domain": card["domain"],
                 "n_props": len(card["propositions"]), "has_essence": bool(card["essence"].strip()),
                 "sha256": card["sha256"], "ok": bool(check.get("ok"))}
        if check.get("ok"):
            entry.update({k: check.get(k) for k in ("type", "direction", "width", "height")})
            if check.get("type") == "flowchart-v2":  # writeback refuses a rendering flowchart without one
                entry["graph"] = graph_of_check(check)
        if repair:
            entry["repair"] = repair
            report.repaired += 1
        report.cards[card["ref"]] = entry
    return report

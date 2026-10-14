"""Mechanical checks V2-V8, V10-V14 and warnings W1-W2 on a parsed diagram IR.

V1 (shape) lives in ir.parse_ir. V9 (the emitted Mermaid parses in the target
Mermaid build) is a property of the generator, covered by the regression
corpus run through tools/mermaid_check.

| Code | Check |
|---|---|
| V2 | size: flow and structure 4-9 nodes (3 when two paths merge), state 3-8, sequence 2-3 (a fourth participant does not fit 700 px) |
| V3 | every node, labelled edge and message cites existing sources (P1..Pn or E) |
| V4 | label wording is at least half found in the cited text (formula names and symbols count) |
| V5 | structural gain: branch, merge or loop in flow; merge, loop or 3+ links in structure (a plain chain of links cites 3+ sources); 3+ transitions in state; 3+ messages in sequence; a flow whose cited text says the work repeats has its back edge |
| V6 | label widths |
| V7 | structure, state and sequence links are labelled; a decision has 2+ labelled exits |
| V8 | a card with 2+ propositions gets a diagram citing 2+ sources (E counts) |
| V10 | labels are plain text, not TeX |
| V11 | a data diagram (arrows data) starts at inputs and ends at outputs or a final store; in any diagram an input has outgoing edges only, into nodes whose cited text mentions it, and an output incoming edges only |
| V12 | the diagram cites E: it carries the key relation the essence states; replacing an old diagram that renders, it also cites 2 propositions (all, if the card has fewer) |
| V13 | no fan-out into more than 3 leaves, and no decision whose 3+ exits are all leaves (that is a list) |
| V14 | estimated rendered width at most 700 px (the batch path measures the real width instead) |

Thresholds are recommended defaults set from the distribution of existing
diagrams and a 50-card pilot; they are not calibrated.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from .ir import DEFAULT_ARROWS, Card, DiagramIR, Issue, is_ref, parse_ir
from .staleness import cited_refs
from .text import display_width, grounding_tokens, has_tex, overlap, source_tokens, title_match
from .width import estimate_width

# Words that say the work repeats. The prompt lists exactly these (a test compares them);
# the English ones match their inflections (repeats, looping, iteration).
LOOP_WORDS = ("重复", "循环", "反复", "迭代", "不断", "直到", "直至", "递推", "轮询", "往复", "每轮", "逐个", "逐位",
              "repeat", "loop", "until", "iterate")
_LOOP_FORMS = {"每轮": "每一?轮", "逐位": "逐位(?!置)", "repeat": r"\brepeat\w*", "loop": r"\bloop(?:s|ed|ing)?\b",
               "until": r"\buntil\b", "iterate": r"\biterat\w*"}
_LOOP = re.compile("|".join(_LOOP_FORMS.get(w, re.escape(w)) for w in LOOP_WORDS), re.I)
# Uses of those words that do not say this work repeats, cut out first:
# - names of things and formulas (循环队列, 循环群, 双重循环, 循环检测, 递推式, Automatic Repeat reQuest,
#   human-in-the-loop, 持续迭代, 迭代难度);
# - a loop as a failure, or repetition that is prevented or replaced (死循环, 无限循环, 陷入循环, 检测到 Agent 循环,
#   避免每帧重复, 以免重复提交, 防止重复入队, 而不是继续同一循环, 直到超时);
# - negations and questions (不会重复, 是否重复, 不必轮询, without repeating);
# - duplicates and repetition as a noun in a list (重复项, 重复的, 重复惩罚, 去重复, 造成重复, 如重复、不通顺,
#   失序和重复。, 重复率);
# - 不断 for a trend rather than a step (不断衰减, 不断增大).
_NOT_LOOPS = re.compile(
    r"循环(?:神经网络|队列|冗余|移位|[左右]移|卷积|小数|层(?!级)|链表|依赖|引用|赛|检测|上限|群)|[双多]重循环|嵌套循环"
    r"|(?:持续|快速|敏捷|版本|产品)迭代|迭代(?:难度|速度|周期|版本)"
    r"|递推(?:式|公式|关系)"
    r"|死循环|无限?循环|(?:避免|以免|防止|禁止|免得|陷入|导致|出现|检测到|而不是|不是)[^。；;，,]{0,20}?"
    r"(?:重复|循环|反复|迭代|轮询|递推)"
    r"|直[到至]超时"
    r"|(?:不|没有?|未|无(?!限)|勿|别|是否)(?:会|要|必|再|需|用|能|可|应|该|必要)?(?:重复|循环|反复|迭代|轮询|递推)"
    r"|重复(?:的|项|值|元素|数据|记录|内容|条目|样本|信息|模板|行|惩罚|劳动|提交|入队|问题|(?=[、）)]))"
    r"|(?:去|去除|删除|丢弃|过滤|消除|剔除|造成|完全)重复|[的、和与及或]重复(?=[。；;，,、）)]|$)|重复(?:率|度|部件)"
    r"|不断(?:衰减|减小|减少|下降|降低|增大|增加|增长|上升|升高|提高|扩大|缩小|变化|发展|演进|演化|加深|加剧|膨胀)"
    r"|\brepeat\s+request|\bin[- ]the[- ]loop\b|\binfinite\s+loops?\b"
    r"|\b(?:not|no|never|without|avoid\w*|prevent\w*|detect\w*|don't|doesn't|won't)\s+(?:\w+\s+){0,2}?"
    r"(?:repeat\w*|loop\w*|iterat\w*)",
    re.I)

# Words that name the role, not the data; they do not show where an input is used.
_GENERIC_IO = ("输入", "输出", "数据", "信号", "input", "output", "data", "signal")
_GENERIC_IO_RE = re.compile("|".join(rf"\b{w}s?\b" if w.isascii() else w for w in _GENERIC_IO), re.I)

RELATION_WORDS = frozenset({
    "part_of", "has_part", "depends_on", "required_by", "similar_to", "contrasts_with",
    "generalizes", "specializes", "follows", "precedes",
    "依赖", "属于", "相似", "类似", "对比", "推广", "泛化", "特化", "先修", "前置", "组成", "包含", "相关",
})


@dataclass(frozen=True)
class Limits:
    """Validator thresholds. Widths are in full-width character units (Latin counts half)."""

    size: dict[str, tuple[int, int]] = field(default_factory=lambda: {
        "flow": (4, 9), "structure": (4, 9), "state": (3, 8), "sequence": (2, 3)})
    merge_min_nodes: int = 3
    grounding_min: float = 0.5
    node_label_max: float = 20
    decision_label_max: float | None = 10
    edge_label_max: float = 14
    message_max: float = 20
    min_messages: int = 3
    min_links: dict[str, int] = field(default_factory=lambda: {"structure": 3, "state": 3})
    labeled_links: tuple[str, ...] = ("structure", "sequence", "state")
    min_cited_sources: int = 2
    chain_min_sources: int = 3
    max_leaf_fanout: int = 3
    max_width: float | None = 700
    require_essence: bool = True
    require_back_edge: bool = True
    exempt_labels: frozenset[str] = frozenset({
        "是", "否", "开始", "结束", "成功", "失败",
        "yes", "no", "start", "end", "success", "failure", "true", "false"})


DEFAULT_LIMITS = Limits()


@dataclass
class Report:
    ir: DiagramIR | None
    errors: list[Issue]
    warnings: list[Issue]

    @property
    def ok(self) -> bool:
        return not self.errors


def _items(ir: DiagramIR) -> list[tuple[str, str, str, tuple[str, ...], bool]]:
    """(kind, where, label, refs, citation_required) for every labelled element."""
    out = []
    for i, n in enumerate(ir.nodes):
        out.append(("decision" if n.role == "decision" else "node", f"nodes[{i}].label", n.label, n.p, True))
    if ir.form == "sequence":
        for i, m in enumerate(ir.messages):
            out.append(("message", f"messages[{i}].text", m.label, m.p, True))
    else:
        for i, e in enumerate(ir.edges):
            if e.label:
                out.append(("edge", f"edges[{i}].label", e.label, e.p, True))
    for i, g in enumerate(ir.groups):
        out.append(("group", f"groups[{i}].label", g.label, g.p, bool(g.p)))
    return out


def _has_cycle(keys: list[str], outs: dict[str, set[str]]) -> bool:
    color: dict[str, int] = {}

    def visit(u: str) -> bool:
        color[u] = 1
        for v in outs.get(u, ()):
            if color.get(v) == 1 or (v not in color and visit(v)):
                return True
        color[u] = 2
        return False

    return any(k not in color and visit(k) for k in keys)


@dataclass(frozen=True)
class _Shape:
    """Graph facts over distinct (from, to) pairs: a repeated edge is not a merge."""

    outs: dict[str, set[str]]
    indeg: dict[str, int]
    merge: bool
    loop: bool

    @classmethod
    def of(cls, ir: DiagramIR) -> "_Shape":
        pairs = {(e.src, e.dst) for e in ir.links if not getattr(e, "note", False)}
        outs: dict[str, set[str]] = defaultdict(set)
        indeg: dict[str, int] = defaultdict(int)
        for src, dst in pairs:
            outs[src].add(dst)
            if src != dst:
                indeg[dst] += 1
        loop = _has_cycle([n.key for n in ir.nodes], outs)
        return cls(outs, indeg, any(v >= 2 for v in indeg.values()), loop)


def _branches(ir: DiagramIR) -> bool:
    exits: dict[str, list] = defaultdict(list)
    for e in ir.edges:
        exits[e.src].append(e)
    return any(n.role == "decision" and len({e.dst for e in exits[n.key]}) >= 2
               and all(e.label for e in exits[n.key]) for n in ir.nodes)


def says_repeat(card: Card, refs: Iterable[str] | None = None) -> tuple[str, str] | None:
    """(ref, quoted context) for the first cited text that says the work repeats, if any.

    refs are the sources the diagram cites; None reads the whole card. Each
    source is read on its own, after cutting out names, negations and
    duplicates (_NOT_LOOPS).
    """
    order = ["E", *(f"P{i}" for i in range(1, len(card.propositions) + 1))]
    wanted = set(order) if refs is None else set(refs)
    for ref in order:
        if ref not in wanted:
            continue
        src = card.source(ref)
        m = _LOOP.search(_NOT_LOOPS.sub(lambda x: " " * len(x.group()), src))
        if m:
            a, b = max(0, m.start() - 12), min(len(src), m.end() + 12)
            return ref, ("…" if a else "") + src[a:b].strip() + ("…" if b < len(src) else "")
    return None


def strong_enough(refs: Iterable[str], n_props: int, has_essence: bool = True) -> bool:
    """Whether a diagram citing refs may replace an old diagram that renders.

    It must cite E (when the card has an essence) and at least 2 propositions
    (all of them, if the card has fewer). The validator (V12) and the
    write-back guard use this one rule.
    """
    refs = set(refs)
    props = [r for r in refs if r != "E"]
    return ("E" in refs or not has_essence) and len(props) >= min(2, n_props)


def _gain(ir: DiagramIR, shape: _Shape, limits: Limits, n_cited: int = 0) -> str | None:
    """V5: why this diagram says more than a sentence would; None if it does not."""
    if ir.form == "flow":
        if _branches(ir) or shape.merge or shape.loop:
            return None
        return ("flow has no decision with 2 labelled exits, no merge and no loop: a chain of steps, or paths that "
                "split and never meet again, reads as well as a list")
    if ir.form == "sequence":
        real = [m for m in ir.messages if not m.note]
        if len(real) < limits.min_messages:
            return f"sequence has {len(real)} messages, needs at least {limits.min_messages}"
        return None
    if ir.form == "structure" and (shape.merge or shape.loop):
        return None
    need = limits.min_links.get(ir.form or "", 0)
    links = len({(e.src, e.dst) for e in ir.edges})
    if links < need:
        what = "relations between parts and no merge" if ir.form == "structure" else "transitions"
        return f"{ir.form} has {links} {what}, needs at least {need}; a list says the same"
    chain = all(len(v - {k}) <= 1 for k, v in shape.outs.items()) and not shape.merge and not shape.loop
    if ir.form == "structure" and chain and n_cited < limits.chain_min_sources:
        return (f"structure is a plain chain citing {n_cited} source(s); a chain of links needs "
                f"{limits.chain_min_sources} (one sentence or one formula reads better as text)")
    return None


def check(ir: DiagramIR, card: Card, limits: Limits = DEFAULT_LIMITS) -> tuple[list[Issue], list[Issue]]:
    """Run V2-V8, V10-V14 and W1-W2 on a parsed draw IR. Returns (errors, warnings)."""
    errors: list[Issue] = []
    warnings: list[Issue] = []
    if ir.decision != "draw":
        return errors, warnings
    form = ir.form or ""
    shape = _Shape.of(ir)

    lo, hi = limits.size[form]
    if form in ("flow", "structure") and shape.merge:
        lo = min(lo, limits.merge_min_nodes)
    if not lo <= len(ir.nodes) <= hi:
        what = "participants" if form == "sequence" else ("states" if form == "state" else "nodes")
        errors.append(Issue("V2", f"{len(ir.nodes)} {what}; {form} needs {lo}-{hi} "
                                  f"(fewer restates the text, more should be split or written as text)"))

    valid = card.refs
    cited: set[str] = set()
    for kind, where, label, refs, required in _items(ir):
        if required or refs:
            bad = [r for r in refs if not is_ref(r) or r not in valid]
            if not refs:
                errors.append(Issue("V3", f"«{label}» cites nothing; cite P1..Pn or E", where))
            elif bad:
                errors.append(Issue("V3", f"«{label}» cites {bad}; this card has {sorted(valid, key=_ref_order)}", where))
            else:
                cited.update(refs)
                if label.strip().lower() not in limits.exempt_labels:
                    src = " ".join(card.source(r) for r in refs)
                    share = overlap(label, src)
                    if share is None or share < limits.grounding_min:
                        pct = "no" if share is None else f"{share:.0%}"
                        errors.append(Issue("V4", f"«{label}» shares {pct} wording with {list(refs)}; "
                                                  f"needs {limits.grounding_min:.0%}. Reuse the card's words", where))
        if has_tex(label):
            errors.append(Issue("V10", f"«{label}» contains TeX; write formula terms as plain text "
                                       f"such as f(y|x), x_t, x², Σ x_i, √d", where))
        cap = {"node": limits.node_label_max, "group": limits.node_label_max,
               "decision": limits.decision_label_max or limits.node_label_max,
               "edge": limits.edge_label_max, "message": limits.message_max}[kind]
        w = display_width(label)
        if w > cap:
            errors.append(Issue("V6", f"«{label}» is {w:g} wide, limit {cap:g} for a {kind} label", where))

    gain = _gain(ir, shape, limits, len(cited))
    if gain:
        errors.append(Issue("V5", gain + "; consider skip"))
    all_cited = [r for r in cited_refs(ir) if r in valid]
    repeat = says_repeat(card, all_cited) if form == "flow" and limits.require_back_edge else None
    if repeat and not shape.loop:
        errors.append(Issue("V5", f"{repeat[0]}, which the diagram cites, may say the work repeats («{repeat[1]}»). "
                                  f"If it describes this work repeating, draw that back edge, from where one round "
                                  f"ends to the step that repeats. If it does not, cite other text or skip; never add "
                                  f"a loop the card does not state"))

    if form in limits.labeled_links:
        for i, e in enumerate(ir.links):
            if not e.label:
                errors.append(Issue("V7", f"{form} links must say what they mean",
                                    f"{'messages' if form == 'sequence' else 'edges'}[{i}]"))
    if form == "flow":
        roles = {n.key: n.role for n in ir.nodes}
        for i, e in enumerate(ir.edges):
            if roles.get(e.src) == "decision" and not e.label:
                errors.append(Issue("V7", "edges leaving a decision must state their condition", f"edges[{i}]"))
        for i, n in enumerate(ir.nodes):
            if n.role == "decision" and len(shape.outs.get(n.key, ())) < 2:
                errors.append(Issue("V7", f"decision «{n.label}» has {len(shape.outs.get(n.key, ()))} stated "
                                          f"outcome(s); a decision needs 2 or more, otherwise make it a step",
                                    f"nodes[{i}]"))

    if limits.require_essence and card.essence.strip() and "E" not in cited:
        errors.append(Issue("V12", "the diagram does not cite E; find the relation the essence states, draw it, "
                                   "and cite E on the nodes or edges that carry it"))
    elif card.old_diagram == "renders" and not strong_enough(all_cited, len(card.propositions),
                                                             bool(card.essence.strip())):
        need = min(2, len(card.propositions))
        errors.append(Issue("V12", f"this card's old diagram renders; a diagram that replaces it cites E and "
                                   f"{need} proposition(s), and this one cites {all_cited}: draw E's relation "
                                   f"with the propositions that state it, or skip"))

    for i, n in enumerate(ir.nodes):
        children = shape.outs.get(n.key, set()) - {n.key}
        leaves = [c for c in children if not (shape.outs.get(c, set()) - {c})]
        if len(leaves) > limits.max_leaf_fanout:
            errors.append(Issue("V13", f"«{n.label}» fans out to {len(leaves)} leaves: a list laid sideways; "
                                       f"write it as text or chain the items", f"nodes[{i}]"))
        elif n.role == "decision" and len(children) >= 3 and len(leaves) == len(children):
            errors.append(Issue("V13", f"decision «{n.label}» only lists {len(children)} parallel options; "
                                       f"options are not a decision", f"nodes[{i}]"))

    width = estimate_width(ir) if limits.max_width else None
    if width and width > limits.max_width:
        errors.append(Issue("V14", f"estimated {width:.0f} px wide, limit {limits.max_width:.0f}: fewer nodes side "
                                   f"by side, shorter labels, or fewer participants"))

    if len(card.propositions) >= 2 and len(cited) < limits.min_cited_sources:
        errors.append(Issue("V8", f"diagram cites {len(cited)} source(s) of {len(card.propositions)} propositions "
                                  f"and the essence; a one-source diagram restates the text, consider skip"))

    if form in DEFAULT_ARROWS:
        arrows = ir.arrows or DEFAULT_ARROWS[form]
        inputs = [n for n in ir.nodes if n.role == "input"]
        outputs = [n for n in ir.nodes if n.role == "output"]
        sinks = outputs or [n for n in ir.nodes if n.role == "store" and shape.indeg.get(n.key)
                            and not (shape.outs.get(n.key, set()) - {n.key})]
        if arrows == "data" and (not inputs or not sinks):
            errors.append(Issue("V11", "a data diagram shows where data enters (role input) and where it "
                                       "leaves (role output, or a final store)"))
        for n in inputs:
            where = f"nodes[{ir.nodes.index(n)}]"
            if shape.indeg.get(n.key):
                errors.append(Issue("V11", f"input «{n.label}» has an incoming edge", where))
            if not shape.outs.get(n.key):
                errors.append(Issue("V11", f"input «{n.label}» feeds nothing; add its outgoing edge", where))
            # generic words go before tokenising, so 输入数据 leaves nothing rather than 入数
            words = grounding_tokens(_GENERIC_IO_RE.sub(" ", n.label))
            for e in ir.edges:
                if e.src != n.key or not words:
                    continue
                target = next(x for x in ir.nodes if x.key == e.dst)
                refs = [r for r in (*target.p, *e.p) if r in card.refs]
                if refs and not words & source_tokens(" ".join(card.source(r) for r in refs)):
                    errors.append(Issue("V11", f"input «{n.label}» goes to «{target.label}», but the text that node "
                                               f"and edge cite ({', '.join(refs)}) never mentions it; connect each "
                                               f"input only to the nodes that use it, and cite the text that says "
                                               f"so on that node or edge", where))
        for n in outputs:
            where = f"nodes[{ir.nodes.index(n)}]"
            if shape.outs.get(n.key):
                errors.append(Issue("V11", f"output «{n.label}» has an outgoing edge", where))
            if not shape.indeg.get(n.key):
                errors.append(Issue("V11", f"output «{n.label}» receives nothing; add its incoming edge", where))

    for i, n in enumerate(ir.nodes):
        if title_match(n.label, list(card.related_titles)):
            warnings.append(Issue("W1", f"«{n.label}» names a related card; card-to-card links belong "
                                        f"in the relations view, not in this diagram", f"nodes[{i}].label"))
    for i, e in enumerate(ir.links):
        if e.label.strip().lower() in RELATION_WORDS:
            warnings.append(Issue("W2", f"«{e.label}» is a relation type, not what happens on this link",
                                  f"{'messages' if form == 'sequence' else 'edges'}[{i}]"))
    return errors, warnings


def _ref_order(r: str) -> tuple[int, int]:
    return (0, 0) if r == "E" else (1, int(r[1:]))


def validate(data: Any, card: Card | dict, limits: Limits = DEFAULT_LIMITS) -> Report:
    """Parse (V1) and check (V2-V8, V10-V14) one IR against its card."""
    if isinstance(card, dict):
        card = Card.from_dict(card)
    ir, issues = parse_ir(data)
    errors = [i for i in issues if i.is_error]
    warnings = [i for i in issues if not i.is_error]
    if ir is not None:
        e, w = check(ir, card, limits)
        errors += e
        warnings += w
    return Report(ir=ir, errors=errors, warnings=warnings)

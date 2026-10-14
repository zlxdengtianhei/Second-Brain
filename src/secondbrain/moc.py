"""Deterministic MOC projection for the SecondBrain vault.

Owned by the "moc-impl" work slice (see INTERFACES.md). ``build_mocs`` turns a
read-only :class:`~secondbrain.vault.Snapshot` into a purely byte-level
*change set* (``Projection.files``) of generated MOC pages under ``20_MOCs``.
It never writes to disk, never calls a model and never edits the curation YAML.

The pages mirror the curation taxonomy (``20_MOCs/_pillars/<MOC title>.yaml``)
read-only: the root page carries the narrative, the pillar/topic entries with
card counts and the cross-MOC bridges; pillar pages list cards per topic; a
residual section holds every card that is not yet classified into a valid
(pillar, topic) pair, so no input card ever disappears.

Design notes relevant to consumers:

* ``Projection.files`` contains **only** generated markdown under ``20_MOCs``
  (root ``20_MOCs/<title>.md`` plus ``20_MOCs/<title>/NN<stem>--NN.md`` pages).
  Store writes these; nothing else (old pages, HOME) is touched here.
* A MOC is rebuilt atomically: an ``affected_domains`` entry expands to every
  domain that shares that MOC (so editing a card of ``attention`` rebuilds the
  MOC also used by ``transformer``).
* Only ``depends_on`` / ``follows`` / ``required_by`` / ``precedes`` decide
  learning order; every other relation type is rendered but never orders cards.

Only Python standard library + PyYAML is used.
"""
from __future__ import annotations

import heapq
import re
from dataclasses import dataclass

from . import labels

_PREREQ_TYPES = frozenset(("depends_on", "follows", "required_by", "precedes"))

MOC_ROOT = "20_MOCs"

# Per-page budgets.
MAX_LINES = 300    # estimated visual lines target (with margin)
MAX_CARDS = 120    # hard card cap
MAX_TOPIC = 25     # a single topic's cards listed on one page

#: Frontmatter on domain and pillar pages: Breadcrumbs reads their list
#: items as "down" links, so MOC pages form the hierarchy above the cards.
BC_FRONTMATTER = "---\nBC-list-note-field: down\n---\n"
_FALLBACK = "pillar"


class MocError(ValueError):
    """Structural errors produced by this module (never a semantic judgement)."""


@dataclass(frozen=True)
class Projection:
    """Byte change set produced by :func:`build_mocs`.

    ``files`` maps vault-relative path -> bytes (only ``20_MOCs`` markdown).
    ``metrics`` carries card coverage IDs, page counts and per-page line/card
    limits; ``issues`` records cycles, illegal classifications, unknown targets
    and other conditions without ever dropping a card.
    """

    files: dict
    metrics: dict
    issues: list


# --------------------------------------------------------------------------
# Safe file naming
# --------------------------------------------------------------------------
_UNSAFE_SEG_RE = re.compile(r"[/\\:*?\"<>|\x00-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")


def _safe_segment(value, fallback):
    """One safe path element from an arbitrary title / pillar name."""
    text = str(value)
    text = _UNSAFE_SEG_RE.sub("_", text)
    text = _WS_RE.sub(" ", text).strip(" .")
    if not text or text in (".", ".."):
        text = fallback
    return text


def _pillar_stem(name):
    return _safe_segment(_WS_RE.sub("", name), _FALLBACK)


def _pillar_filename(order, stem, page):
    return "%02d%s--%02d.md" % (order, stem, page)


# --------------------------------------------------------------------------
# Wiki links (vault-root relative path + alias to avoid name collisions)
# --------------------------------------------------------------------------
def _alias(title):
    t = str(title).replace("|", labels.moc()["alias_pipe"]).replace("[", "").replace("]", "")
    return t


def _link(relpath, alias):
    return "[[%s|%s]]" % (relpath, _alias(alias))


# --------------------------------------------------------------------------
# Learning order (only the four prerequisite edges)
# --------------------------------------------------------------------------
def _prereq_edges(cards):
    """``{src: {dst}}``: ``src`` must be learned before ``dst``, from the four prerequisite edges among ``cards``."""
    by_id = {c.id for c in cards}
    adj = {}
    for c in cards:
        for rel in c.fm.get("relations") or ():
            if not isinstance(rel, dict):
                continue
            typ = rel.get("type")
            tgt = rel.get("target")
            if typ not in _PREREQ_TYPES:
                continue
            if not isinstance(tgt, str) or tgt not in by_id or tgt == c.id:
                continue
            # src must be learned before dst.
            if typ in ("required_by", "precedes"):  # c before tgt
                src, dst = c.id, tgt
            else:                                     # depends_on / follows: tgt before c
                src, dst = tgt, c.id
            adj.setdefault(src, set()).add(dst)
    return adj


def prerequisite_cycles(cards):
    """Groups of two or more cards whose prerequisite links lead from each card back to itself.

    The one definition of a learning-order cycle that `sb refresh`, `sb status`,
    `sb daily` and `sb review queue` report: the strongly connected groups of
    the graph of the four prerequisite edges among ``cards`` (as in
    :func:`learning_order`). A card that only depends on a cycle, or that a
    cycle depends on, is not in it. Each group is sorted by id; groups by their
    first id.
    """
    adj = _prereq_edges(cards)
    index, low, on_stack, stack, groups = {}, {}, set(), [], []
    for root in sorted(c.id for c in cards):
        if root in index:
            continue
        work = [(root, iter(sorted(adj.get(root, ()))))]
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is not None:
                if child not in index:
                    index[child] = low[child] = len(index)
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(adj.get(child, ())))))
                elif child in on_stack:
                    low[node] = min(low[node], index[child])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                group = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    group.append(member)
                    if member == node:
                        break
                if len(group) > 1:
                    groups.append(sorted(group))
    return sorted(groups)


def cycle_issues(cards, moc_of=None):
    """One ``learning_order_cycle`` issue per group of :func:`prerequisite_cycles`."""
    out = []
    for group in prerequisite_cycles(cards):
        issue = {"code": "learning_order_cycle", "card_ids": group,
                 "detail": "These cards' prerequisite links lead back to themselves, so no order puts every "
                           "prerequisite first; every card is kept and the group follows in card id order"}
        if moc_of is not None:
            issue["mocs"] = sorted({moc_of[c] for c in group if c in moc_of})
        out.append(issue)
    return out


def learning_order(cards):
    """Return ``(ordered_cards, issues)`` — a stable prerequisite topo sort.

    Only ``depends_on`` / ``follows`` / ``required_by`` / ``precedes`` create a
    before/after ordering; every other relation type is ignored here. Edges are
    oriented "must be learned first" -> "must be learned later" and ties break
    by card id. Each cycle (:func:`prerequisite_cycles`) is reported as an issue
    but **no card is dropped**: the cards Kahn's sort cannot place are appended
    in stable id order.
    """
    by_id = {c.id: c for c in cards}
    adj = _prereq_edges(cards)
    indeg = {cid: 0 for cid in by_id}
    for outs in adj.values():
        for dst in outs:
            indeg[dst] += 1

    ready = [cid for cid, d in indeg.items() if d == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        nid = heapq.heappop(ready)
        order.append(nid)
        for d in sorted(adj.get(nid, ())):
            indeg[d] -= 1
            if indeg[d] == 0:
                heapq.heappush(ready, d)

    seen = set(order)
    remaining = sorted(cid for cid in by_id if cid not in seen)
    issues = cycle_issues(cards) if remaining else []
    order.extend(remaining)
    return [by_id[cid] for cid in order], issues


# --------------------------------------------------------------------------
# Relations / classification
# --------------------------------------------------------------------------
def _relations(card):
    rels = card.fm.get("relations")
    if isinstance(rels, list):
        return [r for r in rels if isinstance(r, dict)]
    return []


def _classify(card, pillar_topics):
    """Return ``("missing"|"invalid"|"classified", note_or_None)``.

    ``pillar_topics`` maps valid pillar name -> set of valid topic names. A
    missing pillar/topic is *unclassified*; a pillar or topic not in this MOC's
    taxonomy is *illegal* (kept here so the residual lists it, never dropped).
    The note is ``(label key, args)``; :func:`_note` renders it in a language.
    """
    p = card.fm.get("pillar")
    t = card.fm.get("topic")
    if not p and not t:
        return "missing", ("missing_both", ())
    if not p:
        return "missing", ("missing_pillar", ())
    if not t:
        return "missing", ("missing_topic", ())
    if p not in pillar_topics:
        return "invalid", ("bad_pillar", (p,))
    if t not in pillar_topics[p]:
        return "invalid", ("bad_topic", (t, p))
    return "classified", None


def _note(note, lang=None):
    key, args = note
    return labels.moc(lang)[key] % args


def _taxonomy_structure(taxonomy, moc_title, issues):
    """Normalise a taxonomy dict (read-only) for one MOC.

    Returns ``(pillars, pillar_topics, narrative, bridges)``. ``pillars`` is a
    list of ``(name, order, why, topics)`` ordered by ``order`` (falling back to
    list position, so filenames stay stable). A missing/illegal taxonomy is an
    explicit issue; the MOC still keeps all its cards (in the residual).
    """
    if not isinstance(taxonomy, dict):
        if taxonomy is None:
            issues.append({"code": "missing_taxonomy", "moc": moc_title,
                           "detail": "No taxonomy file (<taxonomy folder>/<map title>.yaml); "
                                     "the cards are listed as unclassified"})
        return [], {}, "", []
    narr = taxonomy.get("narrative") or ""
    bridges = taxonomy.get("bridges")
    bridges = bridges if isinstance(bridges, list) else []
    raw = taxonomy.get("pillars")
    pillars = []
    pillar_topics = {}
    for idx, p in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(p, dict) or not p.get("name"):
            continue
        name = p["name"]
        try:
            order = int(p.get("order")) if p.get("order") is not None else idx + 1
        except (TypeError, ValueError):
            order = idx + 1
        topics = p.get("topics")
        topics = [t for t in (topics if isinstance(topics, list) else []) if isinstance(t, str)]
        why = p.get("why") or ""
        pillars.append((name, order, why, topics))
        pillar_topics[name] = set(topics)
    pillars.sort(key=lambda x: (x[1], x[0]))
    return pillars, pillar_topics, narr, bridges


# --------------------------------------------------------------------------
# Page slicing
# --------------------------------------------------------------------------
def _slice_entries(entries):
    """Slice ``[(topic, [cards])]`` into pages respecting the three budgets.

    A topic longer than ``MAX_TOPIC`` continues onto the next page (page number
    is a browse slice within the same pillar, never a new semantic level).
    """
    pages = []
    cur, cur_cards, cur_lines = [], 0, 5
    for topic, cards in entries:
        pos, n = 0, len(cards)
        while pos < n:
            already = sum(len(c) for t, c in cur if t == topic)
            allow = min(MAX_TOPIC - already, MAX_CARDS - cur_cards,
                        MAX_LINES - cur_lines - 1)
            if allow <= 0:
                if cur:
                    pages.append(cur)
                cur, cur_cards, cur_lines = [], 0, 5
                continue
            if cur_lines + 1 + allow > MAX_LINES:
                if cur:
                    pages.append(cur)
                cur, cur_cards, cur_lines = [], 0, 5
                continue
            if not cur or cur[-1][0] != topic:
                cur.append([topic, []])
                cur_lines += 1  # topic header line
            take = cards[pos:pos + allow]
            cur[-1][1].extend(take)
            cur_cards += len(take)
            cur_lines += len(take)
            pos += len(take)
    if cur:
        pages.append(cur)
    return pages


def _chunk_cards(items):
    """Slice a flat ``[(card, note)]`` list into pages under card/line caps."""
    out = []
    cur, lines = [], 6
    for it in items:
        if cur and (len(cur) >= MAX_CARDS or lines + 1 > MAX_LINES):
            out.append(cur)
            cur, lines = [], 6
        cur.append(it)
        lines += 1
    if cur:
        out.append(cur)
    return out


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def _card_line(card, ctx, cur_moc, domain_to_moc):
    """One card line: link + compact inline relation annotation.

    Every relation type present is shown (none silently dropped); target links
    use the vault-relative path + alias; a target in a *different* MOC carries the
    ``cross_map`` label (``［跨域→…］`` in zh) so a prerequisite / bridge keeps its
    contextual meaning.
    """
    L = labels.moc()
    parts = [_link(card.path, card.title)]
    per_type = {}
    for rel in _relations(card):
        typ, tgt = rel.get("type"), rel.get("target")
        if isinstance(typ, str) and isinstance(tgt, str) and tgt in ctx:
            per_type.setdefault(typ, []).append(tgt)
    for typ in sorted(per_type):
        toks = []
        for tgt in sorted(per_type[typ]):
            rpath, rtitle, tdom = ctx[tgt]
            tmoc = domain_to_moc.get(tdom)
            suffix = "" if tmoc == cur_moc else L["cross_map"] % (tmoc or tdom)
            toks.append(_link(rpath, rtitle) + suffix)
        parts.append("%s→ %s" % (typ, L["target_sep"].join(toks)))
    return "- " + L["card_sep"].join(parts)


def _render_residual(moc_title, header, chunk, root_rel, ctx, domain_to_moc):
    L = labels.moc()
    text = [L["residual_title"] % moc_title, "", header, ""]
    for card, note in chunk:
        text.append(_card_line(card, ctx, moc_title, domain_to_moc) + L["card_sep"] + "_%s_" % note)
    text.append("")
    text.append("---")
    text.append(L["navigation"])
    text.append("")
    text.append(_link(root_rel, L["back_to_root"]))
    text.append("")
    return "\n".join(text) + "\n"


def _render_root(moc_title, moc_domains, narrative, pillars, pillar_counts,
                 pillar_links, residual_link_label, residual_total, bridges,
                 root_rel, flat_page_links):
    L = labels.moc()
    text = [BC_FRONTMATTER + "# %s" % moc_title, ""]
    text.append(L["covers"] % (L["domain_sep"].join(moc_domains), len(flat_page_links) + 1))
    text.append("")
    if narrative:
        text.append(L["narrative"])
        text.append("")
        text.append(narrative)
        text.append("")
    text.append(L["pillars"])
    text.append("")
    for (name, order), count in pillar_counts.items():
        links = pillar_links[(name, order)]
        l0 = _link(links[0], "%02d·%s" % (order, name))
        line = L["pillar_line"] % (l0, count)
        if len(links) > 1:
            extra = " · ".join(_link(p, "p%d" % i) for i, p in enumerate(links, 1))
            line += L["pages"] + extra
        text.append(line)
    if residual_total:
        text.append(L["residual_line"] % (_link(residual_link_label[1], L["residual"]), residual_total))
    text.append("")
    if bridges:
        text.append(L["bridges"])
        text.append("")
        for br in bridges:
            if not isinstance(br, dict):
                continue
            bmoc = br.get("moc")
            bw = br.get("why") or ""
            blinks = br.get("links")
            blinks = [b for b in (blinks if isinstance(blinks, list) else []) if isinstance(b, str)]
            text.append("- **%s**" % (bmoc if bmoc else L["unknown"]))
            if bw:
                text.append("  %s" % bw)
            for b in blinks:
                text.append("  - %s" % b)
            text.append("")
    text.append(L["page_index"])
    text.append("")
    text.append(L["this_page"] % root_rel)
    for label, rel in flat_page_links:
        text.append("- [[%s|%s]]" % (rel, label))
    text.append("")
    return "\n".join(text) + "\n"


# --------------------------------------------------------------------------
# Per-MOC build
# --------------------------------------------------------------------------
def _process_moc(snapshot, moc_title, moc_domains, domain_to_moc, issues):
    domain_set = set(moc_domains)
    cards = [c for c in snapshot.cards.values() if c.domain in domain_set]
    ordered, _ = learning_order(cards)  # cycles are reported once for the whole vault (build_mocs)

    ctx = {cid: (c.path, c.title, c.domain) for cid, c in snapshot.cards.items()}
    taxonomy = snapshot.taxonomies.get(moc_title)
    pillars, pillar_topics, narrative, bridges = _taxonomy_structure(
        taxonomy, moc_title, issues)

    buckets = {}
    missing = []
    invalid = []
    for card in ordered:
        state, note = _classify(card, pillar_topics)
        if state == "classified":
            buckets.setdefault((card.fm["pillar"], card.fm["topic"]), []).append(card)
        elif state == "missing":
            missing.append((card, _note(note)))
        else:
            invalid.append((card, _note(note)))
            issues.append({"code": "invalid_classification", "card": card.id,
                           "detail": _note(note, "en"), "moc": moc_title, "domain": card.domain})
    if missing:
        issues.append({"code": "unclassified", "moc": moc_title, "count": len(missing),
                       "detail": "pillar or topic missing; listed on the unclassified pages and counted under no pillar"})

    seg = _safe_segment(moc_title, "moc")
    moc_root = snapshot.layout.mocs if getattr(snapshot, "layout", None) else MOC_ROOT
    prefix = "%s/%s/" % (moc_root, seg)
    root_rel = "%s/%s.md" % (moc_root, seg)

    files = {}
    flat = []  # list of _PageLink in pagination order (non-root)

    # Pillar pages.
    pillar_counts = {}
    pillar_links = {}
    for name, order, why, topics in pillars:
        entries = [(t, list(buckets[(name, t)])) for t in topics if (name, t) in buckets]
        stem = _pillar_stem(name)
        slice_pages = _slice_entries(entries)
        if not slice_pages:
            # A pillar with no assigned cards still gets one page so the root
            # page has a stable link and every pillar stays reachable.
            slice_pages = [[]]
        total = len(slice_pages)
        links = []
        for pi, pg in enumerate(slice_pages, start=1):
            rel = prefix + _pillar_filename(order, stem, pi)
            text = _render_pillar_pages_here(
                rel, moc_title, name, why, pg, pi, total, root_rel, ctx, domain_to_moc,
                order, stem)
            files[rel] = text.encode("utf-8")
            links.append(rel)
            cards_n = sum(len(c) for _, c in pg)
            max_topic = max((len(c) for _, c in pg), default=0)
            lines = text.count("\n") + 1
            flat.append(_PageLink(labels.moc()["page_label"] % pi, rel, order, name, cards_n,
                                  max_topic, lines))
        count = sum(len(v) for k, v in buckets.items() if k[0] == name)
        pillar_counts[(name, order)] = count
        pillar_links[(name, order)] = links

    # Residual pages.
    residual_total = len(missing) + len(invalid)
    residual_page_no = 0
    L = labels.moc()
    for header, group in ((L["missing_header"], missing), (L["invalid_header"], invalid)):
        if not group:
            continue
        for chunk in _chunk_cards(group):
            residual_page_no += 1
            rel = prefix + _pillar_filename(0, L["residual"], residual_page_no)
            text = _render_residual(moc_title, header, chunk, root_rel,
                                    ctx, domain_to_moc)
            files[rel] = text.encode("utf-8")
            lines = text.count("\n") + 1
            flat.append(_PageLink("%s #%d" % (L["residual"], residual_page_no), rel,
                                  None, None, len(chunk), 0, lines))

    # Root page.
    flat_links = [(pl.label, pl.relpath) for pl in flat]
    residual_link = None
    for pl in flat:
        if pl.label.startswith(L["residual"]):
            residual_link = (pl.label, pl.relpath)
            break
    root_text = _render_root(moc_title, moc_domains, narrative, pillars,
                             pillar_counts, pillar_links, residual_link,
                             residual_total, bridges, root_rel, flat_links)
    files[root_rel] = root_text.encode("utf-8")

    pages_metrics = [{"path": root_rel, "label": "root", "cards": 0,
                      "max_topic_len": 0,
                      "visual_lines": root_text.count("\n") + 1}]
    for pl in flat:
        pages_metrics.append({"path": pl.relpath, "label": pl.label,
                              "cards": pl.count, "max_topic_len": pl.max_topic,
                              "visual_lines": pl.lines})

    metrics = {
        "domains": list(moc_domains),
        "card_count": len(cards),
        "classified": sum(len(v) for v in buckets.values()),
        "unclassified": len(missing),
        "invalid": len(invalid),
        "page_count": len(files),
        "root": root_rel,
        "card_ids": sorted(c.id for c in cards),
        "pages": pages_metrics,
    }
    return {"files": files, "metrics": metrics}


def _render_pillar_pages_here(rel, moc_title, pname, why, pg, page_idx, total,
                              root_rel, ctx, domain_to_moc, order, stem):
    L = labels.moc()
    text = [BC_FRONTMATTER + "# %s · %s" % (moc_title, pname), ""]
    if total > 1:
        text.append(L["pillar_page"] % (page_idx, total))
        text.append("")
    if why and page_idx == 1:
        text.append(why)
        text.append("")
    for i, (topic, cards) in enumerate(pg):
        continued = any(t == topic for t, _ in pg[:i])
        text.append("## %s%s" % (topic, L["continued"] if continued else ""))
        text.append("")
        for card in cards:
            text.append(_card_line(card, ctx, moc_title, domain_to_moc))
        text.append("")
    dirpart, fname = rel.rsplit("/", 1)
    prev = next_page = None
    if page_idx > 1:
        prev = "%s/%s" % (dirpart, _pillar_filename(order, _pillar_stem(pname), page_idx - 1))
    if page_idx < total:
        next_page = "%s/%s" % (dirpart, _pillar_filename(order, _pillar_stem(pname), page_idx + 1))
    text.append("---")
    text.append(L["navigation"])
    text.append("")
    cols = [L["no_previous"] if prev is None else _link(prev, L["previous"]),
            L["no_next"] if next_page is None else _link(next_page, L["next"]),
            _link(root_rel, L["back_to_root"])]
    text.append(" · ".join(cols))
    text.append("")
    return "\n".join(text) + "\n"


@dataclass
class _PageLink:
    label: str
    relpath: str
    order: object = None
    name: object = None
    count: int = 0
    max_topic: int = 0
    lines: int = 0


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def build_mocs(snapshot, *, affected_domains=None):
    """Deterministically project in-scope MOCs into a byte change set.

    Never writes to disk, never calls a model, never edits taxonomy. Returns a
    :class:`Projection` (files + metrics + issues).
    """
    catalog = snapshot.catalog if isinstance(snapshot.catalog, dict) else {}
    cat_domains = catalog.get("domains", {}) if isinstance(catalog, dict) else {}
    domain_to_moc = {}
    no_moc = []
    for slug, meta in (cat_domains.items() if isinstance(cat_domains, dict) else []):
        moc = meta.get("moc") if isinstance(meta, dict) else None
        if isinstance(moc, str) and moc:
            domain_to_moc[slug] = moc
        else:
            no_moc.append(slug)
    moc_to_domains = {}
    for slug, moc in domain_to_moc.items():
        moc_to_domains.setdefault(moc, []).append(slug)
    for d in moc_to_domains.values():
        d.sort()

    issues = []
    if no_moc:
        issues.append({"code": "domain_without_moc",
                       "domains": sorted(no_moc),
                       "detail": "These catalog domains have no usable moc"})

    if affected_domains is None:
        scoped = set(moc_to_domains)
    else:
        scoped = set()
        for dom in affected_domains:
            moc = domain_to_moc.get(dom)
            if moc is None:
                issues.append({"code": "domain_not_in_catalog", "domain": dom,
                               "detail": "domain is not in catalog.domains"})
                continue
            scoped.add(moc)

    files = {}
    metrics_mocs = {}
    covered = set()
    total_pages = 0
    for moc in sorted(scoped):
        res = _process_moc(snapshot, moc, moc_to_domains[moc], domain_to_moc, issues)
        files.update(res["files"])
        metrics_mocs[moc] = res["metrics"]
        covered.update(res["metrics"]["card_ids"])
        total_pages += res["metrics"]["page_count"]

    # Learning-order cycles over the whole vault, the same groups `sb status`, `sb daily` and
    # `sb review queue` count; listed when a card of the group is on a map built here.
    moc_of = {cid: domain_to_moc[c.domain] for cid, c in snapshot.cards.items() if c.domain in domain_to_moc}
    issues.extend(i for i in cycle_issues(list(snapshot.cards.values()), moc_of)
                  if covered.intersection(i["card_ids"]))

    # Unknown relation targets among cards that landed in a scoped MOC.
    known = set(snapshot.cards)
    for cid in covered:
        for rel in _relations(snapshot.cards[cid]):
            tgt = rel.get("target")
            if isinstance(tgt, str) and tgt and tgt not in known:
                issues.append({"code": "unknown_relation_target", "card": cid,
                               "target": tgt, "type": rel.get("type"),
                               "detail": "Relation target is not a card id in this vault"})

    # Cards outside all catalog domains cannot reach any domain entry.
    unknown_domain = sorted(cid for cid, c in snapshot.cards.items()
                            if c.domain not in domain_to_moc)
    if unknown_domain:
        issues.append({"code": "card_without_domain_mapping",
                       "card_ids": unknown_domain,
                       "detail": "These cards have a domain outside the catalog; no map reaches them"})

    # Deterministic dedup.
    seen = set()
    deduped = []
    for it in issues:
        key = (it.get("code"),
               str(it.get("card") or it.get("moc") or it.get("domain") or ""),
               str(tuple(it.get("card_ids") or ())))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    issues = deduped

    mocs_out = {}
    for moc in sorted(scoped):
        m = metrics_mocs[moc]
        mocs_out[moc] = {
            "domains": m["domains"],
            "page_count": m["page_count"],
            "root": m["root"],
            "card_count": m["card_count"],
            "classified": m["classified"],
            "unclassified": m["unclassified"],
            "invalid": m["invalid"],
            "pages": m["pages"],
        }

    metrics = {
        "mocs": mocs_out,
        "total_pages": total_pages,
        "covered_count": len(covered),
        "card_count": len(snapshot.cards),
        "covered": sorted(covered),
        "unknown_domain_cards": unknown_domain,
    }
    return Projection(files=files, metrics=metrics, issues=issues)

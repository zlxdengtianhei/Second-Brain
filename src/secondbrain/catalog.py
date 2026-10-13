"""Catalog metadata for the SecondBrain vault.

Validates and exposes the curation catalog: tracks (learning tracks over a set
of domains), the domain→MOC map with a guiding question, relation-type
vocabulary, inverse-relation closure, and per-{domain} relation lists. The
*data* for these lives in vault metadata; this module only checks structural
legality and builds lookup helpers. It never decides semantics.

Only Python standard library + PyYAML (transitively via vault).
"""
from __future__ import annotations

SCHEMA = "secondbrain-catalog/1"
LANGUAGES = ("en", "zh")

_FORBIDDEN = ("", ".", "..")


class CatalogError(ValueError):
    """Structural catalog errors; never a semantic judgement."""


def _is_safe_name(s) -> bool:
    """A safe single path element: no separators, no traversal, no controls.

    Spaces, CJK and punctuation are legal (real MOC titles contain spaces and
    Chinese); we only reject things that would escape the vault tree.
    """
    if not isinstance(s, str) or not s:
        return False
    if "/" in s or "\\" in s:
        return False
    if s in _FORBIDDEN:
        return False
    if any(ord(c) < 0x20 for c in s):
        return False
    return True


def _require(cond, msg):
    if not cond:
        raise CatalogError(msg)


def validate_catalog(data) -> dict:
    """Return ``data`` if it is structurally valid, else raise CatalogError.

    Checks (structural only, no semantic judgement):

    * ``schema`` is ``secondbrain-catalog/1``.
    * ``domains`` maps a safe slug to ``{moc, question}``; both are non-empty
      strings and the MOC value is a safe (single) file name.
    * ``tracks`` is a list of ``{id, title, domains:[str]}`` with no duplicate
      domain inside a track, each slug a known safe domain slug, and every
      domain slug covered by at least one track.
    * ``domain_relations`` keys are known domain slugs and values are lists of
      strings.
    * ``inverse_relations`` is a self-closing (involutive) mapping over the
      *union* of ``relation_types`` and every ``domain_relations`` list. A
      relation may legitimately be domain-specific (appear only in one domain's
      ``domain_relations``), so the reference set is the union, not
      ``relation_types`` alone. Closure here means: if ``A -> B`` then
      ``B -> A``; symmetric relations may simply be absent from the mapping.

    Unknown top-level fields (e.g. ``relation_gloss``) are not our concern and
    are preserved untouched: the returned value is ``data`` itself.
    """
    _require(isinstance(data, dict), "catalog must be a mapping")
    _require(data.get("schema") == SCHEMA,
             "schema must be %r, got %r" % (SCHEMA, data.get("schema")))

    domains = data.get("domains", {})
    _require(isinstance(domains, dict), "domains must be a mapping")
    for slug, meta in domains.items():
        _require(_is_safe_name(slug), "unsafe domain slug %r" % (slug,))
        _require(isinstance(meta, dict), "domain %r value must be a mapping" % (slug,))
        moc = meta.get("moc")
        question = meta.get("question")
        _require(isinstance(moc, str) and moc, "domain %r moc missing" % (slug,))
        _require(isinstance(question, str) and question,
                 "domain %r question missing" % (slug,))
        _require(_is_safe_name(moc), "unsafe MOC filename %r" % (moc,))

    tracks = data.get("tracks", [])
    _require(isinstance(tracks, list), "tracks must be a list")
    covered = set()
    for track in tracks:
        _require(isinstance(track, dict), "each track must be a mapping")
        tid = track.get("id")
        ttitle = track.get("title")
        _require(isinstance(tid, str) and tid, "track id must be a non-empty string")
        _require(isinstance(ttitle, str) and ttitle,
                 "track title must be a non-empty string")
        tdomains = track.get("domains")
        _require(isinstance(tdomains, list), "track %r domains must be a list" % (tid,))
        seen = set()
        for d in tdomains:
            _require(isinstance(d, str), "track %r has non-string domain" % (tid,))
            _require(d in domains, "track %r references unknown domain %r" % (tid, d))
            _require(d not in seen, "track %r duplicates domain %r" % (tid, d))
            seen.add(d)
        covered.update(seen)
    # every domain slug must be covered by at least one track
    missing_cover = [s for s in domains if s not in covered]
    _require(not missing_cover,
             "domains not covered by any track: %r" % (sorted(missing_cover),))

    domain_relations = data.get("domain_relations", {})
    _require(isinstance(domain_relations, dict), "domain_relations must be a mapping")
    domain_rel_words = set()
    for slug, rels in domain_relations.items():
        _require(slug in domains, "domain_relations has unknown domain %r" % (slug,))
        _require(isinstance(rels, list),
                 "domain_relations[%r] must be a list" % (slug,))
        for r in rels:
            _require(isinstance(r, str),
                     "domain_relations[%r] has non-string entry" % (slug,))
            domain_rel_words.add(r)

    relation_types = data.get("relation_types", [])
    _require(isinstance(relation_types, list), "relation_types must be a list")
    for rt in relation_types:
        _require(isinstance(rt, str), "relation_types has a non-string entry")
    known_relations = set(relation_types) | domain_rel_words

    inverse_relations = data.get("inverse_relations", {})
    _require(isinstance(inverse_relations, dict), "inverse_relations must be a mapping")
    for rel, inv in inverse_relations.items():
        _require(isinstance(rel, str) and isinstance(inv, str),
                 "inverse_relations must be string->string")
        _require(rel in known_relations,
                 "inverse_relations key %r is not a known relation" % (rel,))
        _require(inv in known_relations,
                 "inverse_relations value %r is not a known relation" % (inv,))
        _require(inverse_relations.get(inv) == rel,
                 "inverse_relations does not close: %r -> %r -> %r" % (rel, inv, inverse_relations.get(inv)))

    # The language of the vault's generated text (written by `sb init` and `sb language --set`).
    _require(data.get("language", "en") in LANGUAGES,
             "language must be one of %s, got %r" % ("/".join(LANGUAGES), data.get("language")))
    return data


def category_pairs(snapshot, domain) -> set:
    """Legal pairs from curated taxonomy, including categories not yet occupied."""
    meta = snapshot.catalog.get('domains', {}).get(domain, {})
    taxonomy = snapshot.taxonomies.get(meta.get('moc')) or {}
    return {(pillar['name'], topic) for pillar in taxonomy.get('pillars', [])
            for topic in pillar.get('topics', [])}


def taxonomy_packet(snapshot, domains: list) -> dict:
    """Full taxonomy context for the given domains (nothing truncated).

    Resolves each domain slug to its MOC via ``snapshot.catalog``, then returns
    the complete, untruncated taxonomy dict (title, narrative, pillars with
    ``why``/``topics``, bridges) per distinct MOC. A domain with no catalog
    entry, and a MOC with no loaded taxonomy, are reported explicitly as
    ``None`` so callers never mistake absence for silently missing data.
    """
    _require(isinstance(domains, (list, tuple)), "domains must be a list")
    domain_mocs = {}
    mocs = []
    for slug in domains:
        _require(isinstance(slug, str), "domain slug must be a string")
        meta = None
        if isinstance(snapshot.catalog, dict):
            dm = snapshot.catalog.get("domains")
            if isinstance(dm, dict):
                meta = dm.get(slug)
        moc = meta.get("moc") if isinstance(meta, dict) else None
        if isinstance(moc, str) and moc not in mocs:
            mocs.append(moc)
        domain_mocs[slug] = moc
    taxonomies = {}
    for moc in mocs:
        taxonomies[moc] = snapshot.taxonomies.get(moc)
    return {
        "domains": list(domains),
        "domain_mocs": domain_mocs,
        "taxonomies": taxonomies,
    }


def relation_types(catalog: dict) -> list:
    """Every relation type a card may use: ``relation_types`` plus the words of ``domain_relations``."""
    rels = list(catalog.get('relation_types') or [])
    for words in (catalog.get('domain_relations') or {}).values():
        rels += [w for w in words if isinstance(w, str) and w not in rels]
    return rels


def relation_meanings(catalog: dict, types=None, language: str | None = None) -> dict:
    """``{type: meaning}`` in the form "A <type> B ...", where A holds the relation and B is its target.

    One source of truth: a type's own ``relation_gloss``, else the meaning of
    its inverse from ``inverse_relations`` ("A used_by B means B tool_for A"),
    so a type and its inverse cannot be explained in opposite directions.
    """
    from . import labels
    words = labels.pages(language or labels.recorded_language(catalog))
    glosses = catalog.get('relation_gloss') or {}
    inverse = catalog.get('inverse_relations') or {}

    def gloss(name):
        value = glosses.get(name)
        return value.strip() if isinstance(value, str) and value.strip() else None

    out = {}
    for name in relation_types(catalog) if types is None else types:
        other, own = inverse.get(name), gloss(name)
        if other == name:
            key = 'relation_symmetric_gloss' if own else 'relation_symmetric'
        elif own:
            key = 'relation_forward'
        elif other:
            key = 'relation_inverse_gloss' if gloss(other) else 'relation_inverse'
            own = gloss(other)
        else:
            key = 'relation_plain'
        out[name] = words[key].format(type=name, other=other, gloss=own)
    return out

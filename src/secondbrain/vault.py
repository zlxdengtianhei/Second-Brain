"""Deterministic, read-only snapshot of the SecondBrain vault.

Owned by the "vault-impl" work slice (see INTERFACES.md). Reads the vault tree
without writing: cards under ``10_Knowledge``, the registry
``_data/notes_registry.json``, the optional catalog
``_data/secondbrain/catalog.json``, and the per-MOC pillar taxonomies under
``20_MOCs/_pillars/<MOC title>.yaml``. No models, no legacy imports, no writes.

Only Python standard library + PyYAML.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import yaml

# Relative paths inside the vault. ``/`` separators are used so they double as
# the identity keys stored in registry notes and in Card.path.
REGISTRY_RELPATH = "_data/notes_registry.json"
CATALOG_RELPATH = "_data/secondbrain/catalog.json"
KNOWLEDGE_RELPATH = "10_Knowledge"
PILLARS_RELPATH = "20_MOCs/_pillars"


class VaultError(ValueError):
    """Errors produced by this module (structural, never semantic)."""


# --------------------------------------------------------------------------
# Safe loader that rejects duplicate mapping keys.
#
# PyYAML 6.x default safe_load silently keeps the *last* duplicate key. The
# interface contract requires duplicate keys to be rejected, so add a loader
# that raises on them while otherwise behaving exactly like SafeLoader (so
# multi-line block scalars, flow values, CRLF and non-ASCII keys still load).
# --------------------------------------------------------------------------
class _NoDupLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None, None, "duplicate mapping key %r" % (key,), node.start_mark
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDupLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _safe_load_unique(text):
    """yaml.load with SafeLoader semantics but duplicate key rejection."""
    return yaml.load(text, Loader=_NoDupLoader)


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Card:
    id: str
    title: str
    domain: str
    path: str  # relative to vault root, OS-independent "/" separators
    raw: bytes
    fm: dict
    body: str
    sha256: str


@dataclass(frozen=True)
class Snapshot:
    root: object  # pathlib.Path; kept untyped for Python-version neutrality
    cards: dict
    registry: dict
    catalog: dict
    taxonomies: dict  # keyed by MOC title
    issues: list  # list[dict]
    fingerprint: str
    read_hashes: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# Frontmatter splitting / patching
# --------------------------------------------------------------------------
def _locate_frontmatter(raw: bytes):
    """Return ``(fm_content_start, closer_pos, body_start)`` byte offsets.

    ``fm_content_start`` is just after the opening ``---`` line; ``closer_pos``
    is the start of the closing ``---`` line; ``body_start`` is just after that
    closing line (including its newline). ``raw`` is a full markdown file.
    Physical-line scan only; body bytes after the closing delimiter are
    untouched so trailing newlines and CRLF survive.
    """
    nl = raw.find(b"\n")
    first = raw[:nl] if nl != -1 else raw
    if first.strip() != b"---":
        raise VaultError("frontmatter opener missing")
    cur = nl + 1 if nl != -1 else len(raw)
    fm_content_start = cur
    while True:
        e = raw.find(b"\n", cur)
        line = raw[cur:e] if e != -1 else raw[cur:]
        if line.strip() == b"---":
            closer_pos = cur
            body_start = (e + 1) if e != -1 else len(raw)
            return fm_content_start, closer_pos, body_start
        if e == -1:
            raise VaultError("frontmatter closer missing")
        cur = e + 1


def _top_level_ranges(fm_bytes: bytes):
    """Yield ``(start, end, content, is_top)`` for each physical line.

    ``is_top`` is True for column-0 lines (first byte not space/tab). Column-0
    lines delimit top-level key value blocks; comment and blank column-0 lines
    are *separators* that are never absorbed by a key's replaceable block, so
    they are preserved verbatim.
    """
    pos = 0
    n = len(fm_bytes)
    while pos <= n:
        e = fm_bytes.find(b"\n", pos)
        if e == -1:
            content = fm_bytes[pos:n]
            end = n
        else:
            content = fm_bytes[pos:e]
            end = e + 1
        # YAML block sequence items (`- `) are the preceding key's value, not
        # top-level delimiters; treating them as delimiters truncates block-style
        # list values (tags/aliases/sources) and duplicates them on patch.
        is_top = (bool(content) and content[:1] not in (b" ", b"\t")
                  and not content.startswith(b"- "))
        yield pos, end, content, is_top
        if e == -1:
            break
        pos = end


_PREFIX_RE = re.compile(r"^([A-Za-z0-9_.\-\u0080-\uffff]+\s*):")


def _key_of_top_line(content: bytes) -> Optional[str]:
    """Return the top-level key name this column-0 line opens, if any."""
    if not content or content[:1] in (b" ", b"\t"):
        return None
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text.startswith("#"):
        return None  # comment
    m = _PREFIX_RE.match(text)
    if m is None:
        return None
    return m.group(1).strip()


def _dump_key(key: str, value, nl: bytes) -> bytes:
    """Serialize a single top-level ``key: value`` block to bytes.

    ``value is None`` produces YAML ``null`` (per interface: deletion is not
    supported). Line endings follow the dominant newline of the original file.
    """
    text = yaml.safe_dump(
        {key: value}, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    data = text.encode("utf-8")
    if nl != b"\n":
        data = data.replace(b"\n", nl)
    return data


def parse_note(raw: bytes):
    """Split ``raw`` markdown bytes into ``(fm: dict, body: str)``.

    Raises :class:`VaultError` on missing delimiters, duplicate keys, YAML
    errors, or a frontmatter whose top level is not a mapping. The returned
    ``body`` is the full text after the frontmatter (including any spaced
    repetition section), decoded as UTF-8.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise VaultError("raw must be bytes")
    raw = bytes(raw)
    fm_content_start, closer_pos, body_start = _locate_frontmatter(raw)
    fm_bytes = raw[fm_content_start:closer_pos]
    body_bytes = raw[body_start:]
    try:
        data = _safe_load_unique(fm_bytes)
    except yaml.YAMLError as exc:
        raise VaultError("frontmatter YAML error: %s" % (exc,)) from exc
    if not isinstance(data, dict):
        raise VaultError("frontmatter top level is not a mapping")
    try:
        body = body_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VaultError("body is not valid UTF-8: %s" % (exc,)) from exc
    return data, body


def patch_frontmatter(raw: bytes, updates: dict) -> bytes:
    """Return ``raw`` with only the top-level ``updates`` keys changed.

    Only the value blocks of the listed top-level keys are replaced; every
    other byte of the file (other frontmatter fields, comments, blank lines,
    newlines, the body, and any spaced-repetition section) is preserved
    verbatim. Keys not already present are appended at the end of the
    frontmatter. ``updates[key] is None`` writes YAML ``null``; *deleting* a
    key is not supported and raises.
    """
    if not isinstance(updates, dict) or not updates:
        raise VaultError("updates must be a non-empty dict")
    updates = {k: v for k, v in updates.items()}
    if any(not isinstance(k, str) or not k for k in updates):
        raise VaultError("frontmatter update keys must be non-empty strings")
    fm_content_start, closer_pos, _body_start = _locate_frontmatter(raw)
    fm_bytes = raw[fm_content_start:closer_pos]
    nl = b"\r\n" if b"\r\n" in raw else b"\n"

    lines = list(_top_level_ranges(fm_bytes))

    # Locate the replaceable block for every target key.
    blocks = {}  # key -> (start, end)
    for start, end, content, is_top in lines:
        if not is_top:
            continue
        key = _key_of_top_line(content)
        if key in updates:
            if key in blocks:
                raise VaultError("duplicate top-level key %r" % (key,))
            blocks[key] = (start, end)  # end is provisional (the key line's end)
    # Finalize each block end: the start of the next column-0 line after it.
    for key, (start, _end) in blocks.items():
        end = len(fm_bytes)
        for lstart, _lend, _content, is_top2 in lines:
            if is_top2 and lstart > start and lstart < end:
                end = lstart
        blocks[key] = (start, end)

    # Splice in document order.
    out = bytearray()
    idx = 0
    remaining = dict(updates)
    while idx < len(lines):
        start, end, content, is_top = lines[idx]
        matched = None
        for key, (bst, ben) in blocks.items():
            if bst == start:
                matched = (key, ben)
                break
        if matched is not None:
            key, ben = matched
            out += _dump_key(key, updates[key], nl)
            remaining.pop(key, None)
            while idx < len(lines) and lines[idx][0] < ben:
                idx += 1
            continue
        out += fm_bytes[start:end]
        idx += 1

    new_fm = bytes(out)
    # Append any keys that were not present.
    if remaining:
        if not new_fm.endswith(b"\n"):
            new_fm += nl
        for key in sorted(remaining):
            new_fm += _dump_key(key, updates[key], nl)

    return raw[:fm_content_start] + new_fm + raw[closer_pos:]


# --------------------------------------------------------------------------
# Read helpers
# --------------------------------------------------------------------------
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_relpath(rel: str) -> Optional[str]:
    """Validate a registry-declared relative path.

    Returns the OS-independent "/" relative form, or None if unsafe. On the
    POSIX vault a backslash is a legitimate filename character (e.g. LaTeX
    ``\\varepsilon``), so only ``/`` separators and empty/dot/``..`` segments
    are treated as out-of-bounds here.
    """
    if not isinstance(rel, str) or not rel:
        return None
    if rel.startswith("/"):
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    return "/".join(parts)


def _path_is_safe(rel: str) -> bool:
    if not isinstance(rel, str) or not rel or rel.startswith("/"):
        return False
    if any(p in ("", ".", "..") for p in rel.split("/")):
        return False
    return True


def _walk_md(root: str, issues: list):
    """Yield ``(relpath, bytes)`` for every non-symlink ``.md`` under 10_Knowledge.

    Symbolic links and unsafe paths are recorded as issues and skipped. The
    directory walk does not follow directory symlinks, and every yielded file
    is a regular file.
    """
    base = os.path.join(str(root), KNOWLEDGE_RELPATH)
    if not os.path.isdir(base):
        issues.append({"code": "knowledge_dir_missing", "path": KNOWLEDGE_RELPATH,
                       "detail": "10_Knowledge does not exist"})
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        filenames.sort()
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                rel = os.path.relpath(full, str(root)).replace(os.sep, "/")
                issues.append({"code": "unsafe_symlink", "path": rel,
                               "detail": "symbolic links are not read"})
                continue
            if not name.endswith(".md"):
                continue
            rel = os.path.relpath(full, str(root)).replace(os.sep, "/")
            if not _path_is_safe(rel):
                issues.append({"code": "unsafe_path", "path": rel,
                               "detail": "path escapes knowledge root"})
                continue
            try:
                raw = open(full, "rb").read()
            except OSError as exc:
                issues.append({"code": "read_error", "path": rel,
                               "detail": str(exc)})
                continue
            yield rel, raw


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------
def _load_registry(root, issues):
    full = os.path.join(str(root), *REGISTRY_RELPATH.split("/"))
    if not os.path.exists(full):
        issues.append({"code": "registry_missing", "path": REGISTRY_RELPATH,
                       "detail": "notes_registry.json not found"})
        return {}, b""
    if os.path.islink(full):
        issues.append({"code": "unsafe_symlink", "path": REGISTRY_RELPATH,
                       "detail": "registry file is a symlink"})
        return {}, b""
    try:
        raw = open(full, "rb").read()
    except OSError as exc:
        issues.append({"code": "read_error", "path": REGISTRY_RELPATH,
                       "detail": str(exc)})
        return {}, b""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        issues.append({"code": "registry_unreadable", "path": REGISTRY_RELPATH,
                       "detail": "registry JSON parse failed: %s" % exc})
        return {}, raw
    if not isinstance(data, dict):
        issues.append({"code": "registry_unreadable", "path": REGISTRY_RELPATH,
                       "detail": "registry top level is not a mapping"})
        return data, raw
    notes = data.get("notes")
    if notes is not None and not isinstance(notes, list):
        issues.append({"code": "registry_unreadable", "path": REGISTRY_RELPATH,
                       "detail": "registry 'notes' is not a list"})
    return data, raw


def _load_catalog(root, issues, explicit, observed=None):
    if explicit is not None:
        return explicit
    full = os.path.join(str(root), *CATALOG_RELPATH.split("/"))
    if not os.path.exists(full):
        issues.append({"code": "catalog_missing", "path": CATALOG_RELPATH,
                       "detail": "default catalog file not found; snapshot carries an empty catalog"})
        return {}
    if os.path.islink(full):
        issues.append({"code": "catalog_unreadable", "path": CATALOG_RELPATH,
                       "detail": "catalog file is a symlink"})
        return {}
    try:
        raw = open(full, "rb").read()
        if observed is not None:
            observed[CATALOG_RELPATH] = _sha256(raw)
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        issues.append({"code": "catalog_unreadable", "path": CATALOG_RELPATH,
                       "detail": "catalog could not be read: %s" % exc})
        return {}
    if not isinstance(data, dict):
        issues.append({"code": "catalog_unreadable", "path": CATALOG_RELPATH,
                       "detail": "catalog top level is not a mapping"})
        return {}
    return data


def _load_taxonomies(root, issues):
    taxonomies = {}
    raw_hashes = {}
    base = os.path.join(str(root), *PILLARS_RELPATH.split("/"))
    if not os.path.isdir(base):
        issues.append({"code": "pillars_dir_missing", "path": PILLARS_RELPATH,
                       "detail": "20_MOCs/_pillars does not exist"})
        return taxonomies, raw_hashes
    for name in sorted(os.listdir(base)):
        full = os.path.join(base, name)
        if not name.endswith(".yaml"):
            continue
        # reject directory entries that are themselves directories
        if not os.path.isfile(full) or os.path.islink(full):
            issues.append({"code": "unsafe_taxonomy_entry", "path": PILLARS_RELPATH + "/" + name,
                           "detail": "taxonomy entry is not a regular file"})
            continue
        try:
            raw = open(full, "rb").read()
        except OSError as exc:
            issues.append({"code": "read_error", "path": PILLARS_RELPATH + "/" + name,
                           "detail": str(exc)})
            continue
        try:
            parsed = _safe_load_unique(raw)
        except yaml.YAMLError as exc:
            issues.append({"code": "taxonomy_unreadable", "path": PILLARS_RELPATH + "/" + name,
                           "detail": "taxonomy YAML failed: %s" % exc})
            continue
        if not isinstance(parsed, dict):
            issues.append({"code": "taxonomy_unreadable", "path": PILLARS_RELPATH + "/" + name,
                           "detail": "taxonomy top level is not a mapping"})
            continue
        title = parsed.get("title") or name[:-5]
        if not isinstance(title, str):
            title = name[:-5]
        taxonomies[title] = parsed
        raw_hashes[PILLARS_RELPATH + '/' + name] = _sha256(raw)
    return taxonomies, raw_hashes


def read_snapshot(root, *, catalog=None) -> Snapshot:
    """Read a deterministic snapshot of the vault rooted at ``root``.

    ``catalog`` may be supplied explicitly (used for initialization and
    isolated tests); otherwise ``_data/secondbrain/catalog.json`` is read if
    present (a missing file is recorded as an explicit issue, not an error).
    """
    root = os.fspath(root)
    issues = []
    if not os.path.isdir(root):
        raise VaultError("vault root is not a directory: %r" % (root,))

    registry, registry_raw = _load_registry(root, issues)
    read_hashes = {REGISTRY_RELPATH: _sha256(registry_raw)} if registry_raw else {}
    catalog_data = _load_catalog(root, issues, catalog, read_hashes)
    taxonomies, taxonomy_hashes = _load_taxonomies(root, issues)

    # Index registry notes by id and by path.
    reg_by_id = {}
    reg_by_path = {}
    notes = registry.get("notes", []) if isinstance(registry, dict) else []
    if not isinstance(notes, list):
        notes = []
    for i, note in enumerate(notes):
        if not isinstance(note, dict):
            issues.append({"code": "registry_bad_note", "path": REGISTRY_RELPATH,
                           "detail": "registry note %d is not a mapping" % i})
            continue
        npath = _normalize_relpath(str(note.get("path", "")).replace(os.sep, "/"))
        nid = note.get("id")
        if npath is not None:
            if npath in reg_by_path:
                issues.append({"code": "duplicate_registry_path", "path": npath,
                               "detail": "registry has duplicate path %r" % (npath,)})
            reg_by_path[npath] = note
        if isinstance(nid, str) and nid:
            if nid in reg_by_id:
                issues.append({"code": "duplicate_registry_id", "path": npath or REGISTRY_RELPATH,
                               "detail": "registry has duplicate id %r" % (nid,)})
            else:
                reg_by_id[nid] = note

    from pathlib import Path as _Path
    root_path = _Path(root)
    cards = {}
    card_sha_by_rel = {}
    for rel, raw in _walk_md(root, issues):
        try:
            fm, body = parse_note(raw)
        except VaultError as exc:
            issues.append({"code": "parse_failure", "path": rel,
                           "detail": str(exc)})
            continue
        card_id = fm.get("id")
        if not isinstance(card_id, str) or not card_id:
            issues.append({"code": "missing_id", "path": rel,
                           "detail": "card frontmatter has no usable id"})
            continue
        title = fm.get("title")
        if title is None:
            title = ""
        elif not isinstance(title, str):
            title = str(title)
        domain = fm.get("domain")
        if domain is None:
            domain = ""
        elif not isinstance(domain, str):
            domain = str(domain)
        sha = _sha256(raw)
        card_sha_by_rel[rel] = sha

        if rel in cards:
            issues.append({"code": "duplicate_path", "path": rel,
                           "detail": "two cards claim the same file path"})
            continue
        if card_id in cards:
            issues.append({"code": "duplicate_id", "path": rel,
                           "detail": "card id %r already seen" % (card_id,)})
            continue
        card = Card(id=card_id, title=title, domain=domain, path=rel,
                    raw=raw, fm=fm, body=body, sha256=sha)
        cards[card_id] = card

        reg_note = reg_by_id.get(card_id)
        if reg_note is None:
            issues.append({"code": "unregistered", "path": rel,
                           "detail": "card id %r is not in the registry" % (card_id,)})
        else:
            for field in ("title", "domain", "path"):
                reg_val = reg_note.get(field)
                if isinstance(reg_val, str) and reg_val != getattr(card, field):
                    issues.append({"code": "identity_mismatch", "path": rel,
                                   "detail": "card %r differs from registry on %s" % (field, card_id)})

    # Registry notes whose declared file was not found or not parsed.
    for npath in reg_by_path:
        if not npath.startswith(KNOWLEDGE_RELPATH + "/"):
            continue
        exists = os.path.exists(root_path.joinpath(*npath.split("/")))
        if not exists:
            issues.append({"code": "dropped_card", "path": npath,
                           "detail": "registered card file is missing"})
        elif npath not in card_sha_by_rel:
            issues.append({"code": "unparsed_registered", "path": npath,
                           "detail": "registered file exists but was not parsed into a card"})

    # Taxonomy coverage for catalog domains.
    domains_map = catalog_data.get("domains", {}) if isinstance(catalog_data, dict) else {}
    if isinstance(domains_map, dict):
        for slug, meta in domains_map.items():
            if isinstance(meta, dict):
                moc = meta.get("moc")
                if isinstance(moc, str) and moc not in taxonomies:
                    issues.append({"code": "missing_taxonomy", "path": PILLARS_RELPATH,
                                   "detail": "no taxonomy for catalog domain %r (MOC %r)" % (slug, moc)})

    fingerprint = _compute_fingerprint(cards, registry_raw, catalog_data, taxonomies)
    read_hashes.update(taxonomy_hashes)
    read_hashes.update(card_sha_by_rel)
    for meta in domains_map.values():
        if isinstance(meta, dict) and isinstance(meta.get('moc'), str):
            read_hashes.setdefault(PILLARS_RELPATH + '/' + meta['moc'] + '.yaml', None)
    return Snapshot(root=root_path, cards=cards, registry=registry,
                    catalog=catalog_data, taxonomies=taxonomies,
                    issues=issues, fingerprint=fingerprint, read_hashes=read_hashes)


def _compute_fingerprint(cards, registry_raw, catalog, taxonomies):
    h = hashlib.sha256()
    h.update(b"secondbrain-vault-fp/1\n")
    for cid in sorted(cards):
        h.update(cards[cid].sha256.encode("ascii"))
        h.update(b"\n")
    h.update(_sha256(registry_raw).encode("ascii"))
    h.update(b"\n")
    try:
        cat_ser = json.dumps(catalog, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"))
        h.update(cat_ser.encode("utf-8"))
    except (TypeError, ValueError):
        h.update(b"__unserializable_catalog__")
    h.update(b"\n")
    for t in sorted(taxonomies):
        tser = json.dumps(taxonomies[t], sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))
        h.update(t.encode("utf-8"))
        h.update(b"\x00")
        h.update(tser.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def card_packet(card: Card) -> dict:
    """The task-packet shape for one card (full body, never truncated)."""
    fm = card.fm
    return {
        "id": card.id,
        "path": card.path,
        "title": card.title,
        "domain": card.domain,
        "sha256": card.sha256,
        "pillar": fm.get("pillar"),
        "topic": fm.get("topic"),
        "body": card.body,
        "relations": fm.get("relations"),
    }

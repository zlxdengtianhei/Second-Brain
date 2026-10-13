"""Recoverable, compare-before-write transactions for the vault's owned files.

Two write surfaces are kept strictly apart:

* **vault knowledge** (cards, MOCs, sources, registry, catalog, projections) is
  read and written only under the real vault root through no-follow directory
  handles (:func:`read_bytes` / :func:`_atomic`).
* **processing state** (job input, candidates, reviews, run records, commit
  intent, the transaction journal and the locks) lives under the vault's
  *external state root*, ``config.state_dir()/vaults/<sha256 of the canonical
  vault path>/v3`` (:func:`state_root` / :func:`read_state` / :func:`write_json`).

The journal records bytes, not semantic approval. Callers must validate proposals
and their independent reviews before passing a change set to this module.
"""
from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

from . import config
from . import layout as layouts


class Conflict(ValueError):
    """An input changed or another writer owns the vault lock."""


@dataclass(frozen=True)
class Change:
    relative_path: str
    before_sha256: str | None
    after: bytes | None


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def job_name(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}", value):
        raise ValueError("任务ID只能用字母、数字、下划线、短横线或点，长度1–120")
    return value


def relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or str(path) != value or any(x in {".", ".."} for x in path.parts):
        raise ValueError(f"不是规范的库内相对路径：{value!r}")
    # POSIX vault titles contain literal LaTeX backslashes (e.g. \varepsilon).
    # Directory walking below splits only on '/', never interprets '\\'.
    if "\x00" in value:
        raise ValueError("路径含空字节")
    if any(part == '.git' for part in path.parts):
        raise ValueError("不能读写版本库内部路径")
    return path


def _allowed(root: Path, value: str) -> bool:
    """Whether a transaction may write ``value`` in this vault's layout."""
    relative(value)
    lay = layouts.of(root)
    return (lay.is_card(value) or lay.is_moc_page(value) or lay.is_taxonomy_file(value)
            or lay.is_source(value) or lay.is_inbox_file(value) or lay.is_review_page(value)
            or lay.is_projection_json(value)
            or value in {lay.registry, lay.manifest, lay.ingested, lay.catalog,
                         lay.inbox_registry, lay.review_events, *lay.standalone_files})


def _root(root: Path) -> Path:
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir() or root.resolve() != root:
        raise ValueError("vault必须是存在的真实目录，不能含符号链接")
    return root


# --------------------------------------------------------------------------
# External state root (job state, journal, write lock).
# --------------------------------------------------------------------------
#: State layout version. Older tool versions kept their jobs one level up; they
#: are never read or migrated by this version.
STATE_VERSION = "v3"


def state_root(root: Path) -> Path:
    """The vault's external processing-state root: ``state_dir()/vaults/<sha256>/v3``.

    Pure path derivation — reading through it never creates directories. The key
    is the SHA-256 of the canonical (fully resolved) vault absolute path, so
    every caller of the same vault agrees on one state root.
    """
    root = _root(root)
    base = config.state_dir()
    config.validate_state_dir(root, base)
    canonical = str(Path(root).resolve())
    key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return base / "vaults" / key / STATE_VERSION


def _chain_fd(parts: list[str], *, create: bool) -> int:
    """Walk absolute ``parts`` with no-follow directory handles.

    Every component is opened with ``O_NOFOLLOW`` — a symlink anywhere in the
    chain (including the state root itself) raises instead of redirecting the
    write/read surface. Missing components are created as real directories when
    ``create`` is set.
    """
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            if not part or part in {".", ".."}:
                raise ValueError(f"状态路径含非法分量：{part!r}")
            try:
                new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except BaseException:
        os.close(fd)
        raise


def _state_parts(root: Path, rel: str) -> tuple[list[str], tuple]:
    target = relative(rel)
    base = state_root(root)
    return [p for p in base.parts if p != "/"], target.parts


@contextlib.contextmanager
def _state_parent(root: Path, rel: str, *, create: bool = False) -> Iterator[tuple[int, str]]:
    parts, target_parts = _state_parts(root, rel)
    fd = _chain_fd(parts + list(target_parts[:-1]), create=create)
    try:
        yield fd, target_parts[-1]
    finally:
        os.close(fd)


@contextlib.contextmanager
def _state_dir(root: Path, rel: str, *, create: bool = False) -> Iterator[int]:
    parts, target_parts = _state_parts(root, rel)
    fd = _chain_fd(parts + list(target_parts), create=create)
    try:
        yield fd
    finally:
        os.close(fd)


def read_state(root: Path, rel: str) -> bytes | None:
    """Read a state file from the vault's external state root; absent is None.

    Uses the same no-follow, no-hardlink discipline as knowledge reads, and
    never creates directories.
    """
    try:
        with _state_parent(root, rel, create=False) as (fd, name):
            try:
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                return None
            with os.fdopen(child, "rb") as stream:
                st = os.fstat(stream.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                    raise ValueError(f"拒绝非常规或硬链接文件：{rel}")
                return stream.read()
    except FileNotFoundError:
        return None


def _atomic_state(root: Path, rel: str, data: bytes) -> None:
    with _state_parent(root, rel, create=True) as (fd, name):
        temporary = f".secondbrain-{uuid.uuid4().hex}.tmp"
        child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(child, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass


# --------------------------------------------------------------------------
# Vault knowledge reads / writes (real vault only).
# --------------------------------------------------------------------------
def read_bytes(root: Path, rel: str) -> bytes | None:
    """Read a regular vault knowledge file through no-follow handles; None if absent.

    Job-processing state no longer lives in the vault: paths under the old
    ``_data/secondbrain/jobs``, ``_data/secondbrain/write.lock`` and the new
    ``jobs/<id>/...`` layout must go through :func:`state_root` /
    :func:`read_state` instead.
    """
    if rel.startswith("jobs/"):
        raise ValueError(f"任务状态在库外状态根，请用 read_state 或 jobs.* 接口：{rel}")
    try:
        with _parent(root, rel) as (fd, name):
            try:
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                return None
            with os.fdopen(child, "rb") as stream:
                st = os.fstat(stream.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                    raise ValueError(f"拒绝非常规或硬链接文件：{rel}")
                return stream.read()
    except FileNotFoundError:
        return None


@contextlib.contextmanager
def _parent(root: Path, rel: str, *, create: bool = False) -> Iterator[tuple[int, str]]:
    parts = relative(rel).parts
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        yield fd, parts[-1]
    finally:
        os.close(fd)


def _atomic(root: Path, rel: str, data: bytes | None) -> None:
    if data is None:
        with _parent(root, rel) as (fd, name):
            os.unlink(name, dir_fd=fd)
            os.fsync(fd)
        return
    with _parent(root, rel, create=True) as (fd, name):
        temporary = f".secondbrain-{uuid.uuid4().hex}.tmp"
        child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(child, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass


def write_json(root: Path, rel: str, value: dict) -> None:
    """Internal state writer; routes to the vault's external state root.

    No model-visible generic file-write API uses this, and no knowledge path is
    ever routed here — knowledge writes go through :func:`_atomic` on the real
    vault.
    """
    _atomic_state(root, rel, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


_LOCAL = threading.local()


def _lock_wait() -> float:
    try:
        return max(0.0, float(os.environ.get("SECONDBRAIN_LOCK_WAIT", "30")))
    except ValueError:
        return 30.0


@contextlib.contextmanager
def vault_lock(root: Path) -> Iterator[None]:
    """Serialize all vault and job-state writes for one vault.

    Another process holding the lock is waited for (``SECONDBRAIN_LOCK_WAIT``
    seconds, default 30) and then reported as :class:`Conflict`. Re-acquiring
    it in the same thread is a programming error and fails immediately.
    """
    root = _root(root)
    key = str(root.resolve())
    held = getattr(_LOCAL, "held", None)
    if held is None:
        held = _LOCAL.held = set()
    if key in held:
        raise Conflict("另一个SecondBrain写入正在进行（同一线程重复加锁），请稍后恢复当前任务")
    with _state_parent(root, "write.lock", create=True) as (fd, name):
        lock_fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            if not stat.S_ISREG(os.fstat(lock_fd).st_mode) or os.fstat(lock_fd).st_nlink != 1:
                raise ValueError("写锁不是独立常规文件")
            deadline = time.monotonic() + _lock_wait()
            delay = 0.01
            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise Conflict("另一个SecondBrain写入正在进行，请稍后恢复当前任务") from exc
                    time.sleep(delay)
                    delay = min(delay * 2, 0.25)
            held.add(key)
            try:
                yield
            finally:
                held.discard(key)
        finally:
            os.close(lock_fd)


def _journal_path(job_id: str) -> str:
    return f"jobs/{job_name(job_id)}/transaction.json"


def _hash_current(root: Path, rel: str) -> str | None:
    data = read_bytes(root, rel)
    return None if data is None else digest(data)


def _b64(data: bytes | None) -> str | None:
    return None if data is None else base64.b64encode(data).decode("ascii")


def _unb64(value: str | None) -> bytes | None:
    return None if value is None else base64.b64decode(value, validate=True)


def _load_journal(root: Path, job_id: str) -> dict | None:
    raw = read_state(root, _journal_path(job_id))
    if raw is None:
        return None
    journal = json.loads(raw)
    if journal.get("schema") not in {"secondbrain-transaction/1", "secondbrain-transaction/2"} or journal.get("job_id") != job_id:
        raise ValueError("事务格式或任务身份不匹配")
    seen = set()
    for row in journal["changes"]:
        rel = row["path"]
        if rel in seen or not _allowed(root, rel):
            raise ValueError("事务路径重复或越界")
        seen.add(rel)
        for field in ["before", "after"]:
            content = _unb64(row[field])
            actual = None if content is None else digest(content)
            if actual != row[f"{field}_sha256"]:
                raise ValueError("事务内容哈希不匹配")
    _validate_checks(journal.get("checks", {}), {r["path"] for r in journal["changes"]})
    _validate_moves(journal["changes"], layouts.of(root))
    return journal


def _resume_locked(root: Path, journal: dict, *, crash_after: int | None = None) -> dict:
    path = _journal_path(journal["job_id"])
    if journal["status"] in {"rolled_back", "rolling_back"}:
        raise Conflict("该事务已开始回滚，请继续rollback；重新处理请使用新任务ID")
    for rel, sha in journal.get("checks", {}).items():
        if _hash_current(root, rel) != sha:
            raise Conflict(f"只读依赖在准备后变化：{rel}")
    # Validate the entire remaining set before changing a single file.
    for row in journal["changes"]:
        actual = _hash_current(root, row["path"])
        if actual not in {row["before_sha256"], row["after_sha256"]}:
            journal.update(status="conflict", conflict_path=row["path"])
            write_json(root, path, journal)
            raise Conflict(f"文件在准备后被修改：{row['path']}；未覆盖，请重新准备该任务")
    if journal["status"] == "applied":
        # A completed transaction is idempotent only while its outputs survive.
        stale = [r["path"] for r in journal["changes"] if _hash_current(root, r["path"]) != r["after_sha256"]]
        if stale:
            raise Conflict(f"已完成事务输出已改变：{stale[0]}")
        return {"status": "applied", "job_id": journal["job_id"], "paths": [r["path"] for r in journal["changes"]], "replayed": True}
    journal.update(status="applying", conflict_path=None)
    write_json(root, path, journal)
    changed = 0
    for row in journal["changes"]:
        actual = _hash_current(root, row["path"])
        if actual == row["after_sha256"]:
            continue
        if actual != row["before_sha256"]:
            journal.update(status="conflict", conflict_path=row["path"])
            write_json(root, path, journal)
            raise Conflict(f"写入时发现外部修改：{row['path']}")
        _atomic(root, row["path"], _unb64(row["after"]))
        changed += 1
        if crash_after is not None and changed == crash_after:
            raise RuntimeError("injected interruption after durable file write")
    journal["status"] = "applied"
    write_json(root, path, journal)
    return {"status": "applied", "job_id": journal["job_id"], "paths": [r["path"] for r in journal["changes"]], "replayed": False}


def apply_changes(root: Path, job_id: str, changes: list[Change], *, checks: dict[str, str | None] | None = None, _crash_after: int | None = None, operation: str | None = None) -> dict:
    root = _root(root)
    job_name(job_id)
    seen = set()
    for change in changes:
        if not _allowed(root, change.relative_path) or change.relative_path in seen:
            raise ValueError(f"写入路径越界或重复：{change.relative_path}")
        if change.after is not None and not isinstance(change.after, bytes):
            raise ValueError("写入内容必须是bytes，迁移源可为None")
        seen.add(change.relative_path)
    checks = dict(checks or {})
    _validate_checks(checks, seen)
    if not changes:
        return {"status": "unchanged", "job_id": job_id, "paths": []}
    with vault_lock(root):
        journal = _load_journal(root, job_id)
        if journal:
            supplied = [(c.relative_path, c.before_sha256, (None if c.after is None else digest(c.after))) for c in changes]
            saved = [(r["path"], r["before_sha256"], r["after_sha256"]) for r in journal["changes"]]
            if supplied != saved or checks != journal.get("checks", {}):
                raise Conflict("任务ID已绑定另一个变更集；不得覆盖其恢复记录")
            if operation is not None and journal.get('operation') != operation:
                raise Conflict('任务ID已绑定另一typed operation')
            return _resume_locked(root, journal, crash_after=_crash_after)
        rows = []
        for change in changes:
            before = read_bytes(root, change.relative_path)
            actual = None if before is None else digest(before)
            if actual != change.before_sha256:
                raise Conflict(f"准备后文件发生变化：{change.relative_path}")
            if layouts.of(root).is_source(change.relative_path) and before is not None and before != change.after:
                raise Conflict("来源原文不可覆盖")
            rows.append({"path": change.relative_path, "before_sha256": actual,
                         "after_sha256": None if change.after is None else digest(change.after), "before": _b64(before), "after": _b64(change.after)})
        _validate_moves(rows, layouts.of(root))
        version = 2 if checks or any(r["after"] is None for r in rows) else 1
        journal = {"schema": f"secondbrain-transaction/{version}", "job_id": job_id, "status": "prepared", "changes": rows, "checks": checks}
        if operation is not None:
            journal['operation'] = operation
        write_json(root, _journal_path(job_id), journal)
        return _resume_locked(root, journal, crash_after=_crash_after)


def recover(root: Path, job_id: str, action: str = "resume") -> dict:
    root = _root(root)
    if action not in {"resume", "rollback"}:
        raise ValueError("恢复动作必须是resume或rollback")
    with vault_lock(root):
        journal = _load_journal(root, job_id)
        if journal is None:
            raise ValueError("该任务没有待恢复事务")
        if action == "resume":
            return _resume_locked(root, journal)
        for row in journal["changes"]:
            if _hash_current(root, row["path"]) not in {row["before_sha256"], row["after_sha256"]}:
                raise Conflict(f"回滚会覆盖外部修改，已拒绝：{row['path']}")
        journal["status"] = "rolling_back"
        write_json(root, _journal_path(job_id), journal)
        for row in reversed(journal["changes"]):
            actual = _hash_current(root, row["path"])
            if actual == row["before_sha256"]:
                continue
            if actual != row["after_sha256"]:
                raise Conflict(f"回滚途中发现外部修改，已拒绝：{row['path']}")
            before = _unb64(row["before"])
            if before is None:
                with _parent(root, row["path"]) as (fd, name):
                    os.unlink(name, dir_fd=fd)
                    os.fsync(fd)
            else:
                _atomic(root, row["path"], before)
        journal["status"] = "rolled_back"
        write_json(root, _journal_path(job_id), journal)
        return {"status": "rolled_back", "job_id": job_id, "paths": [r["path"] for r in journal["changes"]]}


def _validate_moves(rows: list[dict], lay) -> None:
    """Removing a path is allowed only as part of a move inside this journal.

    This is not a general deletion API. Two moves exist, and both keep the
    original bytes in the same recoverable journal:

    * a card move between domains: only domain/pillar/topic and the matching
      ``flashcards/<domain>`` tag may differ; id, body and review data stay;
    * an inbox archive: an inbox file is removed only when the same
      transaction writes byte-identical content into the sources folder.
    """
    removed = [r for r in rows if r['after'] is None]
    if not removed:
        return
    from .vault import parse_note
    destinations = [r for r in rows if r['before'] is None and r['after'] is not None]
    consumed = set()
    for old in removed:
        if old['before'] is not None and lay.is_inbox_file(old['path']):
            if not any(lay.is_source(r['path']) and r['after'] == old['before'] for r in rows):
                raise ValueError('Inbox原件只能在同一事务把相同字节存入来源目录后移走')
            continue
        path = relative(old['path'])
        if not lay.is_card(old['path']) or old['before'] is None:
            raise ValueError('只能迁移已有知识卡，不能直接删除文件')
        old_fm, old_body = parse_note(_unb64(old['before']))
        if not isinstance(old_fm.get('id'), str) or not old_fm['id']:
            raise ValueError('迁移卡缺少稳定ID')
        matches = [r for r in destinations if lay.is_card(r['path'])
                   and relative(r['path']).name == path.name and r['path'] != old['path']]
        if len(matches) != 1 or matches[0]['path'] in consumed:
            raise ValueError('迁移必须有唯一未占用的新路径，不能只删除旧卡')
        new = matches[0]
        old_domain, new_domain = lay.card_domain(old['path']), lay.card_domain(new['path'])
        new_fm, new_body = parse_note(_unb64(new['after']))
        preserved = {k: v for k, v in old_fm.items() if k not in {'domain', 'pillar', 'topic'}}
        if 'tags' in preserved:
            old_tag, new_tag = f'flashcards/{old_domain}', f'flashcards/{new_domain}'
            tags = preserved['tags']
            preserved['tags'] = [new_tag if t == old_tag else t for t in tags] if isinstance(tags, list) else new_tag if tags == old_tag else tags
        if (old_body != new_body
                or old_fm.get('domain') != old_domain
                or new_fm.get('domain') != new_domain
                or preserved != {k: v for k, v in new_fm.items() if k not in {'domain', 'pillar', 'topic'}}):
            raise ValueError('迁移必须保留ID、正文、复习字段和其他元数据')
        consumed.add(new['path'])


def _validate_checks(checks: dict, written: set[str]) -> None:
    if not isinstance(checks, dict):
        raise ValueError('只读依赖必须是路径到hash的映射')
    for rel, sha in checks.items():
        relative(rel)
        if rel in written or (sha is not None and (not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha))):
            raise ValueError('只读依赖重复写路径或hash非法')

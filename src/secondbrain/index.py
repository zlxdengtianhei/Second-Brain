"""Persistent search index over cards (SQLite FTS5, standard library only).

Documents are the tokens produced by :func:`secondbrain.search._tokenize`
(lower-cased latin words plus CJK unigrams and bigrams) joined by spaces, so
FTS5's ``unicode61`` tokenizer sees exactly those terms and its BM25 ranking
(k1=1.2, b=0.75) matches the in-memory reference ranker closely.

The index lives in the vault's external state root (``cache/search.sqlite``).
Before each query it is synchronized with the snapshot by card hash: only
added, changed or removed cards are touched. When the Python build has no
FTS5, :func:`search` falls back to the in-memory ranker.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

from .search import SearchError, _snippet, _tokenize
from .search import search as memory_search
from .vault import Snapshot

INDEX_REL = 'cache/search.sqlite'
SCHEMA_VERSION = '1'
MAX_QUERY_TERMS = 64

_FTS5: bool | None = None


def fts5_available() -> bool:
    global _FTS5
    if _FTS5 is None:
        try:
            with sqlite3.connect(':memory:') as conn:
                conn.execute('CREATE VIRTUAL TABLE t USING fts5(x)')
            _FTS5 = True
        except sqlite3.OperationalError:
            _FTS5 = False
    return _FTS5


def _cards_digest(snapshot: Snapshot) -> str:
    h = hashlib.sha256(b'secondbrain-index/1\n')
    for cid in sorted(snapshot.cards):
        h.update(cid.encode('utf-8') + b'\0' + snapshot.cards[cid].sha256.encode('ascii') + b'\n')
    return h.hexdigest()


def index_path(root) -> Path:
    from .store import state_root
    folder = state_root(root) / 'cache'
    os.makedirs(folder, mode=0o700, exist_ok=True)
    if folder.is_symlink():
        raise ValueError('index folder must not be a symlink')
    return folder / 'search.sqlite'


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS docs (rowid INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, sha TEXT NOT NULL);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(tokens, tokenize = 'unicode61');
        CREATE VIRTUAL TABLE IF NOT EXISTS vocab USING fts5vocab(fts, 'row');
    ''')
    return conn


def _meta(conn, key):
    row = conn.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
    return row[0] if row else None


def sync(conn: sqlite3.Connection, snapshot: Snapshot) -> dict:
    """Bring the index to the snapshot's cards; returns change counts."""
    digest = _cards_digest(snapshot)
    if _meta(conn, 'schema') == SCHEMA_VERSION and _meta(conn, 'cards_digest') == digest:
        return {'added': 0, 'updated': 0, 'removed': 0}
    conn.execute('BEGIN IMMEDIATE')
    try:
        if _meta(conn, 'schema') != SCHEMA_VERSION:
            conn.execute('DELETE FROM fts')
            conn.execute('DELETE FROM docs')
        current = {cid: (rowid, sha) for rowid, cid, sha in conn.execute('SELECT rowid, id, sha FROM docs')}
        counts = {'added': 0, 'updated': 0, 'removed': 0}
        for cid, (rowid, _sha) in current.items():
            if cid not in snapshot.cards:
                conn.execute('DELETE FROM fts WHERE rowid = ?', (rowid,))
                conn.execute('DELETE FROM docs WHERE rowid = ?', (rowid,))
                counts['removed'] += 1
        for cid, card in snapshot.cards.items():
            old = current.get(cid)
            if old is not None and old[1] == card.sha256:
                continue
            tokens = ' '.join(_tokenize(card.title + '\n' + card.body))
            if old is None:
                rowid = conn.execute('INSERT INTO docs (id, sha) VALUES (?, ?)', (cid, card.sha256)).lastrowid
                counts['added'] += 1
            else:
                rowid = old[0]
                conn.execute('UPDATE docs SET sha = ? WHERE rowid = ?', (card.sha256, rowid))
                conn.execute('DELETE FROM fts WHERE rowid = ?', (rowid,))
                counts['updated'] += 1
            conn.execute('INSERT INTO fts (rowid, tokens) VALUES (?, ?)', (rowid, tokens))
        conn.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', ('schema', SCHEMA_VERSION))
        conn.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', ('cards_digest', digest))
        conn.execute('COMMIT')
        return counts
    except BaseException:
        conn.execute('ROLLBACK')
        raise


def _query_terms(conn, qtokens: list[str]) -> list[str]:
    unique = list(dict.fromkeys(qtokens))
    if len(unique) <= MAX_QUERY_TERMS:
        return unique
    marks = ','.join('?' * len(unique))
    df = dict(conn.execute(f'SELECT term, doc FROM vocab WHERE term IN ({marks})', unique))
    present = [t for t in unique if t in df]
    present.sort(key=lambda t: (df[t], unique.index(t)))
    return present[:MAX_QUERY_TERMS]


def search(root, snapshot: Snapshot, query: str, limit: int = 12) -> list[dict]:
    """Top ``limit`` cards by BM25; same result shape as the in-memory ranker."""
    if not isinstance(query, str):
        raise SearchError('query must be a string')
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise SearchError('limit must be a positive integer')
    if not fts5_available():
        return memory_search(snapshot, query, limit=limit)
    try:
        path = index_path(root)
    except (OSError, ValueError):
        return memory_search(snapshot, query, limit=limit)
    qtokens = _tokenize(query)
    if not qtokens:
        return []
    conn = _connect(path)
    try:
        sync(conn, snapshot)
        terms = _query_terms(conn, qtokens)
        if not terms:
            return []
        match = ' OR '.join('"' + t.replace('"', '""') + '"' for t in terms)
        rows = conn.execute(
            'SELECT docs.id, bm25(fts) AS score FROM fts JOIN docs ON docs.rowid = fts.rowid '
            'WHERE fts MATCH ? ORDER BY score, docs.id LIMIT ?', (match, limit)).fetchall()
    finally:
        conn.close()
    out = []
    for cid, score in rows:
        card = snapshot.cards.get(cid)
        if card is None:
            continue
        out.append({'id': card.id, 'title': card.title, 'domain': card.domain, 'path': card.path,
                    'score': round(-score, 6),
                    'snippet': _snippet(card, card.body.lower() if card.body else '', terms)})
    return out

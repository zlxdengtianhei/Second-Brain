"""Parsing speed-ups must not change results: libyaml loader, parse cache, FTS5 index."""
from __future__ import annotations

import json
import os
import pickle

import pytest
import yaml

from secondbrain import cache, index, search, store, vault
from tests.helpers import DOMAIN, build_vault

TRICKY = [
    "id: a\ntitle: 'quoted: colon'\ncreated: 2026-08-21\n",
    "id: b\ntitle: 中文标题\naliases: [一, 二]\nrelations:\n  - type: depends_on\n    target: c\n    note: 多行\n",
    "id: c\nbody: |\n  line one\n  line two\nnum: 1e3\nflag: yes\nempty:\nwhen: 2026-08-21T10:00:00+02:00\n",
    "id: d\nnested: {a: [1, 2, {b: null}]}\nset: !!set {x, y}\nbin: !!binary aGVsbG8=\n",
]


@pytest.mark.skipif(vault._NoDupCLoader is None, reason='PyYAML built without libyaml')
@pytest.mark.parametrize('text', TRICKY)
def test_libyaml_and_python_loaders_agree(text):
    c = yaml.load(text, Loader=vault._NoDupCLoader)
    p = yaml.load(text, Loader=vault._NoDupPyLoader)
    assert c == p and type(c) is type(p)


@pytest.mark.parametrize('loader', [vault._NoDupPyLoader, vault._NoDupCLoader])
def test_both_loaders_reject_duplicate_keys(loader):
    if loader is None:
        pytest.skip('PyYAML built without libyaml')
    with pytest.raises(yaml.constructor.ConstructorError, match='duplicate'):
        yaml.load('a: 1\na: 2\n', Loader=loader)


def card(root, cid, body, *, title=None):
    path = root / '10_Knowledge' / DOMAIN / f'{cid}.md'
    path.write_text(f'---\nid: {cid}\ntitle: {title or cid}\ndomain: {DOMAIN}\ncreated: 2026-08-21\n---\n{body}',
                    encoding='utf-8')
    reg = root / '_data/notes_registry.json'
    data = json.loads(reg.read_text())
    if not any(n['id'] == cid for n in data['notes']):
        data['notes'].append({'id': cid, 'path': f'10_Knowledge/{DOMAIN}/{cid}.md',
                              'title': title or cid, 'domain': DOMAIN})
        reg.write_text(json.dumps(data, ensure_ascii=False))
    return path


def counting(monkeypatch):
    calls = []
    real = vault._parse_note

    def counted(raw):
        calls.append(raw)
        return real(raw)
    monkeypatch.setattr(vault, '_parse_note', counted)
    return calls


def same(a, b):
    assert a.fingerprint == b.fingerprint
    assert a.read_hashes == b.read_hashes and a.issues == b.issues
    assert {k: (c.path, c.sha256, c.fm, c.body, c.raw) for k, c in a.cards.items()} == \
           {k: (c.path, c.sha256, c.fm, c.body, c.raw) for k, c in b.cards.items()}


def test_warm_snapshot_reparses_only_changed_cards(tmp_path, monkeypatch):
    root = build_vault(tmp_path)
    for i in range(5):
        card(root, f'card_{i}', f'## 本质\n正文 {i}\n')
    calls = counting(monkeypatch)
    cold = vault.read_snapshot(root)
    assert len(calls) == 6
    warm = vault.read_snapshot(root)
    assert len(calls) == 6  # nothing re-parsed
    same(cold, warm)
    path = root / '10_Knowledge' / DOMAIN / 'card_3.md'
    path.write_bytes(path.read_bytes().replace('正文 3'.encode(), '正文 X'.encode()))
    changed = vault.read_snapshot(root)
    assert len(calls) == 7
    assert calls[-1] == path.read_bytes()
    same(changed, vault.read_snapshot(root, use_cache=False))
    assert changed.cards['card_3'].body == '## 本质\n正文 X\n'
    path.unlink()
    removed = vault.read_snapshot(root)
    assert 'card_3' not in removed.cards
    same(removed, vault.read_snapshot(root, use_cache=False))


def test_cache_lives_outside_the_vault_and_damage_only_costs_a_rebuild(tmp_path, monkeypatch):
    root = build_vault(tmp_path)
    first = vault.read_snapshot(root)
    blob = store.state_root(root) / cache.CACHE_REL
    assert blob.is_file() and not any(p.name.endswith('.pkl') for p in root.rglob('*'))
    blob.write_bytes(b'not a pickle')
    calls = counting(monkeypatch)
    same(first, vault.read_snapshot(root))
    assert len(calls) == 1

    class Evil:
        def __reduce__(self):
            return (os.system, ('echo should-not-run',))
    blob.write_bytes(pickle.dumps({'header': cache._header(vault.LOADER_NAME), 'entries': {'x': Evil()}}))
    assert cache.load(root, vault.LOADER_NAME) == {}
    same(first, vault.read_snapshot(root))


def test_header_mismatch_invalidates_the_cache(tmp_path, monkeypatch):
    root = build_vault(tmp_path)
    vault.read_snapshot(root)
    assert cache.load(root, vault.LOADER_NAME)
    assert cache.load(root, 'other-loader') == {}


def test_index_matches_memory_ranker_and_syncs_incrementally(tmp_path):
    if not index.fts5_available():
        pytest.skip('sqlite3 without FTS5')
    root = build_vault(tmp_path)
    texts = {
        'card_bs': ('二分查找', '## 本质\n二分查找每次把有序区间缩小一半，binary search 的循环不变量。\n'),
        'card_tcp': ('TCP 连接建立', '## 本质\nTCP connection setup 用三个报文建立连接，同步序列号。\n'),
        'card_gd': ('梯度下降', '## 本质\n梯度下降沿负梯度更新参数，learning rate 决定步长。\n'),
        'card_bs2': ('二分答案', '## 本质\n在单调判定上做二分查找，求最小可行答案。\n'),
    }
    for cid, (title, body) in texts.items():
        card(root, cid, body, title=title)
    snap = vault.read_snapshot(root)
    for query in ['二分查找', 'tcp connection 连接', '梯度 learning rate', '答案']:
        got = [r['id'] for r in index.search(root, snap, query, 3)]
        want = [r['id'] for r in search.search(snap, query, 3)]
        assert got == want, query
    result = index.search(root, snap, '二分查找', 1)[0]
    assert set(result) == {'id', 'title', 'domain', 'path', 'score', 'snippet'}
    assert result['score'] > 0 and '二分查找' in result['snippet']
    conn = index._connect(index.index_path(root))
    try:
        assert index.sync(conn, snap) == {'added': 0, 'updated': 0, 'removed': 0}
        card(root, 'card_new', '## 本质\n新卡\n')
        (root / '10_Knowledge' / DOMAIN / 'card_gd.md').unlink()
        card(root, 'card_tcp', '## 本质\nTCP 四次挥手释放连接。\n', title='TCP 三次握手')
        assert index.sync(conn, vault.read_snapshot(root)) == {'added': 1, 'updated': 1, 'removed': 1}
    finally:
        conn.close()
    snap2 = vault.read_snapshot(root)
    assert [r['id'] for r in index.search(root, snap2, '挥手', 5)] == ['card_tcp']
    assert index.search(root, snap2, '梯度', 5) == []


def test_index_falls_back_without_fts5(tmp_path, monkeypatch):
    root = build_vault(tmp_path)
    snap = vault.read_snapshot(root)
    monkeypatch.setattr(index, 'fts5_available', lambda: False)
    assert index.search(root, snap, '已有卡', 5) == search.search(snap, '已有卡', 5)


def test_long_queries_keep_the_rarest_terms(tmp_path):
    if not index.fts5_available():
        pytest.skip('sqlite3 without FTS5')
    root = build_vault(tmp_path)
    card(root, 'card_rare', '## 本质\nzebra 是罕见词。\n')
    snap = vault.read_snapshot(root)
    query = ' '.join(f'w{i}' for i in range(200)) + ' zebra'
    assert [r['id'] for r in index.search(root, snap, query, 3)] == ['card_rare']

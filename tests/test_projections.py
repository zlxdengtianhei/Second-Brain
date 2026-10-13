from dataclasses import replace
from datetime import date
import json

from secondbrain import config
from secondbrain.vault import read_snapshot
from secondbrain.projections import review_projection, entry_projection
from tests.helpers import ENTRY_DOCS_LAYOUT, build_vault, DOMAIN, EXISTING


def test_due_inline_new_and_record_use_distinct_plugin_signals(tmp_path):
    root = build_vault(tmp_path)
    path = root / f"10_Knowledge/{DOMAIN}/{EXISTING}.md"
    raw = path.read_text()
    path.write_text(raw.replace("---\nid:", "---\nsr-due: 2026-09-01\ntags: [review]\nid:") + "<!--SR:!2026-09-02,10,250-->\n<!--SR:bad-date-->\n")
    before = path.read_bytes()
    p = review_projection(read_snapshot(root), date(2026, 9, 7))
    assert p.metrics['due_notes'] == 1
    assert p.metrics['due_inline'] == 1
    assert p.metrics['new_notes'] == 0
    assert p.metrics['sr_unparsed'] == 1
    assert path.read_bytes() == before
    path.write_text(raw.replace("---\nid:", "---\ntags: [review]\nkind: record\nid:"))
    assert review_projection(read_snapshot(root), date(2026, 9, 7)).metrics['new_notes'] == 0
    path.write_text(raw.replace("---\nid:", "---\ntags: [review]\nid:"))
    assert review_projection(read_snapshot(root), date(2026, 9, 7)).metrics['new_notes'] == 1


def test_doc_marker_update_preserves_other_user_prose(tmp_path):
    config.activate(replace(config.Settings(), card_language='zh'))
    root = build_vault(tmp_path, lay=ENTRY_DOCS_LAYOUT)
    (root / '使用指南.md').write_text('Before\n<!-- GENERATED:BEGIN kb-stats -->old<!-- GENERATED:END kb-stats -->\nAfter\n')
    projection = entry_projection(read_snapshot(root), date(2026, 9, 7))
    text = projection.files['使用指南.md'].decode()
    assert text.startswith('Before\n') and text.endswith('\nAfter\n')
    assert '**1 张概念卡**' in text and 'old' not in text
    home = projection.files['HOME.md'].decode()
    assert 'sb ingest' in home and 'secondbrain ingest' not in home and 'kb add' not in home
    assert '[[使用指南]] · [[系统总览]]' in home


def test_review_links_count_actual_and_same_transaction_home(tmp_path):
    root = build_vault(tmp_path)
    snapshot = read_snapshot(root)
    without = review_projection(snapshot, date(2026, 9, 7))
    metric = json.loads(without.files['_data/projections/kb_review.json'])
    assert metric['broken_links'] == 1 and metric['broken_links_scope'] == 'review_page'
    with_planned = review_projection(snapshot, date(2026, 9, 7), extra_targets=['HOME.md'])
    assert json.loads(with_planned.files['_data/projections/kb_review.json'])['broken_links'] == 0
    (root / 'HOME.md').write_text('Home')
    with_existing = review_projection(snapshot, date(2026, 9, 7))
    assert json.loads(with_existing.files['_data/projections/kb_review.json'])['broken_links'] == 0

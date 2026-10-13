import pytest

from secondbrain.projections import (
    _replace_or_insert_entry,
    _validate_generated_document,
)
from secondbrain.store import Conflict

def test_n3_zero_entry_inserts_after_stats_without_touching_outer_bytes():
    end_stats = b"<!-- GENERATED:END kb-stats -->"
    migration = b"<!-- GENERATED:BEGIN secondbrain-entry -->X<!-- GENERATED:END secondbrain-entry -->"
    raw = b"before\r\n<!-- GENERATED:BEGIN kb-stats -->old" + end_stats + b"\r\ntrailer"
    out = _replace_or_insert_entry(raw, migration)
    assert out == b"before\r\n<!-- GENERATED:BEGIN kb-stats -->old" + end_stats + migration + b"\r\ntrailer"


def test_n3_existing_entry_replaces_exact_block_and_candidate_validation_rejects_breakage():
    begin = b"<!-- GENERATED:BEGIN secondbrain-entry -->"
    end = b"<!-- GENERATED:END secondbrain-entry -->"
    raw = b"left\n" + begin + b"old" + end + b"\r\nright"
    migration = begin + b"new" + end
    out = _replace_or_insert_entry(raw, migration)
    assert out == b"left\n" + migration + b"\r\nright"
    with pytest.raises(Conflict):
        _validate_generated_document(out.replace(end, b""), "fixture")

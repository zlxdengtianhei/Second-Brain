"""Print who gives the final value of everything an ingest job writes from (secondbrain.decisions), as Markdown.

The design's table of final judges is generated from ``decisions.FINAL`` and ``decisions.STEPS``, the original;
run ``python tools/decisions_table.py`` after changing them.
"""
from __future__ import annotations

from secondbrain import decisions

if __name__ == '__main__':
    print(decisions.table(), end='')

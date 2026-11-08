"""The merge check is told that a `replace` keeps every fact of the proposition it replaces (decision D-24 4).

The h7v3 replay found a replacement that dropped a detail of the existing card's proposition. No program check is
added for it: the prompt says the new proposition keeps every fact of the one it replaces, and that when it cannot,
it is carried as a new proposition instead; the job result keeps the words before and after as it did.
"""
from __future__ import annotations

from secondbrain.runner import prompt


def test_the_merge_check_prompt_says_a_replacement_keeps_every_fact_or_is_added_instead():
    text = prompt('source_check.md')
    assert 'The new proposition must keep every fact of the one it replaces; when it cannot, carry it as a new ' \
           'proposition instead and do not use `replace`.' in text
    assert 'which keeps every fact of the one it swaps (when it cannot, add it as a new proposition, not in ' \
           '`replace`)' in text

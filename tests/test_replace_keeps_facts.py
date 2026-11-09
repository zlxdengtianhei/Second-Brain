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


def test_a_carried_proposition_about_the_group_an_old_proposition_sums_up_goes_in_replace():
    """A merge put three propositions about three tools next to the old proposition that named the same three tools,
    so the card said one point twice. The prompt now has each carried proposition checked against the old card's, and
    one about the group of names or terms an old proposition sums up goes in `replace` for it (no program check)."""
    text = prompt('source_check.md')
    assert "Check each proposition you would carry against `with`'s propositions, one by one. When a proposition of " \
           '`with` sums up the things a carried proposition is about (the same group of product names, or the same ' \
           'group of terms), the two make one point: do not add the carried proposition next to it; put it in ' \
           '`replace` for that proposition' in text
    assert 'a proposition about the same group of product names or terms that one of the card\'s propositions sums ' \
           'up makes that proposition\'s point too, and goes in `replace` for it, not next to it' in text

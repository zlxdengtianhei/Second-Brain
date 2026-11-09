"""Two small faults the fifth verdict found (not blocking): what a patch counts as held, and the review rules a step reads.

1. ``ingest._patch_fits`` took the propositions a patch replaces as still in the card, so a carried proposition with
   the same words as one swapped out was left out as held, and the card ended without it (the check before writing
   then stopped the job). Now the card counts as it reads once replaced.
2. ``config.review_policy()`` read the live configuration even inside a job step, which runs on the rules the job
   froze; no step called it, but one that did would have read the wrong rules. Now inside a step it gives the frozen
   ones, as ``config.active_review()`` does.
"""
from __future__ import annotations

from dataclasses import replace

from secondbrain import cards as cardfmt
from secondbrain import config
from secondbrain.pipelines import ingest
from secondbrain.vault import parse_note
from tests.helpers import EXISTING
from tests.test_patch_set import card_of, job


def test_a_proposition_swapped_out_is_not_counted_as_held_by_the_card(tmp_path):
    root, _, _ = job(tmp_path)
    old = card_of(root, EXISTING)
    second = 'Old proposition 2 about routing readers.'
    patch = {'replace': [{'proposition': 'P2', 'by': 'Old proposition 2 about routing readers, fuller.'}],
             'propositions': [second]}
    problem, add, add_f = ingest._patch_fits(old, patch)
    assert problem is None and add == [second]
    raw = cardfmt.patch(old.raw, taken=patch, add_props=add, replace_props={2: patch['replace'][0]['by']})
    assert cardfmt.propositions_of(parse_note(raw)[1])[1:] == [patch['replace'][0]['by'], second]
    held = {'propositions': ['Old proposition 1 about routing readers.']}  # one the card keeps is still held
    assert ingest._patch_fits(old, held)[1] == []


def test_inside_a_job_step_the_review_rules_are_the_frozen_ones():
    live = config.review_policy()
    frozen = config.Settings(review=replace(live, max_repair_rounds=live.max_repair_rounds + 3))
    with config.job_settings(frozen):
        assert config.review_policy() == frozen.review == config.active_review()
    assert config.review_policy() == live
    config.set_review_policy(replace(live, max_invalid_retries=7))
    try:
        assert config.review_policy().max_invalid_retries == 7  # the override, outside a step
        with config.job_settings(frozen):
            assert config.review_policy() == frozen.review
    finally:
        config.set_review_policy(None)

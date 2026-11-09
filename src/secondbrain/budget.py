"""The budgets of a job step: answers that failed the program's checks, and repair rounds.

Every job kind that asks a model (ingest, export, classification, curation, relocation, domcreate) counts an answer
that failed the program's checks in the job state, by slot and role or step, with :func:`count`, and every one asks
:func:`used_up` whether that budget (``max_invalid_retries``) is spent. The count reaches this module through one
gate, :func:`secondbrain.jobs.refuse`: an answer the validator refused, a submission that is not a JSON object, a
file that is not JSON, an executor's answer that is no JSON object, and a host run given up without an answer all
go there, in API and host mode alike. :func:`rounds_left` is the one test of the repair rounds a slot has left.
"""
from __future__ import annotations


def count(row: dict, message: str, *, field: str = 'count', error: str = 'message', keep: int = 8000) -> int:
    """Count one refused answer in ``row`` (``row[field]`` + 1) and keep its message (``row[error]``); returns the
    count so far. ``row`` lives in the job state, so a job run again goes on from it."""
    row[field] = number = int(row.get(field, 0) or 0) + 1
    row[error] = str(message)[:keep]
    return number


def used_up(number: int, retries: int) -> bool:
    """Whether ``number`` refused answers spend a budget of ``retries`` asks again (``max_invalid_retries``): the
    first answer and ``retries`` more may be refused before the step is given up."""
    return int(number) > int(retries)


def rounds_left(used: int, allowed: int) -> int:
    """The repair rounds a slot has left when it used ``used`` of ``allowed`` (``max_repair_rounds``); never below 0."""
    return max(0, int(allowed) - int(used))

"""Replay a reviewer's task of an earlier ingest job with the prompts of this version.

Every run keeps the exact text its model was given (``<state>/jobs/<job>/packets/reviewer-<slot>-<run>.md``: the
instructions, the output schema and the task packet). To measure what a change of the review prompts catches and
what it sends back wrongly, give a model the same packet with this version's instructions and schema, and read its
answer the way the program reads it:

    python tools/review_replay.py render OLD_TASK.md > NEW_TASK.md
    python tools/review_replay.py read NEW_TASK.md ANSWER.json

``render`` keeps the packet as it was and puts in this version's instructions, assembled by the same functions a
job uses (with the same added parts: mechanical fixes, a focused repair round, a retry), and output schema; a note
the earlier answer got from the validator is left out. It knows a chunk's review, a split card's review and the
merge check.

``read`` first runs the program's checks of the answer that need only the packet: the types against the schema,
every card judged once, the notes' kinds, a split review's ``restates`` and ``matches``, the rule of a focused
round, the merge check's checks against its packet. An answer the program would refuse (and ask for again) prints
one line ``REFUSED`` with the reasons. Otherwise it prints, one line per card, the decision once the program's rules
are applied (an accept with a note of a blocking kind is a revise), the kind of that note, and the question the
reviewer wrote; for the merge check, each patch's question and each disputed card. What needs the job's state (the
vault's other cards, the job's policy beyond what the task text shows, mechanical fixes) is not checked.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

from secondbrain import review_notes, schemas
from secondbrain.executors.base import render_task_text
from secondbrain.pipelines import ingest, source_check, split

#: Each kind of task: the heading its instructions start with, and its schema.
KINDS = {
    'review': ('## Task: independently review proposed cards for this chunk', schemas.REVIEW_CARDS),
    'split': ('## Task: independently review a narrower card', split.REVIEW_SCHEMA),
    'merge': ('## Task: merge cards of this source', source_check.SCHEMA),
}
#: The parts a chunk's review may have after its prompt, each known by its heading.
PARTS = {'amend': '## Mechanical fixes', 'focused': '## This is a repair round: judge the fixes',
         'retry': '## This chunk was ingested before'}


def parse(text: str) -> tuple[str, str, dict]:
    """``(kind, job_id, packet)`` of a task text."""
    kind = next((k for k, (heading, _) in KINDS.items() if heading in text), None)
    if kind is None:
        raise SystemExit('not a review task this tool knows (a chunk review, a split card review, the merge check)')
    job = re.search(r'job `([^`]+)`', text)
    packet = re.search(r'<task_packet>\n(.*)\n</task_packet>', text, re.S)
    if not job or not packet:
        raise SystemExit('the task text has no job id or no task packet')
    return kind, job.group(1), json.loads(packet.group(1))


def render(text: str) -> str:
    kind, job_id, packet = parse(text)
    if kind == 'merge':
        instructions = ingest.merge_check_prompt(job_id)
    elif kind == 'split':
        instructions = ingest.split_review_prompt(job_id)
    else:
        instructions = ingest.review_prompt(job_id, **{k: heading in text for k, heading in PARTS.items()})
    return render_task_text(instructions, packet, KINDS[kind][1])


def checked(task_text: str, answer) -> tuple[str, dict]:
    """``(kind, answer as the program takes it)``; raises ``ValueError`` with the reasons the program refuses it."""
    kind, _, packet = parse(task_text)
    if kind == 'merge':
        return kind, source_check.validate_answer(packet, copy.deepcopy(answer))
    if kind == 'split':
        card = packet.get('card') or {}
        existing = (packet.get('existing_card') or {}).get('propositions') or []
        return kind, split.validate_review(copy.deepcopy(answer), card=card, existing=len(existing))
    candidate = packet.get('candidate') or {}
    domains = [d.get('slug') for d in packet.get('domains') or [] if isinstance(d, dict)]
    review = ingest.validate_review(candidate, copy.deepcopy(answer), set(packet.get('candidate_cards') or {}), domains)
    problems = ingest.focused_problems(review) if PARTS['focused'] in task_text else []
    if problems:
        raise ValueError('Review rejected by validation:\n- ' + '\n- '.join(problems))
    return kind, {**review, 'cards': [review_notes.turned(r) for r in review.get('cards') or []]}


def read(task_text: str, answer) -> list[str]:
    try:
        kind, taken = checked(task_text, answer)
    except Exception as exc:  # what the program refuses is asked for again: it is no decision
        return ['REFUSED\t' + ' '.join(str(exc).split())]
    if kind == 'merge':
        return ([f'patch\t{p.get("target")}\t{p.get("question") or ""}' for p in taken.get('patches') or []] +
                [f'dispute\t{d.get("slug")}\t{d.get("reason") or ""}' for d in taken.get('disputes') or []])
    rows = taken.get('cards') or [] if kind == 'review' else [taken]
    return ['\t'.join([str(row.get('slug', 'card')), str(row.get('decision')), str(row.get('blocking') or ''),
                       str(row.get('question') or '')]) for row in rows]


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'render':
        print(render(Path(sys.argv[2]).read_text(encoding='utf-8')), end='')
    elif len(sys.argv) == 4 and sys.argv[1] == 'read':
        for line in read(Path(sys.argv[2]).read_text(encoding='utf-8'),
                         json.loads(Path(sys.argv[3]).read_text(encoding='utf-8'))):
            print(line)
    else:
        raise SystemExit(__doc__)

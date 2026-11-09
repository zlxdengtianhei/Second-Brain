"""Replay a reviewer's task of an earlier ingest job with the prompts of this version.

Every run keeps the exact text its model was given (``<state>/jobs/<job>/packets/reviewer-<slot>-<run>.md``: the
instructions, the output schema and the task packet). To measure what a change of the review prompts catches and
what it sends back wrongly, give a model the same packet with this version's instructions and schema, and read its
answer the way the program reads it:

    python tools/review_replay.py render OLD_TASK.md > NEW_TASK.md
    python tools/review_replay.py read NEW_TASK.md ANSWER.json

``render`` keeps the packet as it was and puts in this version's instructions (with the same added parts: mechanical
fixes, a focused repair round, a retry) and output schema; a note the earlier answer got from the validator is left
out. It knows a chunk's review, a split card's review and the merge check. ``read`` prints, one line per card, the
decision once the program's rules are applied (an accept with a note of a blocking kind is a revise), the kind of
that note, and the question the reviewer wrote; for the merge check, each patch's question and each disputed card.
It checks the answer's shape only; the checks against the job's state are the job's.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from secondbrain import review_notes, schemas
from secondbrain.executors.base import render_task_text
from secondbrain.pipelines import source_check, split
from secondbrain.runner import prompt

#: Each kind of task: the heading its instructions start with, its prompt, its schema.
KINDS = {
    'review': ('## Task: independently review proposed cards for this chunk', 'review_cards.md', schemas.REVIEW_CARDS),
    'split': ('## Task: independently review a narrower card', 'review_split.md', split.REVIEW_SCHEMA),
    'merge': ('## Task: merge cards of this source', 'source_check.md', source_check.SCHEMA),
}
#: The parts a chunk's review may have after its prompt, each known by its heading.
PARTS = (('## Mechanical fixes', 'review_amend.md'), ('## This is a repair round: judge the fixes', 'review_focused.md'),
         ('## This chunk was ingested before', 'retry_review_cards.md'))


def parse(text: str) -> tuple[str, str, dict]:
    """``(kind, job_id, packet)`` of a task text."""
    kind = next((k for k, (heading, _, _) in KINDS.items() if heading in text), None)
    if kind is None:
        raise SystemExit('not a review task this tool knows (a chunk review, a split card review, the merge check)')
    job = re.search(r'job `([^`]+)`', text)
    packet = re.search(r'<task_packet>\n(.*)\n</task_packet>', text, re.S)
    if not job or not packet:
        raise SystemExit('the task text has no job id or no task packet')
    return kind, job.group(1), json.loads(packet.group(1))


def render(text: str) -> str:
    kind, job_id, packet = parse(text)
    _, name, schema = KINDS[kind]
    if kind == 'merge':
        instructions = prompt(name).replace('{job_id}', job_id)
    else:
        instructions = prompt('common_cards.md').replace('{job_id}', job_id) + '\n' + prompt(name)
    if kind == 'review':
        for heading, part in PARTS:
            if heading in text:
                instructions += '\n' + prompt(part)
    return render_task_text(instructions, packet, schema)


def read(task_text: str, answer: dict) -> list[str]:
    kind, _, _ = parse(task_text)
    if kind == 'merge':
        return ([f'patch\t{p.get("target")}\t{p.get("question") or ""}' for p in answer.get('patches') or []] +
                [f'dispute\t{d.get("slug")}\t{d.get("reason") or ""}' for d in answer.get('disputes') or []])
    rows = answer.get('cards') or [] if kind == 'review' else [answer]
    out = []
    for row in rows:
        problems = review_notes.problems(row.get('notes'), str(row.get('slug', 'review')),
                                         review_notes.CARD_CATEGORIES)
        final = review_notes.turned(row)
        out.append('\t'.join([str(row.get('slug', 'card')), str(final.get('decision')), str(final.get('blocking') or ''),
                              str(row.get('question') or ''), '; '.join(problems)]))
    return out


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'render':
        print(render(Path(sys.argv[2]).read_text(encoding='utf-8')), end='')
    elif len(sys.argv) == 4 and sys.argv[1] == 'read':
        for line in read(Path(sys.argv[2]).read_text(encoding='utf-8'),
                         json.loads(Path(sys.argv[3]).read_text(encoding='utf-8'))):
            print(line)
    else:
        raise SystemExit(__doc__)

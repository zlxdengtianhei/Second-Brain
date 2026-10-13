"""A small vault where one large domain shares the generic words of a source about another domain."""
from __future__ import annotations

import json
from pathlib import Path

from tests.helpers import build_vault

DOMAINS = {
    'algorithms': ('Example Algorithms Map', 'Searching and sorting', 'Basics'),
    'agent-harness': ('Example Agents Map', 'Agent loop', 'Tools'),
    'networking': ('Example Networks Map', 'Reservations', 'Signalling'),
}
ALGORITHM_CARDS = {
    'Binary search': 'Binary search keeps a sorted range lo to hi and halves it each step; the loop invariant '
                     'keeps the answer inside the range.',
    'Merge sort': 'Merge sort splits the array into halves, sorts each half and merges the sorted halves in '
                  'linear time; it is stable.',
    'Quick sort partition': 'Partition moves elements smaller than the pivot to the left and larger ones to the '
                            'right in one pass.',
    'Two pointers': 'Two pointers walk a sorted array from both ends and move the pointer whose value is too '
                    'small or too large.',
}
AGENT_TERMS = ['tool schema', 'sandbox', 'retry budget', 'context window', 'planner', 'token quota', 'judge',
               'submission', 'harness', 'trace', 'memory store', 'handoff', 'guardrail', 'evaluator',
               'scratchpad', 'orchestrator', 'router', 'critic', 'worker pool', 'checkpoint', 'transcript',
               'policy', 'reward', 'rollout', 'episode', 'observation', 'action space', 'stop token', 'prompt',
               'system message']
NETWORK_CARDS = {
    'RSVP soft state': 'RSVP keeps reservation soft state that expires unless PATH and RESV messages refresh it.',
    'Multicast trees': 'Multicast routing builds a tree so one packet reaches every member of a group.',
}

SOURCE = """# Practice notes: searching and sorting

Binary search needs a sorted range. Each step compares the middle element and halves the range; the loop invariant keeps the answer between lo and hi.

In python a common bug is an off-by-one in the loop condition: with a closed range use lo <= hi, and move lo or hi past the middle element in every step.

Merge sort splits the array into halves, sorts each half and merges the two sorted halves in linear time. Equal elements keep their order, so it is stable.

Quick sort uses a partition step around a pivot: smaller elements go to the left, larger ones to the right, then each side is sorted.

Two pointers on a sorted array start at both ends and move the pointer whose value is too small or too large until they meet.

Aside: the online judge harness runs each submission in a sandbox with a retry budget, a tool schema, a planner, a token quota, a trace, a guardrail and an evaluator.
"""


def build(root: Path) -> Path:
    catalog = {'schema': 'secondbrain-catalog/1',
               'tracks': [{'id': 'all', 'title': 'All', 'domains': sorted(DOMAINS)}],
               'domains': {d: {'moc': moc, 'question': f'Questions about {d}'} for d, (moc, _, _) in DOMAINS.items()},
               'relation_types': ['similar_to', 'depends_on'],
               'inverse_relations': {'similar_to': 'similar_to'}, 'domain_relations': {}}
    root = build_vault(root, catalog=catalog)
    notes = json.loads((root / '_data/notes_registry.json').read_text(encoding='utf-8'))['notes']
    for domain, (moc, pillar, topic) in DOMAINS.items():
        (root / '20_MOCs/_pillars' / f'{moc}.yaml').write_text(json.dumps(
            {'title': moc, 'narrative': 'n', 'pillars': [{'name': pillar, 'order': 1, 'why': 'w', 'topics': [topic]}]}),
            encoding='utf-8')
    cards = [('algorithms', t, b) for t, b in ALGORITHM_CARDS.items()]
    cards += [('networking', t, b) for t, b in NETWORK_CARDS.items()]
    cards += [('agent-harness', f'Agent {term}',
               f'The agent loop calls a tool function in python, appends the result to the message list and '
               f'repeats each step until the model returns a final value; this card is about the {term}.')
              for term in AGENT_TERMS]
    for i, (domain, title, body) in enumerate(cards):
        _, pillar, topic = DOMAINS[domain]
        cid = f'card_r{i:02d}'
        rel = f'10_Knowledge/{domain}/{title}.md'
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'---\nid: {cid}\ntitle: {title}\ndomain: {domain}\npillar: {pillar}\ntopic: {topic}\n---\n'
                        f'## Essence\n{body}\n', encoding='utf-8')
        notes.append({'id': cid, 'path': rel, 'title': title, 'domain': domain})
    (root / '_data/notes_registry.json').write_text(json.dumps({'notes': notes}), encoding='utf-8')
    (root / '00_Inbox').mkdir(exist_ok=True)
    (root / '00_Inbox' / 'practice.md').write_text(SOURCE, encoding='utf-8')
    return root

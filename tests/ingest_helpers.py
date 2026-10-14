"""An invented source and scripted producer/reviewer answers for ingest tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from secondbrain import layout as layouts
from tests.helpers import CATALOG, DOMAIN, EXISTING, PILLAR, TOPIC, build_vault

FIXTURE = Path(__file__).parent / 'fixtures' / 'ingest' / 'lantern_queues.md'
SOURCE_NAME = FIXTURE.name

OTHER_DOMAIN = 'pebbles'
OTHER_MOC = 'Example Pebble Map'
OTHER_PILLAR = 'Pebble basics'
OTHER_TOPIC = 'Sizes'
OTHER_CARD = 'card_pebble'

NEW_TOPIC = 'Markers'

CARDS = {
    'lantern-queue': {
        'slug': 'lantern-queue', 'title': 'Lantern queue', 'kind': 'concept',
        'domain': DOMAIN, 'pillar': PILLAR, 'topic': TOPIC,
        'essence': 'A lantern queue keeps arrival order and lets readers skip ahead by lit markers.',
        'propositions': ['Items stay in arrival order.', 'Readers skip ahead by following lit markers.'],
        'intuition': 'Like lamp posts along a road: you can walk to the next lamp without counting stones.',
        'confusions': ['Unlike an index, markers are placed by count, not by key.'],
        'flashcards': [{'q': 'What order does a lantern queue keep?', 'a': 'Arrival order.'},
                       {'q': 'How does a reader skip ahead?', 'a': 'By jumping to a lit marker.'}],
        'relations': [{'type': 'similar_to', 'target': EXISTING, 'note': 'both route readers past items'},
                      {'type': 'similar_to', 'target': EXISTING, 'note': 'listed twice on purpose'}],
        'aliases': ['lantern list'],
        'source_quotes': ['A lantern queue keeps every item in arrival order but lets a reader skip ahead by lit markers.'],
        'status': 'confirmed', 'reason': 'PRODUCER-REASON-1 one structure, one question',
    },
    'lit-marker-cost': {
        'slug': 'lit-marker-cost', 'title': 'Lit marker memory cost', 'kind': 'fact',
        'domain': DOMAIN, 'pillar': PILLAR, 'topic': NEW_TOPIC,
        'essence': 'Each lit marker costs one extra word, one marker per eight items.',
        'propositions': ['A writer lights a marker every eight items.',
                         'Each marker costs one word of memory; 800 items carry 100 markers.'],
        'intuition': None, 'confusions': [],
        'flashcards': [{'q': 'How often is a marker lit?', 'a': 'Every eight items.'},
                       {'q': 'How many markers do 800 items carry?', 'a': '100.'},
                       {'q': 'What does one marker cost?', 'a': 'One extra word of memory.'}],
        'relations': [{'type': 'depends_on', 'target': 'lantern-queue', 'note': 'markers belong to the queue'},
                      {'type': 'part_of', 'target': EXISTING, 'note': 'weak guess', 'tier': 'extension'}],
        'source_quotes': ['A writer lights a marker every eight items.',
                          'so a queue of 800 items carries 100 markers'],
        'status': 'confirmed', 'reason': 'PRODUCER-REASON-2 cost is recalled on its own',
    },
    'marker-dimming': {
        'slug': 'marker-dimming', 'title': 'Marker dimming', 'kind': 'mechanism',
        'domain': DOMAIN, 'pillar': PILLAR, 'topic': NEW_TOPIC,
        'essence': 'Compaction deletes old markers.',
        'propositions': ['Markers before the cut are deleted.'],
        'intuition': None, 'confusions': [],
        'flashcards': [{'q': 'What happens to markers before a cut?', 'a': 'They are deleted.'},
                       {'q': 'Are dimmed markers followed?', 'a': 'No.'}],
        'relations': [{'type': 'depends_on', 'target': 'lit-marker-cost', 'note': 'dimming acts on markers'}],
        'source_quotes': ['markers before the cut are dimmed rather than deleted'],
        'status': 'confirmed', 'reason': 'PRODUCER-REASON-3 compaction behaviour',
    },
    'queue-order': {
        'slug': 'queue-order', 'title': 'Arrival order in lantern queues', 'kind': 'fact',
        'domain': DOMAIN, 'pillar': PILLAR, 'topic': TOPIC,
        'essence': 'Items keep arrival order.',
        'propositions': ['A lantern queue keeps every item in arrival order.'],
        'intuition': None, 'confusions': [],
        'flashcards': [{'q': 'Which order is kept?', 'a': 'Arrival order.'},
                       {'q': 'Does a lantern queue reorder items?', 'a': 'No.'}],
        'relations': [],
        'source_quotes': ['A lantern queue keeps every item in arrival order'],
        'status': 'confirmed', 'reason': 'PRODUCER-REASON-4 order fact',
    },
}

FIXED_DIMMING = {
    **CARDS['marker-dimming'],
    'essence': 'Compaction dims markers before the cut instead of deleting them.',
    'propositions': ['Markers before the cut are dimmed, not deleted.',
                     'Dimmed markers stay readable but are never followed.'],
    'flashcards': [{'q': 'What happens to markers before a compaction cut?', 'a': 'They are dimmed, not deleted.'},
                   {'q': 'Are dimmed markers followed?', 'a': 'No, they stay readable only.'}],
    'source_quotes': ['markers before the cut are dimmed rather than deleted; dimmed markers stay readable but are never followed'],
    'reason': 'PRODUCER-REASON-5 fixed after review',
}

TOPIC_PROPOSAL = {'domain': DOMAIN, 'pillar': PILLAR, 'name': NEW_TOPIC,
                  'boundary': 'How markers are placed, paid for and retired.',
                  'reason': 'PRODUCER-REASON-6 no topic covers markers'}

ALL_REASONS = [c['reason'] for c in CARDS.values()] + [FIXED_DIMMING['reason'], TOPIC_PROPOSAL['reason'],
                                                       'PRODUCER-REASON-7', 'PRODUCER-REASON-8']


def coverage(slugs):
    rows = []
    for slug in slugs:
        rows.append({'source_quote': CARDS[slug]['source_quotes'][0], 'disposition': 'card',
                     'cards': [slug], 'reason': 'PRODUCER-REASON-7 covered'})
    rows.append({'source_quote': 'Thanks for reading, see you next week!', 'disposition': 'out_of_scope',
                 'reason': 'PRODUCER-REASON-8 sign-off'})
    return rows


def first_candidate():
    slugs = ['lantern-queue', 'lit-marker-cost', 'marker-dimming', 'queue-order']
    return {'cards': [CARDS[s] for s in slugs], 'new_topics': [TOPIC_PROPOSAL], 'coverage': coverage(slugs)}


def repair_candidate():
    return {'cards': [FIXED_DIMMING], 'new_topics': [],
            'coverage': [{'source_quote': FIXED_DIMMING['source_quotes'][0], 'disposition': 'card',
                          'cards': ['marker-dimming'], 'reason': 'PRODUCER-REASON-7 covered'}]}


def first_review():
    return {'cards': [{'slug': 'lantern-queue', 'decision': 'accept', 'reason': 'faithful and atomic',
                       'overlap': 'distinct'},  # both link the existing card, so the reviewer must say this
                      {'slug': 'lit-marker-cost', 'decision': 'accept', 'reason': 'numbers match the text',
                       'overlap': 'distinct'},
                      {'slug': 'marker-dimming', 'decision': 'revise',
                       'reason': 'the text says dimmed rather than deleted; the card says deleted'},
                      {'slug': 'queue-order', 'decision': 'duplicate', 'duplicate_of': EXISTING,
                       'reason': 'already covered by the existing card'}],
            'new_topics': [{'domain': DOMAIN, 'pillar': PILLAR, 'name': NEW_TOPIC, 'decision': 'accept',
                            'reason': 'markers deserve their own topic'}],
            'coverage': {'decision': 'accept', 'reason': 'every paragraph is handled'}}


def repair_review(decision='accept'):
    return {'cards': [{'slug': 'marker-dimming', 'decision': decision,
                       'reason': 'now matches the text' if decision == 'accept' else 'still wrong'}],
            'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'covered with earlier round'}}


def build_ingest_vault(root: Path, lay: layouts.VaultLayout | None = None) -> Path:
    """The shared tiny vault plus a second, unrelated MOC that ingest must not touch."""
    lay = lay or layouts.DEFAULT
    catalog = json.loads(json.dumps(CATALOG))
    catalog['domains'][OTHER_DOMAIN] = {'moc': OTHER_MOC, 'question': 'How big are pebbles?'}
    catalog['tracks'].append({'id': 'stones', 'title': 'Stones', 'domains': [OTHER_DOMAIN]})
    root = build_vault(root, catalog=catalog, lay=lay)
    (root / lay.taxonomy / f'{OTHER_MOC}.yaml').write_text(json.dumps(
        {'title': OTHER_MOC, 'narrative': 'n', 'pillars': [
            {'name': OTHER_PILLAR, 'order': 1, 'why': 'w', 'topics': [OTHER_TOPIC]}]}), encoding='utf-8')
    path = root / lay.card_path(OTHER_DOMAIN, 'Pebble sizes.md')
    path.parent.mkdir(parents=True)
    path.write_text(f'---\nid: {OTHER_CARD}\ntitle: Pebble sizes\ndomain: {OTHER_DOMAIN}\n'
                    f'pillar: {OTHER_PILLAR}\ntopic: {OTHER_TOPIC}\n---\n## Essence\nPebbles come in sizes.\n',
                    encoding='utf-8')
    registry = json.loads((root / lay.registry).read_text(encoding='utf-8'))
    registry['notes'].append({'id': OTHER_CARD, 'path': lay.card_path(OTHER_DOMAIN, 'Pebble sizes.md'),
                              'title': 'Pebble sizes', 'domain': OTHER_DOMAIN})
    (root / lay.registry).write_text(json.dumps(registry), encoding='utf-8')
    (root / lay.inbox).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE, root / lay.inbox / SOURCE_NAME)
    return root


#: A valid diagram IR for the repaired "Marker dimming" card (flow with a labelled decision).
DIMMING_IR = {
    'decision': 'draw', 'form': 'flow', 'question': 'What happens to a marker when the queue is compacted?',
    'nodes': [{'key': 'c', 'label': 'Compaction', 'p': ['E'], 'role': 'start'},
              {'key': 'q', 'label': 'Before the cut?', 'p': ['P1'], 'role': 'decision'},
              {'key': 'd', 'label': 'Dimmed, not deleted', 'p': ['P1']},
              {'key': 'k', 'label': 'Markers stay', 'p': ['P1']},
              {'key': 'r', 'label': 'Stay readable', 'p': ['P2'], 'role': 'end'}],
    'edges': [{'from': 'c', 'to': 'q'},
              {'from': 'q', 'to': 'd', 'label': 'before the cut', 'p': ['P1']},
              {'from': 'q', 'to': 'k', 'label': 'not before the cut', 'p': ['P1']},
              {'from': 'd', 'to': 'r', 'label': 'never followed', 'p': ['P2']},
              {'from': 'k', 'to': 'r'}],
}
SKIP_IR = {'decision': 'skip', 'reason': 'the propositions already say it; a picture adds nothing'}


def diagram_answer(packet, irs=None):
    """The model side of a diagram request: an IR per card (skip unless given)."""
    irs = irs or {}
    return {'results': [{'ref': c['ref'], 'ir': irs.get(c['ref'], SKIP_IR)} for c in packet['cards']]}


def packet_in(text):
    """The task packet inside a rendered task text (packet file or API user message)."""
    start = text.index('<task_packet>\n') + len('<task_packet>\n')
    return json.loads(text[start:text.index('\n</task_packet>', start)])


def is_diagram_task(task):
    from secondbrain.pipelines.card_diagrams import is_slot
    return is_slot(task.chunk)


def is_source_check_task(task):
    from secondbrain.pipelines.source_check import is_slot
    return is_slot(task.chunk)


def keep_all(packet, **decisions):
    """A source-check answer: ``keep`` for every card and topic unless ``decisions`` (slug -> row) says otherwise.

    A card with no relation is said to stand alone unless its row says something else.
    """
    def row(card):
        base = {'slug': card['slug'], 'decision': 'keep', **({'alone': 'test'} if card.get('isolated') else {})}
        given = decisions.get(card['slug'], {})
        return {**base, **given} if 'links' not in given and given.get('decision') != 'same' else \
            {k: v for k, v in {**base, **given}.items() if k != 'alone'}
    return {'cards': [row(c) for c in packet['cards']],
            'new_topics': [{'id': t['id'], 'decision': 'keep', **decisions.get(t['id'], {})}
                           for t in packet['new_topics']]}


def with_source_check(responder, seen=None, **decisions):
    """Wrap a reviewer responder so the source check gets ``keep_all`` (with ``decisions``)."""
    def respond(task):
        if is_source_check_task(task):
            if seen is not None:
                seen.append(task)
            return keep_all(task.packet, **decisions)
        return responder(task)
    return respond


def with_diagrams(responder, irs=None, seen=None):
    """Wrap a producer responder so diagram requests get scripted IRs."""
    def respond(task):
        if is_diagram_task(task):
            if seen is not None:
                seen.append(task)
            return diagram_answer(task.packet, irs)
        return responder(task)
    return respond


def scripted(producer_outputs, reviewer_outputs, diagrams=None):
    """Responders that hand out answers in call order and record the packets they saw.

    Diagram requests are answered from ``diagrams`` (ref -> IR; skip otherwise)
    and recorded under ``seen['diagram']``; the source check (sources whose
    cards come from two or more chunks) keeps every card, recorded under
    ``seen['source_check']``.
    """
    seen = {'producer': [], 'reviewer': [], 'diagram': [], 'source_check': []}

    def make(role, queue):
        def respond(task):
            if role == 'producer' and is_diagram_task(task):
                seen['diagram'].append(task)
                return diagram_answer(task.packet, diagrams)
            if role == 'reviewer' and is_source_check_task(task):
                seen['source_check'].append(task)
                return keep_all(task.packet)
            seen[role].append(task)
            if not queue:
                raise AssertionError(f'unexpected {role} call for {task.chunk}')
            value = queue.pop(0)
            return value(task) if callable(value) else value
        return respond
    return make('producer', list(producer_outputs)), make('reviewer', list(reviewer_outputs)), seen


def tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file()}

"""cards.patch: an existing card takes propositions, review cards, a source and relations, and nothing else changes."""
from __future__ import annotations

import pytest

from secondbrain import cards as cardfmt
from secondbrain.textnorm import same_text
from secondbrain.vault import parse_note

OLD = ('---\nid: demo_lit-markers\ntitle: Lit markers let readers skip\ndomain: algorithms\npillar: Queues\n'
       'topic: Markers\nsources:\n- path: 40_Sources/a.md\n  sha256: aaa\n  locator: Writers light markers.\n'
       'relations:\n- type: part_of\n  target: demo_queue\nstatus: confirmed\ncreated: 2026-01-02\n---\n'
       '## Essence\nWriters light a marker every eight items.\n\n## Propositions\n'
       '- P1. A writer lights a marker every eight items.\n- P2. A reader skips to the next marker.\n\n'
       '## Intuition and contrasts\n- Intuition: Like lamp posts.\n\n## Related\n- part_of:: [[Lantern queue]]\n\n'
       '## Review cards\nHow often is a marker lit?::Every eight items.\nWhat does a reader skip to?::The next marker.\n')
TITLES = {'demo_queue': 'Lantern queue', 'demo_cost': 'Marker cost'}


def patched(**fields):
    return cardfmt.patch(OLD.encode(), title_of=TITLES.get, **fields)


def test_a_patch_adds_numbered_propositions_review_cards_a_source_and_a_relation_and_keeps_the_rest():
    out = patched(add_props=['Each marker costs one word.'], replace_props={2: 'A reader skips to the next lit marker.'},
                  add_flashcards=[{'q': 'What does a marker cost?', 'a': 'One word.'}],
                  add_source={'path': '40_Sources/b.md', 'sha256': 'bbb', 'locator': 'Markers cost', 'propositions': ['P3']},
                  add_relations=[{'type': 'similar_to', 'target': 'demo_cost'}])
    fm, body = parse_note(out)
    assert cardfmt.propositions_of(body) == ['A writer lights a marker every eight items.',
                                             'A reader skips to the next lit marker.', 'Each marker costs one word.']
    assert cardfmt.flashcards_of(body)[:2] == cardfmt.flashcards_of(parse_note(OLD.encode())[1])  # untouched
    assert fm['sources'][-1]['propositions'] == ['P3'] and len(fm['sources']) == 2
    assert {'type': 'similar_to', 'target': 'demo_cost'} in fm['relations']
    assert '- similar_to:: [[Marker cost]]' in body and '- part_of:: [[Lantern queue]]' in body
    assert '## Intuition and contrasts\n- Intuition: Like lamp posts.\n' in body
    assert out.decode().startswith(OLD.split('sources:')[0])  # front matter before the lists unchanged


@pytest.mark.parametrize('body, reason', [
    ('## Propositions\n- first\n- second\n\n## Review cards\nq::a\n', 'not numbered'),
    ('## Propositions\n- P1. one\n- P3. three\n\n## Review cards\nq::a\n', 'not numbered'),
    ('## Propositions\n- P1. one\n\n## Review cards\nquestion without answer\n', 'has no ::'),
    ('## Propositions\n- P1. one\n', 'no review-card section'),
])
def test_a_card_out_of_sb_format_is_refused(body, reason):
    with pytest.raises(cardfmt.PatchRefused, match=reason):
        cardfmt.patch(f'---\nid: demo_x\ntitle: X\n---\n{body}'.encode(), add_props=['new'])


def test_a_replacement_outside_the_cards_propositions_is_refused():
    with pytest.raises(cardfmt.PatchRefused, match='P1-P2'):
        patched(replace_props={3: 'nothing there'})


def test_same_text_ignores_case_spaces_punctuation_and_character_forms():
    assert same_text('A writer lights a marker, every 8 items.', 'a writer lights a marker every 8 items')
    assert same_text('ＡＢＣ　１２３', 'abc123') and not same_text('A writer lights', 'A reader lights')

"""A card answers one question; a card that says one thing twice goes back; an example gives its premises.

Blind judges of four groups of written cards found, in the group a cheaper model wrote and a strong model reviewed,
most problem cards answering two questions a reader could ask apart (which tools there are, and how to choose among
them), and cards whose two propositions made the same point; both shapes came most often from points added to an
existing card. The judgment "one recallable unit" was not a thing a reviewer could check. Now:

- one rule says what one question is (:mod:`secondbrain.unit`), in the same words for the producers, every reviewer
  of a card and the merge check composing a patch, and the levels of a repeat are defined by it;
- a reviewer of a card writes the card's question (``question``) before its decision, then checks each proposition
  and review card against it; the question goes on to the merge check;
- a card reviewer's notes have a sixth kind, ``repeats``, which sends an accepted card back as the other blocking
  kinds do; a reviewer of an article's paragraphs keeps the five kinds;
- a patch says the question of the card it goes into, and a card of another question is disputed, judged again and
  written as a card of its own.

The cards and texts are invented.
"""
from __future__ import annotations

import copy

import pytest

from secondbrain import overlap, review_notes, schemas, unit
from secondbrain.export import pipeline as export
from secondbrain.pipelines import source_check, split
from secondbrain.runner import prompt
from tests.ingest_helpers import first_candidate, first_review, keep_all, repair_candidate, repair_review
from tests.test_review_notes import accepted_with
from tests.test_review_rules import run as run_rules

CARD_PROMPTS = ('produce_cards.md', 'split_card.md', 'review_cards.md', 'review_split.md', 'source_check.md')
CARD_REVIEWS = ('review_cards.md', 'review_split.md')
ARTICLE_REVIEWS = ('export_review.md', 'export_smooth_review.md')


# --- one text ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('name', CARD_PROMPTS)
def test_every_prompt_that_writes_or_judges_a_card_has_the_one_question_rule_once(name):
    text = prompt(name)
    assert text.count(unit.ONE_QUESTION) == 1 and unit.PLACEHOLDER not in text


@pytest.mark.parametrize('name', CARD_REVIEWS)
def test_a_card_reviewer_writes_the_question_first_and_reads_repeats_and_premises(name):
    text = prompt(name)
    assert text.count(review_notes.card_part()) == 1 and review_notes.CARD_PLACEHOLDER not in text
    assert review_notes.definition() in text  # the five kinds every reviewer has, word for word as before
    for words in ('`repeats`', 'in `question` first', 'P1 is the first', 'Q1 is the first', '`not_a_unit` note that names them',
                  'the premises it uses (the rule, the coordinates, the code book, the input values)'):
        assert words in text, words


@pytest.mark.parametrize('name', ARTICLE_REVIEWS)
def test_an_article_reviewer_keeps_its_five_kinds(name):
    text = prompt(name)
    assert review_notes.definition() in text
    assert '`repeats`' not in text and unit.ONE_QUESTION not in text and review_notes.PREMISES not in text


def test_the_levels_of_a_repeat_are_defined_by_the_question():
    meaning = overlap.definition()
    assert 'the card answers another question than the cards in `with`, also when it is about the same things' in meaning
    assert 'every proposition it adds answers that question' in meaning
    assert 'sub-question' in meaning  # a narrower sub-question still extends
    assert 'even when it is about the same object' in overlap.UNIT_RULE


def test_the_question_comes_before_the_decision_in_each_card_review():
    row = schemas.REVIEW_CARDS['properties']['cards']['items']['properties']
    assert list(row).index('question') < list(row).index('decision')
    assert list(split.REVIEW_SCHEMA['properties'])[0] == 'question'
    assert 'question' in source_check.SCHEMA['properties']['patches']['items']['properties']
    assert 'repeats' in row['notes']['items']['properties']['category']['enum']
    assert 'repeats' in row['blocking']['enum']
    assert 'repeats' not in schemas.EXPORT_REVIEW['properties']['items']['items']['properties']['notes']['items'][
        'properties']['category']['enum']


# --- repeats ------------------------------------------------------------------------------------------------------
TWICE = {'category': 'repeats', 'text': 'P3 says again what P1 says: markers stay lit'}


def test_an_accepted_card_with_a_repeats_note_goes_back_and_spends_a_repair_round(tmp_path):
    root, job_id, done, seen = run_rules(tmp_path, [first_candidate(), repair_candidate()],
                                         [accepted_with(TWICE), repair_review()])
    assert done['status'] == 'applied', done.get('pending')
    assert [t.chunk for t in seen['reviewer']] == ['c01', 'c01.r1']
    [finding] = seen['reviewer'][1].packet['previous_findings']['cards']
    assert finding['blocking'] == 'repeats' and finding['reason'] == f"the review noted a repeats problem: {TWICE['text']}"
    assert [t['category'] for t in done['notes_turned']] == ['repeats']


def test_a_split_review_that_accepts_with_a_repeats_note_is_a_revise():
    out = split.validate_review({'question': 'How does a reader skip ahead?', 'decision': 'accept',
                                 'restates': False, 'reason': 'ok', 'notes': [TWICE]})
    assert out['decision'] == 'revise' and out['blocking'] == 'repeats' and out['turned']['note'] == TWICE


def test_an_article_reviewer_cannot_note_repeats():
    with pytest.raises(ValueError, match=r'item p1.1.notes\[0\].category must be one of'):
        export._validate_review({}, {'items': [{'id': 'p1.1', 'decision': 'accept', 'reason': 'ok',
                                                'notes': [TWICE]}]}, ['p1.1'])


def test_a_repair_round_review_may_send_a_card_back_for_repeats(monkeypatch):
    from types import SimpleNamespace

    from secondbrain.pipelines import ingest
    review = {'cards': [{'slug': 'marker-dimming', 'decision': 'revise', 'reason': 'P3 repeats P1',
                         'blocking': 'repeats', 'fixed': [{'finding': 'the date', 'fixed': True}]}]}
    monkeypatch.setattr(ingest.jobs, 'policy_of', lambda packet: SimpleNamespace(focused_review=True))
    ingest._check_focused({}, {}, 'c01.r1', review)  # taken
    with pytest.raises(ValueError, match='repeats'):  # another reason is still refused, and the message names it
        ingest._check_focused({}, {}, 'c01.r1', {'cards': [{**review['cards'][0], 'blocking': 'style'}]})


# --- the question goes on to the merge check -----------------------------------------------------------------------
def test_the_question_a_reviewer_writes_reaches_the_merge_check(tmp_path):
    """The chunk reviewer writes each card's question; the merge check sees it on the card, and on a card that
    extends an existing card."""
    from tests.test_patch_set import job, reviewer, run
    from tests.test_invariants import LEVELS
    root, job_id, _ = job(tmp_path)
    checks = []
    plain = reviewer(checks)

    def review(task):
        answer = plain(task)
        if not source_check.is_slot(task.chunk) and not task.chunk.startswith('xr'):  # a chunk's review
            for row in answer.get('cards') or []:
                row['question'] = f'What does {row["slug"]} answer?'
        return answer
    done = run(root, job_id, review)
    assert done['status'] == 'applied', done.get('pending')
    packet = checks[0].packet
    extending = [c for g in packet['patch_set'] for c in g['from']]
    assert extending and all(c['question'] == f'What does {c["slug"]} answer?' for c in extending)
    assert packet['cards'] and all(c['question'] == f'What does {c["slug"]} answer?' for c in packet['cards'])
    assert any(LEVELS.get(c['slug'], {}).get('overlap') == 'extends' for c in extending)


def test_a_card_that_answers_another_question_than_the_card_it_would_go_into_is_written_on_its_own(tmp_path):
    """The shape blind judges found: an existing card answers which things there are, and a new card on the same
    things answers how to choose among them. The merge check writes the existing card's question in its patch,
    disputes the card of the other question, the card is judged again and written as a card of its own; the
    existing card is not changed."""
    from secondbrain import decisions
    from tests.test_patch_set import job, reviewer, run, unchanged
    root, job_id, before = job(tmp_path)
    checks = []

    def check(packet):
        if checks[1:]:
            return keep_all(packet)
        answer = keep_all(packet, _disputes=('lantern-queue',))
        for d in answer['disputes']:
            d['reason'] = ('the existing card answers "which routes are there"; this card answers "how to choose '
                           'a route": another question')
        for p in answer.get('patches') or []:
            p['question'] = 'Which routes are there?'
        return answer
    done = run(root, job_id, reviewer(checks, check=check))
    assert done['status'] == 'applied', done.get('pending')
    assert [c.chunk for c in checks] == ['xc01', 'xc01.r1']
    assert decisions.current(done['decision_record'], 'overlap.existing', 'lantern-queue')['reason'] == 'dispute'
    assert done['outcomes']['lantern-queue'] == 'write' and unchanged(root, before)


def test_a_patch_may_say_the_question_it_answers():
    frozen = {'cards': [], 'new_topics': [], 'patch_set': [{
        'target': 'kc_old', 'card': {'propositions': ['Old point.'], 'flashcards': []},
        'from': [{'slug': 'new-card', 'chunk': 'c01', 'title': 'New', 'question': 'Which routes are there?',
                  'essence': 'e', 'propositions': ['A new route exists.'], 'flashcards': []}]}]}
    answer = {'cards': [], 'new_topics': [], 'patches': [{'target': 'kc_old', 'question': 'Which routes are there?',
                                                          'propositions': ['A new route exists.']}]}
    assert source_check.validate_answer(frozen, copy.deepcopy(answer))['patches'][0]['question'] == \
        'Which routes are there?'
    with pytest.raises(ValueError, match='question'):
        source_check.validate_answer(frozen, {**answer, 'patches': [{**answer['patches'][0], 'question': 3}]})


def test_the_question_is_not_written_to_a_card(tmp_path):
    """The reviewer's question stays in the review: the vault a job writes is the same, byte for byte, with it or
    without it."""
    from tests.test_patch_set import job, reviewer, run

    def vault_after(where, with_question):
        root, job_id, _ = job(where)
        plain = reviewer()

        def review(task):
            answer = plain(task)
            if with_question and not source_check.is_slot(task.chunk) and not task.chunk.startswith('xr'):
                for row in answer.get('cards') or []:
                    row['question'] = f'What does {row["slug"]} answer?'
            return answer
        assert run(root, job_id, review)['status'] == 'applied'
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / '10_Knowledge').rglob('*.md')}
    assert vault_after(tmp_path / 'a', True) == vault_after(tmp_path / 'b', False)

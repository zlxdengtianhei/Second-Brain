"""Every value has one final judge (secondbrain.decisions), and a job reads only what it froze.

P8: re-judging and the decision record, on whole jobs (an invented two-chunk source) and on the record itself.
P9: once prepared, a job runs to the end with the configuration unreadable, and writes the same bytes.
The static check: no step reads the configuration, takes a Settings or ReviewPolicy, or sets a card's place or
relations itself; its scope is every module the P9 job ran plus a fixed list.
"""
from __future__ import annotations

import ast
import copy
import inspect
import re
import sys
import textwrap
from pathlib import Path

import pytest

import secondbrain
from secondbrain import config, decisions, jobs, overlap
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest, source_check
from secondbrain.runner import prompt
from secondbrain.vault import read_snapshot
from tests.helpers import EXISTING, PILLAR, TOPIC
from tests.ingest_helpers import (CARDS, NEW_TOPIC, SOURCE_NAME, build_ingest_vault, first_candidate, first_review,
                                  keep_all, scripted, tree, with_diagrams)
from tests.test_ingest import settings

PACKAGE = Path(secondbrain.__file__).parent


# --- one scale: the prompts list exactly overlap.LEVELS ----------------------------------------------------------
INGEST_PROMPTS = ['produce_cards.md', 'review_cards.md', 'repair_cards.md', 'retry_cards.md', 'retry_review_cards.md',
                  'source_check.md', 'review_dispute.md', 'split_card.md', 'review_split.md', 'context_summary.md',
                  'context_concepts.md', 'review_amend.md', 'review_focused.md']


def retired_levels(text: str) -> list[str]:
    """Backticked repeat levels or fields of earlier versions in a prompt (``keep``, ``duplicate`` ...)."""
    return sorted({t for t in re.findall(r'`([a-z_]+)`', text) if t in overlap.RETIRED})


@pytest.mark.parametrize('name', INGEST_PROMPTS)
def test_every_ingest_prompt_uses_only_the_levels_of_the_one_scale(name):
    text = prompt(name)
    assert retired_levels(text) == []
    if overlap.PLACEHOLDER in (PACKAGE / 'prompts' / name).read_text(encoding='utf-8'):
        assert overlap.definition().strip() in text
        assert re.findall(r'^- `([a-z]+)`:', overlap.definition(), re.M) == list(overlap.LEVELS)


def test_the_prompt_check_turns_red_on_an_old_level():
    assert retired_levels('Give `keep` when no other card makes its claim, `same` otherwise.') == ['keep']


def test_the_shared_definition_is_in_the_three_judging_prompts_and_says_what_a_unit_is():
    for name in ('produce_cards.md', 'review_cards.md', 'source_check.md'):
        assert overlap.UNIT_RULE in prompt(name)
    assert 'sub-question' in overlap.definition()  # design D-09: a narrower sub-question of the same card extends it


# --- the record itself --------------------------------------------------------------------------------------------
class Facts:
    def __init__(self, reappears=(), refiled=()):
        self._reappears, self._refiled = set(reappears), set(refiled)

    def reappears(self, obj, earlier, slot):
        return (obj, slot) in self._reappears

    def refiled(self, obj, earlier, slot):
        return (obj, slot) in self._refiled


def level(value='distinct', ids=()):
    return {'overlap': value, 'with': list(ids)}


def test_a_value_has_one_final_role_and_a_second_value_needs_a_reason_that_happened():
    entries = []
    decisions.record(entries, 'overlap.existing', 'card-c', level('same', [EXISTING]), role='reviewer', slot='c01')
    with pytest.raises(decisions.DecisionError, match='judged by the reviewer, not the merge_check'):
        decisions.record(entries, 'overlap.existing', 'card-c', level(), role='merge_check', slot='xc01')
    with pytest.raises(decisions.DecisionError, match='needs a reason'):
        decisions.record(entries, 'overlap.existing', 'card-c', level(), role='reviewer', slot='c01.r1')
    with pytest.raises(decisions.DecisionError, match='shows no repair_round'):  # it is not in round 1
        decisions.record(entries, 'overlap.existing', 'card-c', level(), role='reviewer', slot='c01.r1',
                         reason='repair_round', facts=Facts())
    with pytest.raises(decisions.DecisionError, match='only for repair_round, refile, dispute'):
        decisions.record(entries, 'overlap.existing', 'card-c', level(), role='reviewer', slot='c01.r1',
                         reason='changed_mind', facts=Facts())
    later = decisions.record(entries, 'overlap.existing', 'card-c', level(), role='reviewer', slot='c01.r1',
                             reason='repair_round', facts=Facts(reappears={('card-c', 'c01.r1')}))
    assert decisions.current(entries, 'overlap.existing', 'card-c') == later and later['supersedes'] == 1
    assert len(entries) == 2  # nothing was overwritten
    with pytest.raises(decisions.DecisionError, match='not a value of the decision record'):
        decisions.record(entries, 'overlap', 'card-c', level(), role='reviewer', slot='c01')


def test_a_dispute_supersedes_only_after_the_merge_check_disputed_the_card():
    entries = []
    decisions.record(entries, 'overlap.existing', 'card-d', level('extends', ['demo_z']), role='reviewer', slot='c01')
    with pytest.raises(decisions.DecisionError, match='shows no dispute'):
        decisions.record(entries, 'overlap.existing', 'card-d', level(), role='reviewer', slot='c01',
                         reason='dispute')
    decisions.record(entries, 'dispute', 'card-d', {'reason': 'it answers another question'}, role='merge_check',
                     slot='xc01')
    decisions.record(entries, 'overlap.existing', 'card-d', level(), role='reviewer', slot='c01', reason='dispute')
    assert decisions.current(entries, 'overlap.existing', 'card-d')['data']['overlap'] == 'distinct'


def test_a_whole_merge_check_round_is_superseded_only_after_a_dispute_in_it():
    entries = []
    decisions.record(entries, 'overlap.source', 'card-e', level(), role='merge_check', slot='xc01')
    with pytest.raises(decisions.DecisionError, match='disputed nothing'):  # P10's bad sample
        decisions.supersede_round(entries, role='merge_check', slot='xc01.r1', earlier_slot='xc01', reason='dispute')
    with pytest.raises(decisions.DecisionError, match='only after a dispute'):
        decisions.supersede_round(entries, role='merge_check', slot='xc01.r1', earlier_slot='xc01',
                                  reason='repair_round')


def relation_chain():
    entries = []
    decisions.step(entries, 'relations', 'card-a', 'create', role='producer', slot='c01',
                   data={'relations': [{'type': 'similar_to', 'target': 'demo_b'}]})
    return entries


def test_the_steps_of_a_composite_value_come_in_order_and_each_role_does_only_its_own():
    entries = relation_chain()
    decisions.step(entries, 'relations', 'card-a', 'retype_relation', role='reviewer', slot='c01',
                   data={'target': 'demo_b', 'type': 'part_of'})
    decisions.step(entries, 'relations', 'card-a', 'add', role='merge_check', slot='xc01',
                   data={'relation': {'type': 'similar_to', 'target': 'card-f'}})
    decisions.step(entries, 'relations', 'card-a', 'filter', role='program', slot='write',
                   data={'target': 'card-f', 'type': 'similar_to'})
    assert decisions.fold(entries, 'relations', 'card-a') == [{'type': 'part_of', 'target': 'demo_b'}]
    with pytest.raises(decisions.DecisionError, match='may not remove_relation'):
        decisions.step(entries, 'relations', 'card-a', 'remove_relation', role='merge_check', slot='xc01',
                       data={'target': 'demo_b'})


def test_a_program_filter_before_the_merge_checks_addition_is_refused():
    entries = relation_chain()
    decisions.step(entries, 'relations', 'card-a', 'filter', role='program', slot='write',
                   data={'target': 'demo_b', 'type': 'similar_to'})
    with pytest.raises(decisions.DecisionError, match='cannot come after a program step'):
        decisions.step(entries, 'relations', 'card-a', 'add', role='merge_check', slot='xc01',
                       data={'relation': {'type': 'similar_to', 'target': 'card-f'}})


def test_a_reviewer_step_after_the_merge_check_in_one_round_is_refused_but_a_dispute_starts_anew():
    entries = relation_chain()
    decisions.step(entries, 'relations', 'card-a', 'add', role='merge_check', slot='xc01',
                   data={'relation': {'type': 'similar_to', 'target': 'card-f'}})
    with pytest.raises(decisions.DecisionError, match='cannot come after a merge_check step'):
        decisions.step(entries, 'relations', 'card-a', 'remove_relation', role='reviewer', slot='c01',
                       data={'target': 'card-f'})  # deleting the edge the merge check added
    decisions.record(entries, 'dispute', 'card-a', {'reason': 'r'}, role='merge_check', slot='xc01')
    decisions.step(entries, 'relations', 'card-a', 'remove_relation', role='reviewer', slot='c01', reason='dispute',
                   data={'target': 'demo_b'})
    assert decisions.fold(entries, 'relations', 'card-a') == []  # the producer's create was carried, then removed
    assert [e['op'] for e in decisions.chain(entries, 'relations', 'card-a')] == ['create', 'remove_relation']


def test_the_table_of_the_design_is_generated_from_the_two_tables():
    table = decisions.table()
    assert all(f'| `{value}` | {rule.role} |' in table for value, rule in decisions.FINAL.items())
    assert all(f'| `{value}` | 1 |' in table for value in decisions.STEPS)


# --- whole jobs ---------------------------------------------------------------------------------------------------
ORDER_QUOTE = 'a lantern queue keeps every item in arrival order'
FILLER = ' '.join(f'Filler sentence {i} says nothing about queues.' for i in range(14))
SOURCE = ('# Lantern queues\n\n' + CARDS['lantern-queue']['source_quotes'][0] + '\n\n## Notes\n\n' + FILLER + '\n\n'
          + FILLER + '\n\n## Lit markers\n\nA writer lights a marker every eight items. Markers cost one extra '
          'word of memory each, so a queue of 800 items carries 100 markers. Note again that '
          + ORDER_QUOTE + '.\n')
ARRIVAL = {**CARDS['queue-order'], 'slug': 'arrival-order', 'title': 'Lantern queues keep arrival order',
           'source_quotes': [ORDER_QUOTE], 'relations': [{'type': 'part_of', 'target': 'lantern-queue', 'note': 'n'}]}


#: A card of the first chunk the reviewer finds distinct, so the merge check has cards from two chunks.
FILLER_NOTE = {**CARDS['queue-order'], 'slug': 'filler-note', 'title': 'Filler sentences say nothing',
               'source_quotes': ['Filler sentence 1 says nothing about queues.'], 'relations': []}


def two_chunk_producer(task):
    text = task.packet['chunk']['text']
    cards = []
    if CARDS['lantern-queue']['source_quotes'][0] in text:  # C repeats the existing card; D extends it
        cards += [{**CARDS['lantern-queue'], 'relations': [{'type': 'similar_to', 'target': EXISTING, 'note': 'n'}],
                   'overlap_hint': {'overlap': 'distinct', 'with': []}},  # a misleading hint: never read
                  {**CARDS['queue-order'], 'relations': []}, FILLER_NOTE]
    if ORDER_QUOTE in text:
        cards += [{**CARDS['lit-marker-cost'], 'topic': TOPIC, 'confusions': [],
                   'relations': [{'type': 'depends_on', 'target': 'arrival-order', 'note': 'n'}]}, ARRIVAL]
    rows = [{'source_quote': c['source_quotes'][0], 'disposition': 'card', 'cards': [c['slug']], 'reason': 'r'}
            for c in cards]
    rows.append({'source_quote': text.strip().splitlines()[-1].strip(), 'disposition': 'out_of_scope', 'reason': 'r'})
    return {'cards': cards, 'new_topics': [], 'coverage': rows}


LEVELS = {'lantern-queue': {'overlap': 'extends', 'with': [EXISTING]},   # D: into the patch set
          'queue-order': {'overlap': 'same', 'with': [EXISTING]}}       # C: out before the merge check


def two_chunk_reviewer(seen, check=None):
    def respond(task):
        if source_check.is_slot(task.chunk):
            seen.append(task)
            return (check or keep_all)(task.packet)
        cand = task.packet['candidate']
        return {'cards': [{'slug': c['slug'], 'decision': 'accept', 'reason': 'ok',
                           **LEVELS.get(c['slug'], {'overlap': 'distinct'})} for c in cand['cards']],
                'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}
    return respond


def two_chunk_job(tmp_path, name='vault', **fields):
    root = build_ingest_vault(tmp_path / name)
    settings(chunk_chars=1000, **fields)
    (root / '00_Inbox' / SOURCE_NAME).write_text(SOURCE, encoding='utf-8')
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    return root, job['job_id']


def advance(root, job_id, seen, check=None):
    return ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=with_diagrams(two_chunk_producer)),
                          reviewer=FakeExecutor('claude-test', responder=two_chunk_reviewer(seen, check)))


def test_p7_a_card_the_reviewer_finds_the_same_leaves_before_the_merge_check_with_one_outcome(tmp_path):
    root, job_id = two_chunk_job(tmp_path)
    seen = []
    done = advance(root, job_id, seen)
    assert done['status'] == 'applied', done.get('pending')
    [check] = seen
    judged = {c['slug'] for c in check.packet['cards']}
    assert judged == {'filler-note', 'lit-marker-cost', 'arrival-order'}  # neither C (same) nor D (extends)
    # the existing card here is not in sb format: lantern-queue is written next to it (write, as patch_set says)
    assert done['outcomes'] == {'lantern-queue': 'write', 'queue-order': 'drop', 'filler-note': 'write',
                                'lit-marker-cost': 'write', 'arrival-order': 'write'}
    assert [d['slug'] for d in done['duplicates'] if d['by'] == 'reviewer'] == ['queue-order']
    assert 'Arrival order in lantern queues' not in {c.title for c in read_snapshot(root).cards.values()}
    # the misleading hint on D was not read: it went where the reviewer's level sent it
    assert done['needs_review'][0]['with'] == EXISTING
    # every value has exactly one entry nothing superseded
    record = done['decision_record']
    for value in decisions.FINAL:
        decisions.values(record, value)
    # bad sample 1: the merge check gives C a level -> refused at submit
    answer = keep_all(check.packet)
    answer['cards'].append({'slug': 'queue-order', 'overlap': 'distinct'})
    with pytest.raises(ValueError, match="'queue-order' is not in the packet"):
        source_check.validate_answer(check.packet, answer)
    # bad sample 3: the merge check judges D, whose level is the reviewer's to give
    answer['cards'][-1] = {'slug': 'lantern-queue', 'overlap': 'same', 'with': ['arrival-order'], 'reason': 'r'}
    with pytest.raises(ValueError, match="'lantern-queue' is not in the packet"):
        source_check.validate_answer(check.packet, answer)
    # bad sample 2: another role writes C's level against the vault; the reviewer writes it again with no reason
    state = copy.deepcopy(jobs.load(root, job_id)['decisions'])
    with pytest.raises(decisions.DecisionError, match='judged by the reviewer'):
        decisions.record(state, 'overlap.existing', 'queue-order', level(), role='merge_check', slot='xc01')
    with pytest.raises(decisions.DecisionError, match='needs a reason'):
        decisions.record(state, 'overlap.existing', 'queue-order', level(), role='reviewer', slot='c01')


def test_the_outcome_table_refuses_a_level_against_the_source_for_a_card_judged_against_the_vault():
    entries = []
    decisions.record(entries, 'overlap.existing', 'card-c', level('same', [EXISTING]), role='reviewer', slot='c01')
    decisions.record(entries, 'overlap.source', 'card-c', level(), role='merge_check', slot='xc01')
    with pytest.raises(overlap.OutcomeError, match='not in the merge check'):
        overlap.outcome(entries, 'card-c', checked=True)
    with pytest.raises(overlap.OutcomeError, match='no final level against the vault'):
        overlap.outcome([], 'card-x', checked=False)


def test_a_card_refiled_after_its_new_topic_was_refused_is_judged_again_for_refile(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    review = first_review()
    review['cards'][2] = {'slug': 'marker-dimming', 'decision': 'accept', 'overlap': 'distinct', 'reason': 'fine'}
    review['new_topics'][0].update(decision='revise', reason='too broad')
    refile = [{**CARDS[s], 'topic': TOPIC} for s in ('lit-marker-cost', 'marker-dimming')]
    rows = [{'source_quote': c['source_quotes'][0], 'disposition': 'card', 'cards': [c['slug']], 'reason': 'r'}
            for c in refile]
    again = {'cards': [{'slug': c['slug'], 'decision': 'accept', 'overlap': 'distinct', 'reason': 'ok'}
                       for c in refile], 'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}
    produce, respond, _ = scripted([first_candidate(), {'cards': refile, 'new_topics': [], 'coverage': rows}],
                                   [review, again])
    job = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    done = ingest.advance(root, job['job_id'], producer=FakeExecutor('gpt-test', responder=produce),
                          reviewer=FakeExecutor('claude-test', responder=respond))
    assert done['status'] == 'applied', done.get('pending')
    record = jobs.load(root, job['job_id'])['decisions']
    verdicts = [e for e in record if e['kind'] == 'value' and e['value'] == 'verdict' and e['object'] == 'lit-marker-cost']
    assert [(e['slot'], e.get('reason')) for e in verdicts] == [('c01', None), ('c01.r1', 'refile')]
    chain = decisions.chain(record, 'classification', 'lit-marker-cost')
    assert [(e['role'], e['op']) for e in chain] == [('producer', 'propose'), ('reviewer', 'accept')]
    assert decisions.fold(record, 'classification', 'lit-marker-cost')['topic'] == TOPIC
    assert done['outcomes']['lit-marker-cost'] == 'write'


def test_a_relation_is_made_by_steps_the_producer_creates_the_merge_check_adds(tmp_path):
    def check(packet):
        return keep_all(packet, **{'arrival-order': {'overlap': 'distinct', 'links': [
            {'target': 'filler-note', 'type': 'similar_to', 'note': 'both say what a queue keeps'}]}})
    root, job_id = two_chunk_job(tmp_path)
    done = advance(root, job_id, [], check)
    record = done['decision_record']
    ops = [(e['role'], e['op']) for e in decisions.chain(record, 'relations', 'arrival-order')]
    assert ops == [('producer', 'create'), ('merge_check', 'add')]
    assert done['outcomes']['arrival-order'] == 'write' and done['source_check']['status'] == 'checked'
    written = {c.title: c for c in read_snapshot(root).cards.values()}
    assert {'type': 'similar_to', 'target': next(c.id for c in written.values() if c.title == FILLER_NOTE['title']),
            'note': 'both say what a queue keeps'} in written[ARRIVAL['title']].fm['relations']


# --- P9: once frozen, a job does not read the configuration --------------------------------------------------------
def frozen_run(tmp_path, monkeypatch, name, *, block: bool):
    root, job_id = two_chunk_job(tmp_path, name=name)
    if block:
        def unreadable(*args, **kwargs):
            raise AssertionError('a job step read the configuration after prepare')
        for fn in ('current', 'load', 'review_policy'):
            monkeypatch.setattr(config, fn, unreadable)
    modules = set()

    def profile(frame, event, arg):
        if event == 'call':
            modules.add(frame.f_code.co_filename)
    sys.setprofile(profile)
    try:
        done = advance(root, job_id, [])
    finally:
        sys.setprofile(None)
    if block:
        monkeypatch.undo()
    return root, done, sorted(m for m in modules if Path(m).is_relative_to(PACKAGE))


KNOWLEDGE = ('10_Knowledge/', '20_MOCs/', '40_Sources/')


def test_p9_a_prepared_job_runs_to_the_end_without_reading_the_configuration(tmp_path, monkeypatch):
    plain_root, plain, _ = frozen_run(tmp_path, monkeypatch, 'plain', block=False)
    root, done, _ = frozen_run(tmp_path, monkeypatch, 'blocked', block=True)
    assert done['status'] == plain['status'] == 'applied'
    written = {k: v for k, v in tree(root).items() if k.startswith(KNOWLEDGE)}
    assert written and written == {k: v for k, v in tree(plain_root).items() if k.startswith(KNOWLEDGE)}
    assert done['outcomes'] == plain['outcomes']


def test_p9_a_setting_changed_after_prepare_does_not_change_the_job(tmp_path, monkeypatch):
    plain_root, plain, _ = frozen_run(tmp_path, monkeypatch, 'plain', block=False)
    root, job_id = two_chunk_job(tmp_path, name='changed')
    config.set_review_policy(config.ReviewPolicy(max_repair_rounds=0, withhold_producer_reasons=False))
    config.activate(config.Settings(card_language='zh', card_language_set=True))
    try:
        done = advance(root, job_id, [])
    finally:
        config.set_review_policy(None)
    assert done['outcomes'] == plain['outcomes']
    assert {k: v for k, v in tree(root).items() if k.startswith(KNOWLEDGE)} == \
        {k: v for k, v in tree(plain_root).items() if k.startswith(KNOWLEDGE)}
    frozen = jobs.read_input(root, job_id)['settings']
    assert frozen['review']['max_repair_rounds'] == 2 and frozen['review']['withhold_producer_reasons'] is True


# --- the static check -----------------------------------------------------------------------------------------------
#: Modules always checked, whether or not the P9 job runs them.
LISTED = ['pipelines/', 'jobs.py', 'runner.py', 'gates.py', 'labels.py', 'layout.py', 'reviews.py',
          'diagrams/audit.py', 'executors/host.py', 'decisions.py', 'overlap.py', 'retrieval.py']
#: Where a job is frozen: these may read the configuration (and the config module itself).
FREEZE_POINTS = {'prepare', 'retry'}
CONFIG_READS = {'current', 'load', 'review_policy'}
#: The write step: it reads final values only through secondbrain.decisions.
WRITERS = {('pipelines/ingest.py', '_apply_job'), ('overlap.py', 'outcome'), ('pipelines/source_check.py', 'apply')}
PLACE_KEYS = {'domain', 'pillar', 'topic', 'relations'}


def _functions(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def static_problems(source: str, rel: str) -> list[str]:
    """The three rules on one module's source (``rel``: its path under the package)."""
    tree = ast.parse(textwrap.dedent(source))
    problems = []
    if rel == 'config.py':
        return problems
    parents = {}
    for fn in _functions(tree):
        for child in ast.walk(fn):
            parents.setdefault(child, fn)
    for node in ast.walk(tree):
        fn = parents.get(node)
        name = fn.name if fn else '<module>'
        # 1. no config read outside a freeze point; no private config state anywhere
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'config':
            if node.attr in {'_CURRENT', '_POLICY'}:
                problems.append(f'{rel}:{node.lineno} {name} reads config.{node.attr}')
            elif node.attr in CONFIG_READS and name not in FREEZE_POINTS:
                problems.append(f'{rel}:{node.lineno} {name} calls config.{node.attr}()')
        if isinstance(node, ast.ImportFrom) and (node.module or '').endswith('config'):
            bad = [a.name for a in node.names if a.name in CONFIG_READS | {'_CURRENT', '_POLICY'}]
            if bad:
                problems.append(f'{rel}:{node.lineno} imports {", ".join(bad)} from config')
    for fn in _functions(tree):
        if fn.name in FREEZE_POINTS:
            continue
        # 2. no Settings or ReviewPolicy handed to a step
        for arg in fn.args.args + fn.args.kwonlyargs:
            note = ast.unparse(arg.annotation) if arg.annotation is not None else ''
            if re.search(r'\b(Settings|ReviewPolicy)\b', note) or (arg.arg in {'settings', 'policy'} and not note):
                problems.append(f'{rel}:{fn.lineno} {fn.name} takes {arg.arg}' + (f': {note}' if note else ''))
        # 3. the write step reads no answer and sets no place or relations itself
        if (rel, fn.name) in WRITERS:
            for arg in fn.args.args + fn.args.kwonlyargs:
                if arg.arg in {'answer', 'review'}:
                    problems.append(f'{rel}:{fn.lineno} {fn.name} takes the answer ({arg.arg})')
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and ast.unparse(node.func).endswith('artifact') and \
                        any(isinstance(a, ast.Constant) and a.value == 'review' for a in node.args):
                    problems.append(f'{rel}:{node.lineno} {fn.name} reads a review answer')
                if isinstance(node, (ast.Assign, ast.AugAssign)):
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                        for sub in ast.walk(target):
                            if isinstance(sub, ast.Subscript) and isinstance(sub.slice, ast.Constant) and \
                                    sub.slice.value in PLACE_KEYS:
                                problems.append(f'{rel}:{node.lineno} {fn.name} sets {sub.slice.value} itself')
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                        node.func.attr in {'update', 'setdefault', 'pop'} and (
                        PLACE_KEYS & {k.arg for k in node.keywords} or any(
                            isinstance(a, ast.Constant) and a.value in PLACE_KEYS for a in node.args)):
                    problems.append(f'{rel}:{node.lineno} {fn.name} sets a place or relations itself')
            if rel == 'overlap.py' and 'decisions.' not in ast.unparse(fn):
                problems.append(f'{rel}:{fn.lineno} outcome does not read the decision record')
    return problems


def test_static_check_on_every_module_the_frozen_job_ran(tmp_path, monkeypatch):
    _, _, ran = frozen_run(tmp_path, monkeypatch, 'scope', block=True)
    scope = {Path(m).relative_to(PACKAGE).as_posix() for m in ran}
    for listed in LISTED:
        scope |= {p.relative_to(PACKAGE).as_posix() for p in PACKAGE.glob(listed + ('*.py' if listed.endswith('/')
                                                                                      else ''))}
    assert {'pipelines/ingest.py', 'jobs.py', 'decisions.py', 'store.py', 'moc.py'} <= scope
    problems = [p for rel in sorted(scope) for p in static_problems((PACKAGE / rel).read_text(encoding='utf-8'), rel)]
    assert problems == []


@pytest.mark.parametrize('source, found', [
    ('def outcomes(root):\n    return config.current().review\n', 'outcomes calls config.current()'),
    ('def step(root):\n    return config._CURRENT\n', 'reads config._CURRENT'),
    ('from .config import review_policy\n', 'imports review_policy from config'),
    ('def chunk_outcome(root, settings):\n    return settings\n', 'takes settings'),
    ('def chunk_outcome(root, rules: ReviewPolicy):\n    return rules\n', 'takes rules: ReviewPolicy'),
    ('def _apply_job(root, card):\n    card["domain"] = "x"\n', 'sets domain itself'),
    ('def _apply_job(root, card):\n    card.update(relations=[])\n', 'sets a place or relations itself'),
    ('def _apply_job(root, state):\n    return jobs.artifact(root, state, "review", "c01")\n', 'reads a review answer'),
])
def test_the_static_check_turns_red_on_each_rule(source, found):
    problems = static_problems(source, 'pipelines/ingest.py')
    assert any(found in p for p in problems), problems


def test_the_static_check_lets_a_freeze_point_read_the_configuration():
    assert static_problems('def prepare(root, *, settings=None):\n    return config.current()\n',
                           'pipelines/ingest.py') == []


def test_card_views_and_review_reads_stay_out_of_the_writer():
    assert 'overlap_hint' not in inspect.getsource(overlap.outcome) + inspect.getsource(ingest._apply_job)
    assert NEW_TOPIC and PILLAR  # fixtures used above

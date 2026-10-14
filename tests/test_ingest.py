"""Ingest chain S0-S9 end to end with scripted executors (no model, no network).

The main test walks the acceptance list for a newly ingested source:
file name = title, frontmatter contract, verbatim quotes, source hashes,
rendered sections, registries, MOC scope, run records, idempotent re-run and
byte-exact rollback.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest
import yaml

from secondbrain import cli, config, inbox, jobs
from secondbrain import layout as layouts
from secondbrain.executors import FakeExecutor
from secondbrain.gates import model_family
from secondbrain.pipelines import ingest
from secondbrain.service import refresh
from secondbrain.store import digest, recover
from secondbrain.vault import parse_note, read_snapshot
from tests.helpers import CLOCKS, CUSTOM_LAYOUT, DOMAIN, EXISTING, MOC, PILLAR, TOPIC, pin_clock
from tests.ingest_helpers import (ALL_REASONS, CARDS, DIMMING_IR, FIXED_DIMMING, NEW_TOPIC, OTHER_MOC,
                                  SOURCE_NAME, build_ingest_vault, coverage, diagram_answer, first_candidate,
                                  first_review, packet_in, repair_candidate, repair_review, scripted, tree,
                                  with_diagrams)

HEADINGS = {'en': ['## Essence', '## Propositions', '## Intuition and contrasts', '## Related', '## Review cards'],
            'zh': ['## 本质', '## 命题', '## 直觉与易混对比', '## 相关', '## 复习卡']}


def settings(**ingest_fields):
    current = config.current()
    language = ingest_fields.pop('language', current.card_language)
    return config.activate(replace(current, card_language=language,
                                   ingest=replace(current.ingest, **ingest_fields)))


def executors(producer_outputs, reviewer_outputs, diagrams=None):
    produce, review, seen = scripted(producer_outputs, reviewer_outputs, diagrams)
    return (FakeExecutor('gpt-test', responder=produce), FakeExecutor('claude-test', responder=review), seen)


def run_default(root, lay, **kwargs):
    producer, reviewer, seen = executors([first_candidate(), repair_candidate()],
                                         [first_review(), repair_review()], {'marker-dimming': DIMMING_IR})
    prepared = ingest.prepare(root, root / lay.inbox / SOURCE_NAME)
    result = ingest.advance(root, prepared['job_id'], producer=producer, reviewer=reviewer, **kwargs)
    return prepared, result, seen


def folders(root):
    return {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_dir()}


def moc_files(root, lay, moc):
    return {p: b for p, b in tree(root).items()
            if p == f'{lay.mocs}/{moc}.md' or p.startswith(f'{lay.mocs}/{moc}/')}


@pytest.mark.parametrize('lay, language', [(layouts.DEFAULT, 'zh'), (CUSTOM_LAYOUT, 'en')],
                         ids=['default-layout-zh', 'custom-layout-en'])
def test_ingest_acceptance_list(tmp_path, lay, language):
    root = build_ingest_vault(tmp_path / 'vault', lay)
    settings(language=language)
    assert refresh(root, job_id='moc-before', apply=True)['status'] == 'applied'
    other_moc_before = moc_files(root, lay, OTHER_MOC)
    assert other_moc_before
    before = tree(root)
    folders_before = folders(root)
    original = before[f'{lay.inbox}/{SOURCE_NAME}']
    source_text = original.decode('utf-8')

    item = inbox.scan(root)[0]
    assert (item['status'], item['sha256']) == ('new', digest(original))

    prepared, result, seen = run_default(root, lay)
    job_id = prepared['job_id']
    assert job_id == f'ingest-{digest(original)[:16]}'
    assert result['status'] == 'applied', result
    assert [p['title'] for p in result['produced']] == ['Lantern queue', 'Lit marker memory cost', 'Marker dimming']
    assert [d['slug'] for d in result['duplicates']] == ['queue-order']
    assert result['new_topics'] == [{'domain': DOMAIN, 'pillar': PILLAR, 'name': NEW_TOPIC}]
    assert result['pending'] == [] and result['inbox_moved'] is True

    after = tree(root)
    snap = read_snapshot(root)
    assert snap.issues == []
    source_rel = lay.source_path(digest(original), SOURCE_NAME)
    by_title = {c.title: c for c in snap.cards.values()}
    expected = {'Lantern queue': CARDS['lantern-queue'], 'Lit marker memory cost': CARDS['lit-marker-cost'],
                'Marker dimming': FIXED_DIMMING}
    for title, fields in expected.items():
        card = by_title[title]
        # 1. file name = title; complete frontmatter contract
        assert card.path == lay.card_path(DOMAIN, f'{title}.md')
        fm = card.fm
        assert {'id', 'title', 'kind', 'domain', 'pillar', 'topic', 'tags', 'sources', 'relations',
                'status', 'created'} <= fm.keys()
        assert fm['id'].startswith('kc_') and fm['title'] == title and fm['domain'] == DOMAIN
        assert fm['pillar'] == PILLAR and fm['topic'] == fields['topic']
        assert {'review', f'flashcards/{DOMAIN}'} <= set(fm['tags'])
        assert fm['sources'] == [{'path': source_rel, 'sha256': digest(original),
                                  'locator': fields['source_quotes'][0]}]
        # 2. verbatim quotes; source hashes
        assert all(q in source_text for q in fields['source_quotes'])
        assert digest(after[source_rel]) == fm['sources'][0]['sha256'] == digest(original)
        # 3. sections, 2-4 review lines, no bare ids, no duplicate related lines
        positions = [card.body.find(h + '\n') for h in HEADINGS[language]]
        present = [p for p in positions if p >= 0]  # empty intuition and related sections are left out
        assert positions[0] >= 0 and positions[1] >= 0 and positions[-1] >= 0 and present == sorted(present)
        assert (positions[2] >= 0) == bool(fields.get('intuition') or fields.get('confusions'))
        assert '暂无' not in card.body and 'None yet' not in card.body
        review = card.body.split(HEADINGS[language][-1] + '\n', 1)[1]
        qa = [line for line in review.splitlines() if '::' in line]
        assert 2 <= len(qa) <= 4 and len(qa) == len(fields['flashcards'])
        assert not re.search(r'\b(kc_|card_)[a-z0-9]', card.body)
        related = card.body.split(HEADINGS[language][3] + '\n', 1)[1].split('\n## ', 1)[0]
        lines = [line for line in related.splitlines() if line.startswith('- ')]
        assert len(lines) == len(set(lines))
    lantern, cost, dimming = (by_title[t] for t in expected)
    # diagram step: drawn by code from a validated IR, only where it helps
    diagram_heading = {'zh': '## 图', 'en': '## Diagram'}[language]
    assert diagram_heading not in lantern.body and diagram_heading not in cost.body
    figure = dimming.body.split(diagram_heading + '\n', 1)[1].split('\n## ', 1)[0]
    assert figure.startswith('```mermaid\n%% diagram v1 props=E,P1,P2 sha=') and figure.rstrip().endswith('```')
    assert dimming.body.index(diagram_heading) < dimming.body.index(HEADINGS[language][4])
    assert {d['ref']: d['status'] for d in result['diagrams']} == {
        'lantern-queue': 'skip', 'lit-marker-cost': 'skip', 'marker-dimming': 'draw'}
    [diagram_task] = seen['diagram']
    assert [c['ref'] for c in diagram_task.packet['cards']] == ['lantern-queue', 'marker-dimming']
    assert lantern.fm['aliases'] == ['lantern list']
    assert [r['target'] for r in lantern.fm['relations']].count(EXISTING) == 1
    assert '- similar_to:: [[已有卡]]' in lantern.body
    assert '- depends_on:: [[Lantern queue]]' in cost.body
    assert '<details>' in cost.body and '[[已有卡]] — part_of' in cost.body
    assert ('depends_on', cost.id, 'dimming acts on markers') in {
        (r['type'], r['target'], r.get('note')) for r in dimming.fm['relations']}
    # inverse edges on the existing card: frontmatter and its related section; the old body text stays first
    existing = snap.cards[EXISTING]
    assert ('similar_to', lantern.id) in {(r['type'], r['target']) for r in existing.fm['relations']}
    old_body = parse_note(before[existing.path])[1]
    assert existing.body.startswith(old_body) and '- similar_to:: [[Lantern queue]]' in existing.body[len(old_body):]
    # accepted new topic in the taxonomy file
    taxonomy = yaml.safe_load(after[f'{lay.taxonomy}/{MOC}.yaml'])
    assert taxonomy['pillars'][0]['topics'] == [TOPIC, NEW_TOPIC]
    # 4. registries
    reg_rows = [json.loads(line) for line in after[lay.inbox_registry].decode().splitlines()]
    assert reg_rows[-1]['status'] == 'applied' and reg_rows[-1]['sha256'] == digest(original)
    manifest = [json.loads(line) for line in after[lay.manifest].decode().splitlines()]
    assert manifest[-1]['vault_path'] == source_rel and manifest[-1]['sha256'] == digest(original)
    notes = json.loads(after[lay.registry])['notes']
    assert {n['id'] for n in notes} >= {lantern.id, cost.id, dimming.id}
    assert lay.review_events not in after or after[lay.review_events] == before.get(lay.review_events)
    # inbox original moved: removed from the inbox, identical bytes in sources
    assert f'{lay.inbox}/{SOURCE_NAME}' not in after and after[source_rel] == original
    # 5. only the affected MOC changed; a refresh preview is then a no-op
    assert moc_files(root, lay, OTHER_MOC) == other_moc_before
    moc_before = {p: b for p, b in before.items()
                  if p == f'{lay.mocs}/{MOC}.md' or p.startswith(f'{lay.mocs}/{MOC}/')}
    assert moc_files(root, lay, MOC) != moc_before
    home = after[lay.home].decode('utf-8')
    assert {'zh': f'**{len(snap.cards)} 张概念卡**', 'en': f'**cards: {len(snap.cards)}**'}[language] in home
    assert {'zh': '## 支柱', 'en': '## Pillars'}[language] in after[f'{lay.mocs}/{MOC}.md'].decode('utf-8')
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    assert {p for p in changed if p.startswith(lay.mocs + '/') and not lay.is_taxonomy_file(p)}
    assert not any(p.startswith(f'{lay.mocs}/{OTHER_MOC}') for p in changed)
    assert refresh(root, job_id='moc-after')['changed_files'] == 0
    # 6. run records: distinct runs bound to the frozen input; reviewer saw no producer reasons
    state = jobs.load(root, job_id)
    runs = jobs.list_runs(root, job_id)
    assert len(runs) == 5 and len({r['run_id'] for r in runs}) == 5
    assert all(r['packet_sha256'] == state['input_sha256'] for r in runs)
    families = config.review_policy().model_families
    for run in runs:
        assert run['model_reported']
    producer_models = {r['model_reported'] for r in runs if r['role'] == 'producer'}
    reviewer_models = {r['model_reported'] for r in runs if r['role'] == 'reviewer'}
    assert {model_family(m, families) for m in producer_models}.isdisjoint(
        {model_family(m, families) for m in reviewer_models})
    review_packets = [task.packet_path for task in seen['reviewer']]
    assert len(review_packets) == 2
    for path in review_packets:
        text = open(path, encoding='utf-8').read()
        assert not any(reason in text for reason in ALL_REASONS)
    repair_task = seen['producer'][1]
    assert repair_task.step == 'repair' and repair_task.chunk == 'c01.r1'
    assert repair_task.packet['kept_slugs'] == ['lantern-queue', 'lit-marker-cost']
    assert [c['slug'] for c in repair_task.packet['review_findings']['cards']] == ['marker-dimming', 'queue-order']
    # 7. the same file again is unchanged
    again = ingest.prepare(root, root / source_rel)
    assert again['status'] == 'unchanged'
    [item] = inbox.scan(root)  # the moved source stays listed as done
    assert (item['status'], item['in_inbox'], item['path']) == ('done', False, source_rel)
    # 8. rollback restores every byte (including the inbox original) and removes the folders it created
    assert (root / source_rel).parent.is_dir()
    assert recover(root, job_id, 'rollback')['status'] == 'rolled_back'
    assert tree(root) == before
    assert not (root / source_rel).parent.exists()
    assert folders(root) == folders_before


def _strip_breadcrumbs_field(root, lay):
    """Make MOC pages look like ones written before the BC-list-note-field frontmatter existed."""
    from secondbrain.moc import BC_FRONTMATTER
    for path in (root / lay.mocs).rglob('*.md'):
        text = path.read_text(encoding='utf-8')
        if text.startswith(BC_FRONTMATTER):
            path.write_text(text[len(BC_FRONTMATTER):], encoding='utf-8')


def test_ingest_without_a_prior_refresh_changes_only_the_affected_maps(tmp_path):
    lay = layouts.DEFAULT
    root = build_ingest_vault(tmp_path / 'vault')
    refresh(root, job_id='old-renderer', apply=True)
    _strip_breadcrumbs_field(root, lay)  # pages as an older renderer wrote them
    drift = refresh(root, job_id='preview-before')
    other = moc_files(root, lay, OTHER_MOC)
    assert drift['changed_files'] == len(other) + len(moc_files(root, lay, MOC))
    assert drift['drift']['kinds'] == {'breadcrumbs_field_only': drift['changed_files']}
    assert drift['drift']['by_moc'][OTHER_MOC] == {'breadcrumbs_field_only': len(other)}
    before = tree(root)
    _, result, _ = run_default(root, lay)
    assert result['status'] == 'applied'
    after = tree(root)
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    maps = {p for p in changed if p.startswith(lay.mocs + '/') and not lay.is_taxonomy_file(p)}
    assert maps and all(p == f'{lay.mocs}/{MOC}.md' or p.startswith(f'{lay.mocs}/{MOC}/') for p in maps)
    assert moc_files(root, lay, OTHER_MOC) == other  # untouched, still in the old format
    left = refresh(root, job_id='preview-after')
    assert left['drift']['by_moc'] == {OTHER_MOC: {'breadcrumbs_field_only': len(other)}}
    assert refresh(root, job_id='preview-domain', domains=[DOMAIN])['changed_files'] == 0


def test_cli_ingest_reports_unchanged_and_rolls_back(tmp_path):
    lay = layouts.DEFAULT
    root = build_ingest_vault(tmp_path / 'vault')
    settings(after_ingest='keep')
    before = tree(root)
    prepared, result, _ = run_default(root, lay)
    assert result['status'] == 'applied' and result['inbox_moved'] is False
    assert (root / lay.inbox / SOURCE_NAME).is_file()
    assert inbox.scan(root)[0]['status'] == 'done'
    assert cli.main(['--vault', str(root), 'ingest', str(root / lay.inbox / SOURCE_NAME)]) == 0
    assert cli.main(['--vault', str(root), 'recover', prepared['job_id'], '--rollback']) == 0
    assert tree(root) == before


def test_cli_ingest_prints_unchanged(tmp_path, capsys):
    root = build_ingest_vault(tmp_path / 'vault')
    settings(after_ingest='keep')
    run_default(root, layouts.DEFAULT)
    assert cli.main(['--vault', str(root), 'ingest', str(root / '00_Inbox' / SOURCE_NAME)]) == 0
    assert json.loads(capsys.readouterr().out)[0]['status'] == 'unchanged'


def test_rejected_card_stays_pending_and_the_rest_is_written(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    producer, reviewer, _ = executors([first_candidate(), repair_candidate()],
                                      [first_review(), repair_review('revise')])
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, prepared['job_id'], producer=producer, reviewer=reviewer)
    assert result['status'] == 'partial'
    assert [p['slug'] for p in result['pending']] == ['marker-dimming']
    assert result['pending'][0]['reason'] == 'still wrong'
    assert [p['title'] for p in result['produced']] == ['Lantern queue', 'Lit marker memory cost']
    snap = read_snapshot(root)
    assert 'Marker dimming' not in {c.title for c in snap.cards.values()}
    rows = [json.loads(line) for line in (root / '_data/secondbrain/inbox_registry.jsonl').read_text().splitlines()]
    assert rows[-1]['status'] == 'partial' and rows[-1]['pending'] == 1
    assert not (root / '00_Inbox' / SOURCE_NAME).exists()  # moved to sources even though partial
    assert [(i['status'], i['in_inbox'], i['path']) for i in inbox.scan(root)] == [
        ('partial', False, layouts.DEFAULT.source_path(rows[-1]['sha256'], SOURCE_NAME))]  # still listed
    again = ingest.prepare(root, root / layouts.DEFAULT.source_path(rows[-1]['sha256'], SOURCE_NAME))
    assert (again['status'], again['result'], again['pending']) == ('unchanged', 'partial', 1)
    assert again['hint'] == f'sb ingest --retry {prepared["job_id"]}'


def test_relation_to_a_pending_card_is_not_written(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    review = first_review()
    review['cards'][1]['decision'] = 'revise'
    review['cards'][1]['reason'] = 'numbers are off'
    review['cards'][2] = {'slug': 'marker-dimming', 'decision': 'accept', 'reason': 'fine'}
    again = {'cards': [CARDS['lit-marker-cost']], 'new_topics': [], 'coverage': coverage(['lit-marker-cost'])[:1]}
    producer, reviewer, _ = executors([first_candidate(), again], [review, {
        'cards': [{'slug': 'lit-marker-cost', 'decision': 'revise', 'reason': 'numbers are still off'}],
        'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}])
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, prepared['job_id'], producer=producer, reviewer=reviewer)
    assert result['status'] == 'partial'
    assert {p['slug'] for p in result['pending']} == {'lit-marker-cost'}
    assert result['skipped_relations'][0]['target_slug'] == 'lit-marker-cost'
    dimming = next(c for c in read_snapshot(root).cards.values() if c.title == 'Marker dimming')
    assert dimming.fm['relations'] == [] and 'depends_on::' not in dimming.body


def test_invalid_output_is_returned_with_errors_and_spends_a_round(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    bad = first_candidate()
    bad['cards'][0] = {**bad['cards'][0], 'source_quotes': ['this sentence is not in the source'],
                       'title': 'Bad/Title', 'flashcards': bad['cards'][0]['flashcards'][:1]}
    producer, reviewer, seen = executors([bad, first_candidate(), repair_candidate()],
                                         [first_review(), repair_review()])
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, prepared['job_id'], producer=producer, reviewer=reviewer)
    # the invalid answer used the only repair round, so the revised card stays pending
    assert result['status'] == 'partial'
    assert [p['slug'] for p in result['pending']] == ['marker-dimming']
    retry_prompt = seen['producer'][1].prompt
    assert 'rejected by the validator' in retry_prompt
    for needle in ('not a verbatim slice', 'not allowed in file names', 'flashcards must hold 2-4'):
        assert needle in retry_prompt
    runs = jobs.list_runs(root, prepared['job_id'])
    assert [r['status'] for r in runs if r['role'] == 'producer'].count('failed') == 1


def test_validation_rejects_bad_candidates(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    packet = jobs.read_input(root, prepared['job_id'])
    ids = set(read_snapshot(root).cards)

    def check(candidate, **kw):
        return ingest.validate_candidate(packet, candidate, slot='c01', other_cards=kw.get('other', []),
                                         kept_slugs=set(), existing_ids=ids,
                                         existing_titles={'已有卡'.casefold()})
    assert check(first_candidate())
    cases = {
        'source_quotes': lambda c: c['cards'][0].update(source_quotes=['A lantern queue keeps every item in random order']),
        'flashcards': lambda c: c['cards'][0].update(flashcards=c['cards'][0]['flashcards'] * 3),
        'neither a topic': lambda c: c['cards'][0].update(topic='Unknown topic'),
        'not a catalog domain': lambda c: c['cards'][0].update(domain='nowhere'),
        'already exists': lambda c: c['cards'][0].update(title='已有卡'),
        'not allowed in file names': lambda c: c['cards'][0].update(title='A: B'),
        'already used in this source': lambda c: c['cards'][1].update(slug='lantern-queue'),
        'is neither an existing card id': lambda c: c['cards'][0]['relations'].append(
            {'type': 'depends_on', 'target': 'kc_missing'}),
        'type must be one of': lambda c: c['cards'][0]['relations'].append({'type': 'causes', 'target': EXISTING}),
        'not by id': lambda c: c['cards'][0].update(essence=f'Compare with {EXISTING} and kc_other.'),
        'overlaps existing topic': lambda c: (c['new_topics'][0].update(name='Overviews'),
                                              [x.update(topic='Overviews') for x in c['cards'] if x['topic'] == NEW_TOPIC]),
        'at most 2 new topics': lambda c: c['new_topics'].extend([
            {**c['new_topics'][0], 'name': 'Beacons'}, {**c['new_topics'][0], 'name': 'Lamps'}]),
        'every card needs a coverage row': lambda c: c['coverage'].pop(0),
        'does not exist in': lambda c: c['new_topics'][0].update(pillar='New pillar'),
    }
    for message, mutate in cases.items():
        candidate = json.loads(json.dumps(first_candidate()))
        mutate(candidate)
        with pytest.raises(ingest.IngestError, match=message):
            check(candidate)


def test_review_must_judge_every_card_and_topic():
    candidate = first_candidate()
    review = first_review()
    assert ingest.validate_review(candidate, review, {EXISTING})
    missing = {**review, 'cards': review['cards'][1:]}
    with pytest.raises(ingest.IngestError, match='exactly once'):
        ingest.validate_review(candidate, missing, {EXISTING})
    no_reason = json.loads(json.dumps(review))
    no_reason['cards'][0]['reason'] = ' '
    with pytest.raises(ingest.IngestError, match='concrete reason'):
        ingest.validate_review(candidate, no_reason, {EXISTING})
    bad_dup = json.loads(json.dumps(review))
    bad_dup['cards'][3]['duplicate_of'] = 'kc_missing'
    with pytest.raises(ingest.IngestError, match='duplicate_of'):
        ingest.validate_review(candidate, bad_dup, {EXISTING})


def test_rejected_new_topic_keeps_its_cards_pending(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    review = first_review()
    review['new_topics'][0].update(decision='revise', reason='too narrow for a topic')
    review['cards'][2] = {'slug': 'marker-dimming', 'decision': 'accept', 'reason': 'fine'}
    repair = {'cards': [], 'new_topics': [], 'coverage': [
        {'source_quote': 'Markers cost one extra word', 'disposition': 'needs_taxonomy',
         'suggested_pillar': 'Marker economics', 'reason': 'no fitting pillar'}]}
    producer, reviewer, _ = executors([first_candidate(), repair],
                                      [review, {'cards': [], 'new_topics': [],
                                                'coverage': {'decision': 'accept', 'reason': 'ok'}}])
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    result = ingest.advance(root, prepared['job_id'], producer=producer, reviewer=reviewer)
    assert result['status'] == 'partial' and result['new_topics'] == []
    assert {p['slug'] for p in result['pending']} == {'lit-marker-cost', 'marker-dimming'}
    assert result['needs_taxonomy'][0]['suggested_pillar'] == 'Marker economics'
    snap = read_snapshot(root)
    assert NEW_TOPIC not in snap.taxonomies[MOC]['pillars'][0]['topics']


def test_title_conflict_gets_a_suffix_and_ids_stay_unique(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    producer, reviewer, _ = executors([first_candidate(), repair_candidate()], [first_review(), repair_review()])
    reviewed = ingest.advance(root, prepared['job_id'], producer=producer, reviewer=reviewer, apply=False)
    assert reviewed['status'] == 'reviewed'
    assert [c['title'] for c in reviewed['proposal']['accepted']] == [
        'Lantern queue', 'Lit marker memory cost', 'Marker dimming']
    # a card with the same title appears after the review, before the write
    (root / '10_Knowledge' / DOMAIN / 'Lantern queue.md').write_text(
        f'---\nid: kc_lantern-queue\ntitle: Lantern queue\ndomain: {DOMAIN}\npillar: {PILLAR}\n'
        f'topic: {TOPIC}\n---\n## Essence\nWritten by hand.\n', encoding='utf-8')
    registry = json.loads((root / '_data/notes_registry.json').read_text())
    registry['notes'].append({'id': 'kc_lantern-queue', 'path': f'10_Knowledge/{DOMAIN}/Lantern queue.md',
                              'title': 'Lantern queue', 'domain': DOMAIN})
    (root / '_data/notes_registry.json').write_text(json.dumps(registry))
    result = ingest.advance(root, prepared['job_id'], producer=producer, reviewer=reviewer)
    produced = {p['slug']: p for p in result['produced']}
    assert produced['lantern-queue']['title'] == 'Lantern queue (2)'
    assert produced['lantern-queue']['id'] == 'kc_lantern-queue-2'
    assert produced['lantern-queue']['path'] == f'10_Knowledge/{DOMAIN}/Lantern queue (2).md'
    cost = next(c for c in read_snapshot(root).cards.values() if c.title == 'Lit marker memory cost')
    assert '- depends_on:: [[Lantern queue (2)]]' in cost.body


def test_store_conflict_sends_the_job_to_needs_attention_and_advance_rebuilds(tmp_path, monkeypatch):
    root = build_ingest_vault(tmp_path / 'vault')
    producer, reviewer, _ = executors([first_candidate(), repair_candidate()], [first_review(), repair_review()])
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    real = jobs.finish_transaction
    calls = []

    def racing(root_, job_id, result, changes, **kw):
        if not calls:
            calls.append(1)
            target = root_ / changes[0].relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('appeared meanwhile', encoding='utf-8')
            try:
                return real(root_, job_id, result, changes, **kw)
            finally:
                target.unlink()
        return real(root_, job_id, result, changes, **kw)
    monkeypatch.setattr(jobs, 'finish_transaction', racing)
    with pytest.raises(ingest.Conflict):
        ingest.advance(root, prepared['job_id'], producer=producer, reviewer=reviewer)
    state = jobs.load(root, prepared['job_id'])
    assert state['status'] == 'needs_attention' and 'commit_intent' not in state
    done = ingest.advance(root, prepared['job_id'], producer=producer, reviewer=reviewer)
    assert done['status'] == 'applied' and done['new_card_count'] == 3


def test_chunks_are_produced_in_order_with_earlier_cards_in_context(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    settings(chunk_chars=1000)
    filler = ' '.join(f'Filler sentence {i} says nothing about queues.' for i in range(14))
    long_source = ('# Lantern queues\n\n' + CARDS['lantern-queue']['source_quotes'][0] + '\n\n'
                   + '## Notes\n\n' + filler + '\n\n' + filler + '\n\n'
                   + '## Lit markers\n\nA writer lights a marker every eight items. Markers cost one extra '
                     'word of memory each, so a queue of 800 items carries 100 markers.\n')
    (root / '00_Inbox' / SOURCE_NAME).write_text(long_source, encoding='utf-8')

    def produce(task):
        text = task.packet['chunk']['text']
        slugs = [s for s in ('lantern-queue', 'lit-marker-cost') if CARDS[s]['source_quotes'][0] in text]
        cards = [CARDS[s] for s in slugs]
        rows = [{'source_quote': CARDS[s]['source_quotes'][0], 'disposition': 'card', 'cards': [s],
                 'reason': 'covered'} for s in slugs]
        quote = text.strip().splitlines()[-1].strip()
        rows.append({'source_quote': quote, 'disposition': 'out_of_scope', 'reason': 'rest'})
        topics = [ingest_topic()] if 'lit-marker-cost' in slugs else []
        return {'cards': cards, 'new_topics': topics, 'coverage': rows}

    def review(task):
        cards = [{'slug': c['slug'], 'decision': 'accept', 'reason': 'ok'} for c in task.packet['candidate']['cards']]
        topics = [{**{k: t[k] for k in ('domain', 'pillar', 'name')}, 'decision': 'accept', 'reason': 'ok'}
                  for t in task.packet['candidate']['new_topics']]
        return {'cards': cards, 'new_topics': topics, 'coverage': {'decision': 'accept', 'reason': 'ok'}}
    producer = FakeExecutor('gpt-test', responder=with_diagrams(produce))
    reviewer = FakeExecutor('claude-test', responder=review)
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    packet = jobs.read_input(root, prepared['job_id'])
    assert len(packet['chunks']) >= 2
    assert ''.join(packet['source']['text'][c['start']:c['end']] for c in packet['chunks']) == packet['source']['text']
    result = ingest.advance(root, prepared['job_id'], producer=producer, reviewer=reviewer)
    assert result['status'] == 'applied'
    assert {p['title'] for p in result['produced']} == {'Lantern queue', 'Lit marker memory cost'}
    later = [t for t in producer.calls if t.chunk != 'c01' and not t.chunk.startswith('dg')]
    assert any(c['slug'] == 'lantern-queue' for t in later for c in t.packet['other_chunks_cards'])
    cost = next(c for c in read_snapshot(root).cards.values() if c.title == 'Lit marker memory cost')
    assert '- depends_on:: [[Lantern queue]]' in cost.body


def test_chunk_too_large_is_split_in_halves_and_retried(tmp_path):
    from secondbrain.executors.base import Result
    root = build_ingest_vault(tmp_path / 'vault')
    filler = ' '.join(f'Filler sentence {i} says nothing about queues.' for i in range(14))
    long_source = ('# Lantern queues\n\n' + CARDS['lantern-queue']['source_quotes'][0] + '\n\n'
                   + filler + '\n\n' + filler + '\n\n## Lit markers\n\nA writer lights a marker every '
                   'eight items. Markers cost one extra word of memory each, so a queue of 800 items carries '
                   '100 markers.\n')
    (root / '00_Inbox' / SOURCE_NAME).write_text(long_source, encoding='utf-8')

    def produce(task):
        text = task.packet['chunk']['text']
        if len(text) > 1000:
            return Result(status='failed', attestation='test',
                          error={'code': 'CHUNK_TOO_LARGE', 'message': 'context window exceeded'})
        slugs = [s for s in ('lantern-queue', 'lit-marker-cost') if CARDS[s]['source_quotes'][0] in text]
        rows = [{'source_quote': CARDS[s]['source_quotes'][0], 'disposition': 'card', 'cards': [s],
                 'reason': 'covered'} for s in slugs]
        rows.append({'source_quote': text.strip().splitlines()[-1].strip(), 'disposition': 'out_of_scope',
                     'reason': 'rest'})
        return {'cards': [CARDS[s] for s in slugs], 'new_topics': [ingest_topic()] if 'lit-marker-cost' in slugs
                else [], 'coverage': rows}

    def review(task):
        cand = task.packet['candidate']
        return {'cards': [{'slug': c['slug'], 'decision': 'accept', 'reason': 'ok'} for c in cand['cards']],
                'new_topics': [{**{k: t[k] for k in ('domain', 'pillar', 'name')}, 'decision': 'accept',
                                'reason': 'ok'} for t in cand['new_topics']],
                'coverage': {'decision': 'accept', 'reason': 'ok'}}
    producer = FakeExecutor('gpt-test', responder=with_diagrams(produce))
    reviewer = FakeExecutor('claude-test', responder=review)
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    assert prepared['chunks'] == ['c01']
    result = ingest.advance(root, prepared['job_id'], producer=producer, reviewer=reviewer)
    assert result['status'] == 'applied', result
    assert {p['title'] for p in result['produced']} == {'Lantern queue', 'Lit marker memory cost'}
    state = jobs.load(root, prepared['job_id'])
    assert state['chunks']['c01']['split'] is True
    assert [t.chunk for t in producer.calls][:2] == ['c01', 'c01-1']
    assert {p['chunk'] for p in result['produced']} <= {'c01-1', 'c01-2', 'c01-3'}
    runs = jobs.list_runs(root, prepared['job_id'])
    assert [r['status'] for r in runs if r['chunk'] == 'c01'] == ['failed']


def ingest_topic():
    from tests.ingest_helpers import TOPIC_PROPOSAL
    return TOPIC_PROPOSAL


def test_host_mode_hands_off_packets_and_accepts_submissions(tmp_path):
    from secondbrain.executors.host import HostExecutor
    root = build_ingest_vault(tmp_path / 'vault')
    prepared = ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    job_id = prepared['job_id']
    host = HostExecutor()
    waiting = ingest.advance(root, job_id, producer=host, reviewer=host)
    assert waiting['status'] == 'awaiting_submission'
    [handoff] = waiting['handoffs']
    assert handoff['role'] == 'producer' and handoff['chunk'] == 'c01'
    text = open(handoff['packet_path'], encoding='utf-8').read()
    assert 'Lantern queues' in text and '## Output JSON schema' in text
    assert f"job submit {job_id} --role producer --run-id {handoff['run_id']}" in handoff['submit_cmd']
    # the same hand-off is returned until it is submitted
    assert ingest.advance(root, job_id, producer=host, reviewer=host)['handoffs'][0]['run_id'] == handoff['run_id']

    def submit(h, payload, model):
        path = tmp_path / f"{h['run_id']}.json"
        path.write_text(json.dumps(payload), encoding='utf-8')
        assert cli.main(['--vault', str(root), 'job', 'submit', job_id, '--role', h['role'], '--run-id',
                         h['run_id'], '--file', str(path), '--model', model]) == 0
    submit(handoff, first_candidate(), 'gpt-test')
    review = ingest.packet_for(root, job_id, role='reviewer', chunk=None, executor=host)
    assert review['role'] == 'reviewer'
    text = open(review['packet_path'], encoding='utf-8').read()
    assert not any(reason in text for reason in ALL_REASONS)
    submit(review, first_review(), 'gpt-test-2')  # same family: allowed in host mode, but marked
    repair = ingest.advance(root, job_id, producer=host, reviewer=host)['handoffs'][0]
    assert repair['chunk'] == 'c01.r1' and repair['step'] == 'repair'
    submit(repair, repair_candidate(), 'gpt-test')
    last = ingest.advance(root, job_id, producer=host, reviewer=host)['handoffs'][0]
    submit(last, repair_review(), 'claude-test')
    [diagram] = ingest.advance(root, job_id, producer=host, reviewer=host)['handoffs']
    assert diagram['step'] == 'diagram' and diagram['chunk'] == 'dg01' and diagram['role'] == 'producer'
    request = packet_in(open(diagram['packet_path'], encoding='utf-8').read())
    assert [c['ref'] for c in request['cards']] == ['lantern-queue', 'marker-dimming']
    assert ingest.packet_for(root, job_id, role='producer', chunk='dg01', executor=host)['run_id'] == diagram['run_id']
    submit(diagram, diagram_answer(request, {'marker-dimming': DIMMING_IR}), 'gpt-test')
    done = ingest.advance(root, job_id, producer=host, reviewer=host)
    assert done['status'] == 'applied' and done['same_family'] is True
    assert [d['ref'] for d in done['diagrams'] if d['status'] == 'draw'] == ['marker-dimming']
    state = jobs.load(root, job_id)
    assert state['same_family'] is True and state['chunks']['c01']['same_family'] is True
    runs = jobs.list_runs(root, job_id)
    assert {r['attestation'] for r in runs} == {'self_reported'}


def test_api_mode_rejects_a_same_family_pair_before_any_call(tmp_path):
    from secondbrain.executors.factory import build
    endpoint = config.EndpointConfig(kind='openai', base_url='https://api.example.com/v1', model='gpt-a',
                                     api_key_env='SB_TEST_KEY')
    s = replace(config.Settings(), producer=endpoint, reviewer=replace(endpoint, model='gpt-b'))
    with pytest.raises(ValueError, match='both'):
        build(s, 'api')
    unknown = replace(s, reviewer=replace(endpoint, model='mystery-model'))
    with pytest.raises(ValueError, match='Unknown model family'):
        build(unknown, 'api')
    host_p, host_r = build(s, 'host')
    assert host_p is host_r and host_p.name == 'host'


def test_inbox_scan_skip_and_legacy_records(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    (root / '00_Inbox' / 'old.md').write_text('already done by the old chain\n', encoding='utf-8')
    (root / '00_Inbox' / 'noise.md').write_text('a shopping list\n', encoding='utf-8')
    (root / '00_Inbox' / '.hidden').write_text('x', encoding='utf-8')
    old_sha = digest(b'already done by the old chain\n')
    (root / '_data' / 'ingested.jsonl').write_text(json.dumps({'file': 'old.md', 'sha256': old_sha}) + '\n')
    items = {i['name']: i for i in inbox.scan(root)}
    assert set(items) == {'old.md', 'noise.md', SOURCE_NAME}
    assert items['old.md']['status'] == 'done' and items[SOURCE_NAME]['status'] == 'new'
    assert inbox.skip(root, 'noise.md', 'not knowledge')['changed'] is True
    assert {i['name']: i['status'] for i in inbox.scan(root)}['noise.md'] == 'skipped'
    assert ingest.prepare(root, root / '00_Inbox' / 'noise.md')['status'] == 'skipped'
    assert ingest.prepare(root, root / '00_Inbox' / 'old.md')['status'] == 'unchanged'
    ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    assert {i['name']: i['status'] for i in inbox.scan(root)}[SOURCE_NAME] == 'in_progress'
    with pytest.raises(ValueError, match='reason'):
        inbox.skip(root, SOURCE_NAME, ' ')


def test_source_limits(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    settings(max_source_bytes=10)
    with pytest.raises(ingest.IngestError, match='larger than 10 bytes'):
        ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    settings(max_source_bytes=8 * 1024 * 1024)
    (root / '00_Inbox' / 'scan.pdf').write_bytes(b'%PDF-1.4\n\xff\xfe\x00binary')
    with pytest.raises(ingest.IngestError, match='converter'):
        ingest.prepare(root, root / '00_Inbox' / 'scan.pdf')
    (root / '00_Inbox' / 'link.md').symlink_to(root / '00_Inbox' / SOURCE_NAME)
    with pytest.raises(ingest.IngestError, match='symlink'):
        ingest.prepare(root, root / '00_Inbox' / 'link.md')


def test_optional_git_commit_takes_only_the_written_paths(tmp_path, monkeypatch):
    import subprocess
    root = build_ingest_vault(tmp_path / 'vault')
    for key, value in {'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
                       'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid',
                       'GIT_CONFIG_GLOBAL': str(tmp_path / 'gitconfig'), 'GIT_CONFIG_NOSYSTEM': '1'}.items():
        monkeypatch.setenv(key, value)

    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, check=True).stdout
    git('init', '-q')
    git('add', '-A')
    git('commit', '-q', '-m', 'start')
    (root / 'scratch.md').write_text('unrelated local edit\n', encoding='utf-8')
    config.activate(replace(config.current(), git_commit=True))
    _, result, _ = run_default(root, layouts.DEFAULT)
    assert result['status'] == 'applied' and result['commit']['status'] == 'committed'
    committed = set(git('-c', 'core.quotepath=false', 'show', '--name-only', '--no-renames', '--format=',
                        'HEAD').splitlines()) - {''}
    assert committed == set(result['applied_paths'])
    assert f'00_Inbox/{SOURCE_NAME}' in committed  # the archive move is part of the commit
    assert git('status', '--porcelain').strip() == '?? scratch.md'
    assert git('log', '-1', '--format=%s').startswith('secondbrain: ingest ingest-')


def test_an_unregistered_card_is_named_when_writing_is_refused(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    stray = root / '10_Knowledge' / DOMAIN / 'Stray note.md'
    stray.write_text(f'---\nid: card_stray\ntitle: Stray note\ndomain: {DOMAIN}\n---\nbody\n', encoding='utf-8')
    with pytest.raises(ValueError) as err:
        ingest.prepare(root, root / '00_Inbox' / SOURCE_NAME)
    message = str(err.value)
    assert 'unresolved problem' in message and ('Stray note.md' in message or 'card_stray' in message)
    assert not any('一' <= ch <= '鿿' for ch in message)


def test_home_counts_folders_and_cards_as_the_ingest_leaves_the_vault(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')  # default after_ingest = move
    config.activate(replace(config.current(), card_language='en'))
    _, result, _ = run_default(root, layouts.DEFAULT)
    assert result['status'] == 'applied' and result['inbox_moved']
    home = (root / 'HOME.md').read_text(encoding='utf-8')
    table = {d: int(n.replace(',', '')) for d, n in re.findall(r'^\| `([^`]+)/` \| ([\d,]+) \|$', home, re.M)}

    def on_disk(folder):
        base = root / folder
        return sum(1 for p in base.rglob('*') if p.is_file() and not any(
            x.startswith('.') for x in p.relative_to(base).parts)) if base.is_dir() else 0
    assert table and table == {d: on_disk(d) for d in table}
    assert table['00_Inbox'] == 0  # the source has moved to 40_Sources in the same transaction
    cards = int(re.search(r'\*\*cards: ([\d,]+)\*\*', home)[1].replace(',', ''))  # the inventory line
    assert cards == len(read_snapshot(root).cards) == table['10_Knowledge']



@pytest.mark.parametrize('moment, review_day, calendar_day', CLOCKS)
def test_ingest_dates_home_by_the_review_day_and_cards_by_the_calendar(tmp_path, monkeypatch, moment, review_day,
                                                                        calendar_day):
    from datetime import date
    from secondbrain import labels
    pin_clock(monkeypatch, moment)
    root = build_ingest_vault(tmp_path / 'vault')
    config.activate(replace(config.current(), card_language='en'))
    _, result, _ = run_default(root, layouts.DEFAULT)
    assert result['status'] == 'applied'
    home = (root / 'HOME.md').read_text(encoding='utf-8')
    assert f'Counted on {review_day}:' in home
    assert labels.pages('en')['review_missing'].format(date=review_day) in home
    snap = read_snapshot(root)
    assert {snap.cards[p['id']].fm['created'] for p in result['produced']} == {date.fromisoformat(calendar_day)}

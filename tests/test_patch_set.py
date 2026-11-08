"""A card that extends an existing card goes into it: the merge check's patch, disputes, split slots, conflicts.

The source is the invented two-chunk source of tests/test_invariants.py: in the first chunk the reviewer finds
lantern-queue extends the existing card and queue-order the same as it; filler-note (first chunk), lit-marker-cost
and arrival-order (last chunk) are distinct. Here the existing card is in sb format, so it can take a patch.
"""
from __future__ import annotations

import pytest

from secondbrain import cards as cardfmt
from secondbrain import decisions, inbox, jobs
from secondbrain import layout as layouts
from secondbrain.executors import FakeExecutor
from secondbrain.pipelines import ingest, source_check, split
from secondbrain.pipelines.ingest import content_digest
from secondbrain.vault import parse_note, read_snapshot
from tests.helpers import EXISTING
from tests.ingest_helpers import CARDS, keep_all, with_diagrams
from tests.test_ingest import settings
from tests.test_invariants import ARRIVAL, LEVELS, two_chunk_job, two_chunk_producer

LANTERN = CARDS['lantern-queue']


def sb_existing(root, n_props=2, n_flashcards=2):
    """Rewrite the existing card in sb format with ``n_props`` propositions; returns its bytes."""
    path = next((root / '10_Knowledge').rglob(f'{EXISTING}.md'))
    fm, _ = parse_note(path.read_bytes())
    head = path.read_bytes().decode('utf-8').split('\n---\n', 1)[0] + '\n---\n'
    props = '\n'.join(f'- P{i}. Old proposition {i} about routing readers.' for i in range(1, n_props + 1))
    cards_ = '\n'.join(f'Old question {i}?::Old answer {i}.' for i in range(1, n_flashcards + 1))
    body = (f'## 本质\n已有卡正文，保持逐字不动。\n\n## 命题\n{props}\n\n## 直觉与易混对比\n- 直觉：路灯。\n\n'
            f'## 相关\n暂无。\n\n## 复习卡\n{cards_}\n')
    path.write_bytes((head + body).encode('utf-8'))
    return path.read_bytes()


def reviewer(checks=None, *, check=None, rejudge=None, split_review=None, levels=LEVELS):
    """The chunk reviewer of the two-chunk source; the merge check, a dispute's judgement again and a split review
    answered by the given functions."""
    def respond(task):
        if source_check.is_slot(task.chunk):
            if checks is not None:
                checks.append(task)
            return (check or keep_all)(task.packet)
        if task.chunk.startswith('xr'):
            card = task.packet['card']
            return (rejudge or (lambda p: {'slug': card['slug'], 'overlap': 'distinct', 'with': [],
                                           'reason': 'it answers its own question'}))(task.packet)
        if split.is_slot(task.chunk.split('.')[0]):
            answer = (split_review or (lambda p: {'decision': 'accept', 'restates': False, 'reason': 'faithful'}))(
                task.packet)
            props = task.packet['card'].get('propositions') or []
            return {'matches': [{'proposition': f'P{i}', 'existing': None} for i in range(1, len(props) + 1)],
                    **answer}
        cand = task.packet['candidate']
        return {'cards': [{'slug': c['slug'], 'decision': 'accept', 'reason': 'ok',
                           **levels.get(c['slug'], {'overlap': 'distinct'})} for c in cand['cards']],
                'new_topics': [], 'coverage': {'decision': 'accept', 'reason': 'ok'}}
    return respond


SPLIT_CARD = {'slug': 'lantern-skip-ahead', 'title': 'Skipping ahead in a lantern queue', 'kind': 'mechanism',
              'essence': 'A reader of a lantern queue skips ahead by lit markers.',
              'propositions': ['Readers skip ahead by following lit markers.'],
              'intuition': None, 'confusions': [],
              'flashcards': [{'q': 'How does a reader skip ahead?', 'a': 'By jumping to a lit marker.'},
                             {'q': 'What does a reader follow to skip?', 'a': 'Lit markers.'}],
              'source_quotes': [LANTERN['source_quotes'][0]],
              'relations': [{'type': 'part_of', 'target': EXISTING, 'note': 'one case of routing readers'}]}


def producer(split_answer=None, seen=None):
    def respond(task):
        if split.is_slot(task.chunk.split('.')[0]):
            if seen is not None:
                seen.append(task)
            return split_answer(task) if split_answer else {'card': SPLIT_CARD}
        return two_chunk_producer(task)
    return with_diagrams(respond)


def run(root, job_id, review, produce=None):
    return ingest.advance(root, job_id, producer=FakeExecutor('gpt-test', responder=produce or producer()),
                          reviewer=FakeExecutor('claude-test', responder=review))


def job(tmp_path, n_props=2, **fields):
    root, job_id = two_chunk_job(tmp_path, **fields)
    raw = sb_existing(root, n_props)
    return root, job_id, raw


def card_of(root, cid):
    return read_snapshot(root).cards[cid]


def unchanged(root, before: bytes) -> bool:
    """The existing card took no patch: its body is as it was (back links of new cards only touch its relations)."""
    now = card_of(root, EXISTING)
    old_fm, old_body = parse_note(before)
    rest = {k: v for k, v in old_fm.items() if k != 'relations'}
    return cardfmt.sections(now.body) == {**cardfmt.sections(old_body), **{
        k: v for k, v in cardfmt.sections(now.body).items() if k == '相关'}} and \
        {k: v for k, v in now.fm.items() if k != 'relations'} == rest


# --- the patch ----------------------------------------------------------------------------------------------------
def test_an_extending_card_goes_into_its_existing_card_and_the_old_words_stay(tmp_path):
    root, job_id, before = job(tmp_path)
    checks = []
    done = run(root, job_id, reviewer(checks))
    assert done['status'] == 'applied', done.get('pending')
    [check] = checks
    [group] = check.packet['patch_set']
    assert group['target'] == EXISTING and [c['slug'] for c in group['from']] == ['lantern-queue']
    assert 'lantern-queue' not in {c['slug'] for c in check.packet['cards']}  # its level is the reviewer's
    assert done['outcomes']['lantern-queue'] == 'patch' and done['patch_set'] == {'lantern-queue': 'patched'}
    assert 'lantern-queue' not in {p['slug'] for p in done['produced']}
    old = card_of(root, EXISTING)
    props = cardfmt.propositions_of(old.body)
    assert props == [f'Old proposition {i} about routing readers.' for i in (1, 2)] + LANTERN['propositions']
    flash = cardfmt.flashcards_of(old.body)
    assert flash[:2] == cardfmt.flashcards_of(parse_note(before)[1]) and flash[2:] == LANTERN['flashcards']
    assert old.fm['sources'][-1]['propositions'] == ['P3', 'P4']
    assert '## 直觉与易混对比\n- 直觉：路灯。\n' in old.body and 'status' not in old.fm  # nothing else set
    [merged] = done['merged_into_existing']
    assert merged['id'] == EXISTING and merged['from'] == ['lantern-queue'] and merged['propositions'] == ['P3', 'P4']
    # arrival-order linked lantern-queue: the link goes to the card lantern-queue went into, as a program step
    arrival = next(p['id'] for p in done['produced'] if p['slug'] == 'arrival-order')
    assert {'type': 'part_of', 'target': EXISTING} == {k: v for k, v in card_of(root, arrival).fm['relations'][0].items()
                                                        if k in ('type', 'target')}
    ops = [(e['role'], e['op']) for e in decisions.chain(done['decision_record'], 'relations', 'arrival-order')]
    assert ('program', 'redirect') in ops


def test_a_proposition_the_existing_card_has_is_not_added_twice(tmp_path):
    root, job_id, _ = job(tmp_path)
    path = next((root / '10_Knowledge').rglob(f'{EXISTING}.md'))
    path.write_bytes(path.read_bytes().replace(b'- P2. Old proposition 2 about routing readers.',
                                               b'- P2. Items stay in arrival order.'))
    checks = []

    def check(packet):  # first everything (P2 again, word for word: sent back), then P2 named in already
        if len(checks) == 1:
            return keep_all(packet)
        return keep_all(packet, _patches={EXISTING: {'propositions': [LANTERN['propositions'][1]],
                                                     'flashcards': LANTERN['flashcards'],
                                                     'already': [{'text': LANTERN['propositions'][0], 'as': 'P2'}]}})
    done = run(root, job_id, reviewer(checks, check=check))
    props = cardfmt.propositions_of(card_of(root, EXISTING).body)
    assert props.count('Items stay in arrival order.') == 1 and props[-1] == LANTERN['propositions'][1]
    assert done['merged_into_existing'][0]['propositions'] == ['P3']
    assert "says what card_exist P2 says word for word" in done['source_check_refusals'][0]['message']


def test_the_merge_check_must_give_each_existing_card_one_patch_from_the_cards_that_extend_it(tmp_path):
    root, job_id, _ = job(tmp_path)
    checks = []

    def check(packet):
        checks.append(packet)
        if len(checks) == 1:
            return keep_all(packet, _patches={EXISTING: {'propositions': ['Something no card says.']}})
        return keep_all(packet)
    done = run(root, job_id, reviewer(check=check))
    assert done['status'] == 'applied' and len(checks) == 2 and done['outcomes']['lantern-queue'] == 'patch'
    assert 'copied exactly from the cards that extend' in jobs.load(root, job_id)['chunks']['xc01']['last_validation_error']
    with pytest.raises(ValueError, match='give card_exist its patch from lantern-queue, or dispute them'):
        source_check.validate_answer(checks[0], {**keep_all(checks[0]), 'patches': []})


@pytest.mark.parametrize('answer, problem', [
    ({'patches': [{'target': EXISTING, 'propositions': ['Items stay in arrival order.']}] * 2}, 'gets one patch'),
    ({'patches': [{'target': 'demo_other', 'propositions': ['x']}]}, 'existing card of the patch set'),
    ({'patches': [{'target': EXISTING}]}, 'an empty patch'),
    ({'patches': [{'target': EXISTING, 'replace': [{'proposition': 'P9', 'by': 'Items stay in arrival order.'}]}]},
     'P1–P2'),
    ({'patches': [], 'disputes': [{'slug': 'arrival-order', 'reason': 'r'}]}, 'a card of the patch set'),
    ({'patches': [], 'disputes': [{'slug': 'lantern-queue', 'reason': ' '}]}, 'say why'),
])
def test_a_patch_or_dispute_out_of_form_is_refused(tmp_path, answer, problem):
    root, job_id, _ = job(tmp_path)
    checks = []
    run(root, job_id, reviewer(checks, check=lambda p: {**keep_all(p), 'patches': [], 'disputes': []}))
    frozen = checks[0].packet
    with pytest.raises(ValueError, match=problem):
        source_check.validate_answer(frozen, {**keep_all(frozen), **answer})


# --- P10: a dispute sends the card back to its reviewer, and the merge check runs again as a whole -------------------
def test_p10_a_disputed_card_is_judged_again_and_the_whole_merge_check_round_is_replaced(tmp_path):
    root, job_id, before = job(tmp_path)
    checks = []

    def check(packet):
        return keep_all(packet, _disputes=('lantern-queue',)) if not checks[1:] else keep_all(packet)
    done = run(root, job_id, reviewer(checks, check=check))
    assert done['status'] == 'applied', done.get('pending')
    assert [c.chunk for c in checks] == ['xc01', 'xc01.r1']
    assert 'lantern-queue' in {c['slug'] for c in checks[1].packet['cards']}  # now in the decision set
    record = done['decision_record']
    assert decisions.current(record, 'overlap.existing', 'lantern-queue')['reason'] == 'dispute'
    assert {e['slot'] for e in decisions.values(record, 'overlap.source').values()} == {'xc01.r1'}
    assert decisions.values(record, 'dispute') == {}  # the whole earlier round, its dispute too, is replaced
    rounds = [e for e in record if e['kind'] == 'round']
    assert [(r['slot'], r['supersedes_slot'], r['reason']) for r in rounds] == [('xc01.r1', 'xc01', 'dispute')]
    assert done['outcomes']['lantern-queue'] == 'write' and unchanged(root, before)
    # every merge-check value of the first round is gone with it, not only the disputed card's
    first = [e for e in record if e['kind'] == 'value' and e['slot'] == 'xc01']
    assert first and all(e['n'] in decisions.superseded(record) for e in first)


def second_existing(root, cid='card_w'):
    """Another existing card in sb format (a copy of the first under another id and title), in the registry."""
    import json
    src = next((root / '10_Knowledge').rglob(f'{EXISTING}.md'))
    (src.parent / f'{cid}.md').write_text(src.read_text(encoding='utf-8').replace(f'id: {EXISTING}', f'id: {cid}')
                                          .replace('title: ', 'title: W ', 1), encoding='utf-8')
    reg = root / layouts.DEFAULT.registry
    data = json.loads(reg.read_text(encoding='utf-8'))
    first = next(n for n in data['notes'] if n['id'] == EXISTING)
    data['notes'].append({**first, 'id': cid, 'path': first['path'].replace(EXISTING, cid), 'title': 'W ' + first['title']})
    reg.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return cid


def test_p10_as_designed_the_other_patch_of_a_replaced_round_is_given_again_and_counts_once(tmp_path):
    """Design section 1.5, P10: D extends the existing card and is disputed; F extends another existing card W;
    E is distinct. Round 1 patches W and disputes D; D is judged again; round 2 judges D and E and patches W again.
    W's patch has one live entry, from round 2; F goes into W once; D, E and F each have one outcome."""
    root, job_id, _ = job(tmp_path)
    w = second_existing(root)

    def produce(task):
        out = two_chunk_producer(task)
        for c in out['cards']:
            if c['slug'] == 'lit-marker-cost':  # F
                c['relations'] = list(c['relations']) + [{'type': 'similar_to', 'target': w, 'note': 'n'}]
        return out
    levels = {**LEVELS, 'lit-marker-cost': {'overlap': 'extends', 'with': [w]}}
    checks = []

    def check(packet):
        return keep_all(packet, _disputes=('lantern-queue',)) if not checks[1:] else keep_all(packet)
    done = run(root, job_id, reviewer(checks, check=check, levels=levels), with_diagrams(produce))
    assert done['status'] == 'applied', done.get('pending')
    assert [c.chunk for c in checks] == ['xc01', 'xc01.r1']  # no round asked again: every record was accepted
    record = done['decision_record']
    patches = [(e['object'], e['slot'], e['n'] in decisions.superseded(record))
               for e in record if e['kind'] == 'value' and e['value'] == 'patch']
    assert patches == [(w, 'xc01', True), (w, 'xc01.r1', False)]
    assert {o: e['slot'] for o, e in decisions.values(record, 'patch').items()} == {w: 'xc01.r1'}
    assert done['outcomes'] == {'lantern-queue': 'write', 'queue-order': 'drop', 'filler-note': 'write',
                                'lit-marker-cost': 'patch', 'arrival-order': 'write'}
    assert done['patch_set'] == {'lit-marker-cost': 'patched'}
    assert [(m['id'], m['from']) for m in done['merged_into_existing']] == [(w, ['lit-marker-cost'])]
    added = CARDS['lit-marker-cost']['propositions'][0]
    assert cardfmt.propositions_of(card_of(root, w).body).count(added) == 1
    assert 'lit-marker-cost' not in {c.fm.get('slug') for c in read_snapshot(root).cards.values()}
    assert not [c for c in read_snapshot(root).cards.values() if c.title == CARDS['lit-marker-cost']['title']]


def test_a_card_still_disputed_after_the_last_round_stays_pending(tmp_path):
    root, job_id, before = job(tmp_path)
    again = {'lantern-queue': lambda p: {'slug': 'lantern-queue', 'overlap': 'extends', 'with': [EXISTING],
                                         'reason': 'it still only adds'}}
    done = run(root, job_id, reviewer(check=lambda p: keep_all(p, _disputes=('lantern-queue',)),
                                      rejudge=lambda p: again['lantern-queue'](p)))
    assert done['status'] == 'partial'
    assert [p['slug'] for p in done['pending']] == ['lantern-queue']
    assert 'could not add it to' in done['pending'][0]['reason'] and unchanged(root, before)


def test_a_judgement_again_must_name_existing_cards():
    frozen = {'card': {'slug': 'lantern-queue'}}
    with pytest.raises(ingest.IngestError, match='may name only existing'):
        ingest._validate_rejudge(frozen, {'slug': 'lantern-queue', 'overlap': 'extends', 'with': ['arrival-order'],
                                          'reason': 'r'}, {EXISTING})


# --- a merge-check card that repeats a card of the patch set ----------------------------------------------------------
def test_a_source_card_the_same_as_a_patch_set_card_goes_with_it_into_the_existing_card(tmp_path):
    root, job_id, _ = job(tmp_path)
    added = 'Lantern queues keep arrival order.'
    rows = {'arrival-order': {'overlap': 'extends', 'with': ['lantern-queue'], 'reason': 'both say what order holds',
                              'carry': {'propositions': [ARRIVAL['propositions'][0]]}}}

    def check(packet):
        return keep_all(packet, _patches={EXISTING: {'propositions': LANTERN['propositions'][:1]
                                                     + [ARRIVAL['propositions'][0]],
                                                     'already': [{'text': LANTERN['propositions'][1], 'as': 'P1'}]}},
                        **rows)
    done = run(root, job_id, reviewer(check=check))
    assert done['status'] == 'applied', done.get('pending')
    assert done['outcomes']['arrival-order'] == 'merge_carry'
    assert 'arrival-order' not in {p['slug'] for p in done['produced']}
    assert cardfmt.propositions_of(card_of(root, EXISTING).body)[-1] == ARRIVAL['propositions'][0] != added


# --- split slots -------------------------------------------------------------------------------------------------------
def test_an_existing_card_that_would_grow_past_twelve_propositions_is_left_and_a_narrower_card_written(tmp_path):
    root, job_id, before = job(tmp_path, n_props=11)
    seen = []
    done = run(root, job_id, reviewer(), producer(seen=seen))
    assert done['status'] == 'applied', done.get('pending')
    [task] = seen
    assert task.chunk == 'sp01' and task.packet['existing_card']['id'] == EXISTING
    assert unchanged(root, before)
    [made] = done['split']
    assert made['from'] == 'lantern-queue' and made['of'] == EXISTING and made['slot'] == 'sp01'
    card = card_of(root, made['id'])
    assert card.title == SPLIT_CARD['title'] and {'type': 'part_of', 'target': EXISTING} == {
        k: v for k, v in card.fm['relations'][0].items() if k in ('type', 'target')}
    record = done['decision_record']
    assert decisions.current(record, 'overlap.split', 'lantern-queue')['data'] == {
        'overlap': 'distinct', 'with': [EXISTING], 'card': SPLIT_CARD['slug']}
    assert decisions.current(record, 'split.verdict', 'lantern-queue')['role'] == 'split_reviewer'


def test_h7_a_patch_cut_down_to_the_limit_is_sent_back_and_the_program_splits_what_does_not_fit(tmp_path):
    """H7 (on f5b3024): the merge check cut a patch so the existing card ended at exactly 12 propositions, leaving
    out a new one, and the card was patched without a split. Now that answer is sent back (carry completeness,
    design section 4.1), and the whole patch, 13 propositions, goes to a split slot."""
    root, job_id, before = job(tmp_path, n_props=11)
    checks, seen = [], []

    def check(packet):
        if len(checks) == 1:  # the cut answer: only what still fits under 12
            return keep_all(packet, _patches={EXISTING: {'propositions': [LANTERN['propositions'][1]],
                                                         'flashcards': LANTERN['flashcards']}})
        return keep_all(packet)
    done = run(root, job_id, reviewer(checks, check=check), producer(seen=seen))
    assert done['status'] == 'applied', done.get('pending')
    error = jobs.load(root, job_id)['chunks']['xc01']['last_validation_error']
    assert f"leaves out {LANTERN['propositions'][0]!r}" in error and 'program splits' in error
    assert len(checks) == 2 and [t.chunk for t in seen] == ['sp01']
    assert unchanged(root, before) and done['split'][0]['from'] == 'lantern-queue'
    assert len(cardfmt.propositions_of(card_of(root, EXISTING).body)) == 11


def test_a_proposition_the_existing_card_already_says_is_named_not_carried(tmp_path):
    root, job_id, _ = job(tmp_path)
    checks = []
    run(root, job_id, reviewer(checks, check=lambda p: {**keep_all(p), 'patches': [], 'disputes': []}))
    frozen = checks[0].packet
    said = {'target': EXISTING, 'propositions': [LANTERN['propositions'][1]]}
    for already, problem in (([{'text': LANTERN['propositions'][0], 'as': 'P2'}], None),
                             ([{'text': LANTERN['propositions'][0], 'as': 'P9'}], r'P1–P2'),
                             ([{'text': 'Something else.', 'as': 'P1'}], 'copied exactly'),
                             ([], 'leaves out')):
        answer = {**keep_all(frozen), 'patches': [{**said, 'already': already}]}
        if problem is None:
            assert source_check.validate_answer(frozen, answer) is answer
        else:
            with pytest.raises(ValueError, match=problem):
                source_check.validate_answer(frozen, answer)


SPLIT_ORDER = {'slug': 'arrival-order-under-markers', 'title': 'Arrival order under lit markers', 'kind': 'mechanism',
               'essence': 'A lantern queue keeps arrival order even while readers skip by markers.',
               'propositions': ['Skipping by a marker does not change the order of the items.'],
               'intuition': None, 'confusions': [],
               'flashcards': [{'q': 'Does skipping change the order?', 'a': 'No.'},
                              {'q': 'What order holds while readers skip?', 'a': 'Arrival order.'}],
               'source_quotes': [ARRIVAL['source_quotes'][0]],
               'relations': [{'type': 'part_of', 'target': 'lit-marker-cost', 'note': 'one case of it'}]}


def test_d15_a_card_of_the_source_too_large_to_merge_into_goes_to_a_split_slot(tmp_path):
    """Decision D-15: arrival-order extends lit-marker-cost (12 propositions), carrying 1: past the limit the program
    leaves lit-marker-cost as it is and writes arrival-order as a narrower card through a split slot; the merge
    check is not sent back and not asked to judge it distinct."""
    root, job_id, _ = job(tmp_path)
    pad = [f'Lit marker fact {i} holds.' for i in range(1, 11)]
    checks, seen = [], []

    def produce(task):
        if split.is_slot(task.chunk.split('.')[0]):
            seen.append(task)
            return {'card': SPLIT_ORDER}
        out = two_chunk_producer(task)
        for c in out['cards']:
            if c['slug'] == 'lit-marker-cost':
                c['propositions'] = list(c['propositions']) + pad
        return out

    def check(packet):
        return keep_all(packet, **{'arrival-order': {'overlap': 'extends', 'with': 'lit-marker-cost', 'reason': 'r',
                                                     'carry': {'propositions': list(ARRIVAL['propositions'])}}})
    done = run(root, job_id, reviewer(checks, check=check), with_diagrams(produce))
    assert done['status'] == 'applied', done.get('pending')
    assert len(checks) == 1 and done['source_check_refusals'] == []
    assert [t.chunk for t in seen] == ['sp01']
    shown = seen[0].packet['existing_card']
    assert shown['id'] == 'lit-marker-cost' and len(shown['propositions']) == 12
    assert done['merge_set'] == {'arrival-order': 'split'} and done['outcomes']['arrival-order'] == 'merge_carry'
    cards_ = {c.title: c for c in read_snapshot(root).cards.values()}
    lit = cards_[CARDS['lit-marker-cost']['title']]
    assert len(cardfmt.propositions_of(lit.body)) == 12                       # left as it was
    assert ARRIVAL['title'] not in cards_                                      # not written as it was
    made = cards_[SPLIT_ORDER['title']]                                        # but as the narrower card
    assert {'type': 'part_of', 'target': lit.id} in [{k: r[k] for k in ('type', 'target')} for r in made.fm['relations']]
    [entry] = done['split']
    assert entry['from'] == 'arrival-order' and entry['of'] == lit.id


def test_when_the_merge_check_is_skipped_the_outcome_of_a_patch_set_card_follows_where_it_went(tmp_path):
    """Second verdict, not blocking 4: the merge check failed validation every time and was skipped, so no patch
    was composed and lantern-queue was written next to its existing card; the outcome says write, as patch_set."""
    root, job_id, before = job(tmp_path)
    done = run(root, job_id, reviewer(check=lambda p: {'cards': [], 'new_topics': []}))
    assert done['source_check']['status'] == 'skipped'
    assert done['patch_set'] == {'lantern-queue': 'written'} and done['outcomes']['lantern-queue'] == 'write'
    assert unchanged(root, before) and len(done['source_check_refusals']) == 3


def test_a_split_card_the_reviewer_rejects_is_rewritten_in_each_round_then_stays_pending(tmp_path):
    root, job_id, before = job(tmp_path, n_props=11)
    seen = []
    done = run(root, job_id, reviewer(split_review=lambda p: {'decision': 'revise', 'restates': True,
                                                              'reason': 'it says what P2 says'}),
               producer(seen=seen))
    assert [t.chunk for t in seen] == ['sp01', 'sp01.r1', 'sp01.r2']  # max_repair_rounds = 2
    assert seen[1].packet['review_findings']['reason'] == 'it says what P2 says'
    assert done['status'] == 'partial' and [p['slug'] for p in done['pending']] == ['lantern-queue']
    assert unchanged(root, before)


@pytest.mark.parametrize('change, problem', [
    ({'propositions': ['Old proposition 3 about routing readers!']}, 'restate the propositions'),
    ({'relations': [{'type': 'similar_to', 'target': EXISTING}]}, 'must link card_exist with part_of'),
    ({'source_quotes': ['words the chunk does not have']}, 'source_quotes'),
    ({'slug': 'lantern-queue-x', 'flashcards': [{'q': 'a::b', 'a': 'c'}] * 2}, 'without "::"'),
])
def test_a_split_card_that_restates_or_does_not_narrow_is_refused(change, problem):
    item = {'with': EXISTING, 'existing_propositions': [f'Old proposition {i} about routing readers.' for i in (1, 2, 3)]}
    payload = {'card': {**SPLIT_CARD, **change}}
    with pytest.raises(ValueError, match=problem):
        split.validate_card(item, payload, text=LANTERN['source_quotes'][0] + ' and more', taken={'lantern-queue'},
                            catalog_types={'part_of', 'similar_to', 'depends_on'})


def test_a_split_review_that_accepts_a_restating_card_is_refused():
    with pytest.raises(ValueError, match='restates'):
        split.validate_review({'decision': 'accept', 'restates': True, 'reason': 'r'})


# --- conflicts and the switch ----------------------------------------------------------------------------------------
def test_a_conflict_open_on_an_existing_card_stays_open_after_the_card_takes_a_patch(tmp_path):
    root, job_id, _ = job(tmp_path)
    lay = layouts.of(root)
    snapshot = read_snapshot(root)
    row = {'with': EXISTING, 'with_title': snapshot.cards[EXISTING].title, 'card': 'demo_earlier', 'slug': 'earlier',
           'title': 'Earlier card', 'detail': 'they disagree on the order', 'by': 'reviewer', 'job_id': 'ingest-0',
           'at': '2026-01-01T00:00:00Z', 'source_sha256': 'abc', 'with_digest': content_digest(snapshot.cards[EXISTING])}
    path = root / lay.conflicts
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(inbox.registry_line(row))
    assert len(ingest.open_conflicts(root, snapshot)) == 1
    done = run(root, job_id, reviewer())
    after = read_snapshot(root)
    assert content_digest(after.cards[EXISTING]) != row['with_digest']  # the patch changed it
    [still] = ingest.open_conflicts(root, after)
    assert still['detail'] == row['detail'] and still['card'] == 'demo_earlier'
    assert done['status'] == 'applied'


def test_with_merging_off_an_extending_card_is_written_next_to_its_existing_card(tmp_path):
    root, job_id = two_chunk_job(tmp_path, merge_into_existing=False)
    before = sb_existing(root)
    checks = []
    done = run(root, job_id, reviewer(checks))
    assert done['status'] == 'applied', done.get('pending')
    assert all(not c.packet.get('patch_set') for c in checks)
    assert unchanged(root, before) and done['patch_set'] == {'lantern-queue': 'written'}
    [review] = [n for n in done['needs_review'] if n['title'] == LANTERN['title']]
    assert review['with'] == EXISTING and 'merge_into_existing is off' in review['not_merged']
    assert jobs.read_input(root, job_id)['settings']['merge_into_existing'] is False


def test_an_existing_card_out_of_sb_format_cannot_take_a_patch_and_the_card_is_written_next_to_it(tmp_path):
    root, job_id = two_chunk_job(tmp_path)  # the test vault's existing card has no propositions section
    checks = []
    done = run(root, job_id, reviewer(checks))
    assert all(not c.packet.get('patch_set') for c in checks)
    [review] = [n for n in done['needs_review'] if n['title'] == LANTERN['title']]
    assert 'cannot take a patch: it has no propositions section' in review['not_merged']

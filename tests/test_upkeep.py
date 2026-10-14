"""What the owner must see and may clear: cards waiting as needs-review, and old rollback journals."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from secondbrain import cli, jobs, labels, projections
from secondbrain.pipelines import ingest
from secondbrain import layout as layouts
from secondbrain.service import Service
from secondbrain.store import Conflict, state_root
from secondbrain.vault import read_snapshot
from tests.helpers import DOMAIN, EXISTING, PILLAR, TOPIC, build_vault
from tests.test_page_language import CJK, DAY, use
from tests.test_retry import apply_retry, partial_job


def waiting_card(root, title='Unsure card'):
    """A card the owner has not confirmed yet."""
    lay = layouts.DEFAULT
    rel = lay.card_path(DOMAIN, f'{title}.md')
    (root / rel).write_text(f'---\nid: kc_unsure\ntitle: {title}\ndomain: {DOMAIN}\npillar: {PILLAR}\n'
                            f'topic: {TOPIC}\nstatus: needs-review\n---\n## Essence\nNot sure yet.\n',
                            encoding='utf-8')
    registry = json.loads((root / lay.registry).read_text(encoding='utf-8'))
    registry['notes'].append({'id': 'kc_unsure', 'path': rel, 'title': title, 'domain': DOMAIN})
    (root / lay.registry).write_text(json.dumps(registry), encoding='utf-8')
    return rel


# --- needs-review cards ----------------------------------------------------------------------------
@pytest.mark.parametrize('language', ['zh', 'en'])
def test_home_counts_needs_review_cards_and_says_where_they_are_listed(tmp_path, language):
    use(language)
    root = build_vault(tmp_path / 'vault')
    line = labels.pages(language)['needs_review']
    before = projections.entry_projection(read_snapshot(root), DAY).files['HOME.md'].decode('utf-8')
    assert line.split('**')[0] not in before  # nothing waits: no line
    waiting_card(root)
    home = projections.entry_projection(read_snapshot(root), DAY).files['HOME.md'].decode('utf-8')
    assert line.format(i=4, n=1) in home
    assert '`[status:needs-review]`' in home and '`sb status --needs-review`' in home
    cli.parser().parse_args(['status', '--needs-review'])  # the command HOME names exists
    if language == 'en':
        assert not CJK.search(line)


def conflict_row(root, card_id=EXISTING):
    """An open conflict with ``card_id``, as an ingest records it."""
    card = read_snapshot(root).cards[card_id]
    path = root / layouts.DEFAULT.conflicts
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'card': 'kc_new', 'slug': 'new', 'title': 'New', 'with': card_id, 'detail': 'd',
                                'by': 'reviewer', 'with_digest': ingest.content_digest(card)}) + '\n',
                    encoding='utf-8')


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_home_counts_open_conflicts_after_needs_review_cards(tmp_path, language):
    use(language)
    root = build_vault(tmp_path / 'vault')
    T = labels.pages(language)

    def home():
        return projections.entry_projection(read_snapshot(root), DAY).files['HOME.md'].decode('utf-8')
    assert T['conflicts'].split('**')[0][3:] not in home()
    conflict_row(root)
    assert T['conflicts'].format(i=4, n=1) in home()
    waiting_card(root)
    assert T['needs_review'].format(i=4, n=1) in home() and T['conflicts'].format(i=5, n=1) in home()
    assert '`sb status`' in T['conflicts']
    if language == 'en':
        assert not CJK.search(T['conflicts'])


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_the_entry_block_says_the_text_around_it_is_hand_written(language):
    # drill 3: the refreshed block lost the note that the kb commands below it belong to the old system
    note = projections.entry_block(language)[-2]
    assert ('手写' in note and '旧系统' in note) if language == 'zh' else ('by hand' in note and 'older system' in note)


def test_status_counts_needs_review_cards_and_lists_them_on_request(tmp_path, capsys):
    root = build_vault(tmp_path / 'vault')
    assert Service(root).status()['needs_review'] == 0
    rel = waiting_card(root)
    status = Service(root).status()
    assert status['needs_review'] == 1 and '--needs-review' in status['needs_review_hint']
    assert cli.main(['--vault', str(root), 'status', '--needs-review']) == 0
    assert json.loads(capsys.readouterr().out) == [{'id': 'kc_unsure', 'title': 'Unsure card', 'path': rel}]


# --- pruning rollback journals ------------------------------------------------------------------
def journal_files(root, job_id):
    return {name for name in jobs.PRUNABLE_FILES if (state_root(root) / jobs.location(job_id, name)).exists()}


def two_writes(tmp_path):
    """A partial parent and its applied retry child: two writes of one source."""
    from tests.ingest_helpers import build_ingest_vault
    root = build_ingest_vault(tmp_path / 'vault')
    parent = partial_job(root)
    child = apply_retry(root, parent)['job_id']
    return root, parent, child


def test_prune_previews_old_writes_but_keeps_the_newest_of_a_source(tmp_path, capsys):
    root, parent, child = two_writes(tmp_path)
    assert jobs.prune(root)['jobs'] == []  # both are minutes old; the default is 30 days
    later = datetime.now(timezone.utc) + timedelta(days=31)
    preview = jobs.prune(root, now_utc=later)
    assert [j['job_id'] for j in preview['jobs']] == [parent]  # the child is the newest write of the source
    assert preview['jobs'][0]['files'] == ['commit-intent.json', 'transaction.json'] and preview['mb'] > 0
    assert child in preview['kept_newest'] and 'can then no longer be undone' in preview['hint']
    assert journal_files(root, parent) == set(jobs.PRUNABLE_FILES)  # a preview deletes nothing
    assert cli.main(['--vault', str(root), 'job', 'prune', '--days', '0']) == 0
    assert [j['job_id'] for j in json.loads(capsys.readouterr().out)['jobs']] == [parent]


def test_a_pruned_write_says_why_it_cannot_be_rolled_back(tmp_path, capsys):
    root, parent, child = two_writes(tmp_path)
    assert cli.main(['--vault', str(root), 'job', 'prune', '--days', '0', '--apply']) == 0
    done = json.loads(capsys.readouterr().out)
    assert [j['job_id'] for j in done['jobs']] == [parent] and journal_files(root, parent) == set()
    state = jobs.load(root, parent)
    assert state['pruned']['bytes'] > 0 and 'commit_intent' not in state and state['status'] == 'partial'
    assert cli.main(['--vault', str(root), 'recover', parent, '--rollback']) == 2
    error = json.loads(capsys.readouterr().err)['error']
    assert f'The rollback journal of {parent} was pruned' in error and 'sb job prune' in error
    with pytest.raises(Conflict, match='journal was pruned'):
        jobs.archive(root, parent, reason='try again')
    assert jobs.load(root, parent)['status'] == 'partial'  # advance and list still read it
    assert cli.main(['--vault', str(root), 'job', 'list']) == 0
    capsys.readouterr()
    # the newest write keeps its journal and still rolls back
    assert journal_files(root, child) == set(jobs.PRUNABLE_FILES)
    assert cli.main(['--vault', str(root), 'recover', child, '--rollback']) == 0
    assert jobs.prune(root, days=0, apply=True)['jobs'] == []  # nothing left that may go


def test_status_shows_the_state_folder_and_its_size(tmp_path):
    root, _, _ = two_writes(tmp_path)
    state = Service(root).status()['state_dir']
    assert state['path'] == str(state_root(root)) and state['mb'] > 0 and 'sb job prune' in state['hint']
    assert set(state['parts']) >= {'cache', 'jobs'} and state['prunable_mb'] == 0


def test_the_state_hint_names_the_part_that_is_large(tmp_path, monkeypatch):
    # drill 3: the hint blamed rollback journals while the cache was 99 of 181 MB and prune found nothing
    root, parent, _ = two_writes(tmp_path)
    (state_root(root) / 'cache' / 'search.sqlite').write_bytes(b'x' * 3_000_000)
    state = Service(root).status()['state_dir']
    assert state['hint'].startswith('most of it is the parse cache and search index (cache/, 3.')
    assert 'finds no rollback journal older than 30 days' in state['hint']
    (state_root(root) / jobs.location(parent, 'packets', 'big.md')).write_bytes(b'x' * 6_000_000)
    monkeypatch.setattr(jobs, 'prune', lambda root: {'mb': 1.25})
    state = Service(root).status()['state_dir']
    assert state['hint'].startswith('most of it is job records (jobs/, 6.')
    assert state['hint'].endswith('sb job prune --apply frees 1.25 MB of rollback journals older than 30 days')

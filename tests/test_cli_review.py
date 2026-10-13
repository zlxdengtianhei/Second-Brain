"""`sb review status|queue|log|import-sr` and the `sb diagrams` pass-through."""
from __future__ import annotations

import json

import pytest

pytest.importorskip('fsrs')

from secondbrain import cli  # noqa: E402
from secondbrain import layout as layouts  # noqa: E402
from secondbrain.review_registry import question_id  # noqa: E402
from secondbrain.vault import read_snapshot  # noqa: E402
from tests.ingest_helpers import build_ingest_vault  # noqa: E402
from tests.test_ingest import run_default  # noqa: E402

NOW = '2026-10-13T09:00:00+02:00'


def run(capsys, root, *argv):
    code = cli.main(['--vault', str(root), *argv])
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip() else None), err


@pytest.fixture
def vault(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    run_default(root, layouts.DEFAULT)
    return root


def test_status_queue_and_an_agent_review(vault, capsys):
    snap = read_snapshot(vault)
    code, status, err = run(capsys, vault, 'review', 'status', '--now', NOW)
    assert code == 0, err
    assert status['totals']['total'] == len(snap.cards) == status['totals']['unlearned']
    code, queue, _ = run(capsys, vault, 'review', 'queue', '--now', NOW, '--questions', '--new', '3')
    assert code == 0 and queue['new_count'] == 3 and queue['due_count'] == 0 and len(queue['cards']) == 3
    code, queue, _ = run(capsys, vault, 'review', 'queue', '--now', NOW, '--questions')
    titles = [c['title'] for c in queue['cards']]
    assert len(titles) == len(snap.cards)
    assert titles.index('Lantern queue') < titles.index('Lit marker memory cost')  # depends_on order
    lantern = queue['cards'][titles.index('Lantern queue')]
    assert [q['question'] for q in lantern['questions']] == [
        'What order does a lantern queue keep?', 'How does a reader skip ahead?']
    card_id = lantern['id']
    code, logged, err = run(capsys, vault, 'review', 'log', '--card', card_id, '--now', NOW, '--device', 'cli-test',
                            '--item', 'What order does a lantern queue keep?=3:hit',
                            '--item', f'{question_id("How does a reader skip ahead?")}=2:partial',
                            '--grader', 'example-model')
    assert code == 0, err
    assert logged['event']['rating'] == 2 and logged['state']['state'] == 'learning'
    log_file = vault / '_data/review/events-cli-test.jsonl'
    assert logged['written'] == str(log_file)
    [line] = log_file.read_text(encoding='utf-8').splitlines()
    assert json.loads(line)['items'][0] == {'q': question_id('What order does a lantern queue keep?'),
                                            'rating': 3, 'verdict': 'hit'}
    code, status, _ = run(capsys, vault, 'review', 'status', '--now', NOW)
    assert status['totals']['learning'] == 1 and status['reviews_today'] == 1
    code, overview, _ = run(capsys, vault, 'status')
    assert overview['review']['total'] == len(snap.cards) and overview['inbox'] == {'done': 1}  # moved, listed
    code, _, err = run(capsys, vault, 'review', 'log', '--card', 'kc_missing', '--rating', '3', '--now', NOW)
    assert code == 2 and 'No card' in err
    code, _, err = run(capsys, vault, 'review', 'log', '--card', card_id, '--item', 'oops', '--now', NOW)
    assert code == 2 and '--item must look like' in err


def test_queue_order_matches_the_registry_with_taxonomy(vault, capsys):
    from secondbrain import review_registry, reviews
    from secondbrain.store import state_root
    now = reviews.now_local(NOW)
    snap = read_snapshot(vault)
    expected = review_registry.queue_report(
        reviews.review_dir(vault), reviews.card_metas(snap), now, new_per_day=10,
        taxonomy=review_registry.build_taxonomy(
            [(f'20_MOCs/_pillars/{moc}.yaml', data) for moc, data in snap.taxonomies.items()]))
    code, queue, err = run(capsys, vault, 'review', 'queue', '--now', NOW)
    assert code == 0, err
    assert [c['id'] for c in queue['cards']] == [c['id'] for c in expected['cards']]
    assert len(queue['cards']) == len(snap.cards)
    # the replay cache lives in the state directory, next to the job state, never in the vault
    cache = reviews.cache_dir(vault)
    assert cache == state_root(vault).parent / 'review-cache' and cache.is_dir()
    assert not any('review-state' in p.name for p in vault.rglob('*'))


def test_the_daily_review_page_shows_the_review_queue(vault, capsys):
    import re
    from datetime import date, timedelta
    from secondbrain.projections import review_time
    day = date(2026, 10, 13)
    noon = review_time(day)
    code, queue, err = run(capsys, vault, 'review', 'queue', '--now', noon.isoformat())
    assert code == 0, err
    studied = queue['cards'][1]['id']
    code, _, err = run(capsys, vault, 'review', 'log', '--card', studied, '--rating', '1', '--device', 'cli-test',
                       '--now', (noon - timedelta(days=2)).isoformat())
    assert code == 0, err
    code, queue, _ = run(capsys, vault, 'review', 'queue', '--now', noon.isoformat())
    assert queue['due_count'] == 1 and queue['cards'][0]['id'] == studied and queue['new_count'] >= 2
    code, result, err = run(capsys, vault, 'daily', '--date', day.isoformat(), '--apply')
    assert code == 0 and result['status'] == 'applied', err
    page = (vault / '50_Reviews/Daily review 2026-10-13.md').read_text(encoding='utf-8')
    listed = re.findall(r'^- \[\[([^|\]]+)\|', page, re.M)
    assert listed == [c['path'][:-3] for c in queue['cards']]  # same cards, same order
    assert f"New cards not started yet: {queue['new_available']}; {queue['new_count']} of them" in page
    assert '[[50_Reviews/Daily review 2026-10-13|Open the review page of 2026-10-13]]' in (vault / 'HOME.md').read_text(
        encoding='utf-8')  # written in the same transaction, so HOME links it


def sb_commands(text):
    import re
    import shlex
    return [shlex.split(c)[1:] for c in re.findall(r'`(sb [^`]+)`', text)]


def test_the_command_homes_hint_names_makes_the_page_and_links_it(vault, capsys):
    from secondbrain import labels, projections, reviews
    day = reviews.learning_date()
    page = projections.review_page(layouts.of(vault), day)
    assert not (vault / page).exists()
    code, _, err = run(capsys, vault, 'docs', '--apply')
    assert code == 0, err
    home = (vault / 'HOME.md').read_text(encoding='utf-8')
    hint = labels.pages('en')['review_missing'].format(date=day.isoformat())
    assert hint in home and f'[[{page[:-3]}|' not in home
    [command] = sb_commands(hint)  # do what HOME says
    code, _, err = run(capsys, vault, *command)
    assert code == 0, err
    home = (vault / 'HOME.md').read_text(encoding='utf-8')
    assert (vault / page).is_file() and f'[[{page[:-3]}|Open the review page of {day}]]' in home and hint not in home


def test_a_page_made_after_midnight_is_the_review_day_of_sb_review_queue(vault, capsys, monkeypatch):
    import re
    from datetime import datetime
    from secondbrain import reviews
    night = datetime.fromisoformat('2026-10-14T01:00:00+02:00')  # before the 04:00 start of the next review day
    real = reviews.now_local
    monkeypatch.setattr(reviews, 'now_local', lambda value=None: night if value is None else real(value))
    code, queue, err = run(capsys, vault, 'review', 'queue', '--now', night.isoformat())
    assert code == 0 and queue['today'] == '2026-10-13', err
    studied = queue['cards'][0]['id']
    run(capsys, vault, 'review', 'log', '--card', studied, '--rating', '1', '--device', 'cli-test',
        '--now', '2026-10-13T21:00:00+02:00')
    code, queue, _ = run(capsys, vault, 'review', 'queue', '--now', night.isoformat())
    code, result, err = run(capsys, vault, 'daily', '--apply')  # no --date: the current review day
    assert code == 0 and result['date'] == '2026-10-13', err
    page = (vault / '50_Reviews/Daily review 2026-10-13.md').read_text(encoding='utf-8')
    listed = re.findall(r'^- \[\[([^|\]]+)\|', page, re.M)
    assert listed == [c['path'][:-3] for c in queue['cards']]
    assert 'review day 2026-10-13 (days start at 04:00)' in page


def test_without_the_review_extra_daily_writes_home_and_today_explains(vault, capsys, monkeypatch):
    from secondbrain import reviews

    def missing():
        raise reviews.ReviewExtraMissing('Review commands need the review extra: pip install "secondbrain[review]"')
    monkeypatch.setattr(reviews, '_registry', missing)
    code, _, err = run(capsys, vault, 'today', '--apply')
    assert code == 2 and 'secondbrain[review]' in err
    (vault / 'HOME.md').write_text('# old home\n', encoding='utf-8')  # the ingest left HOME current
    code, result, err = run(capsys, vault, 'daily', '--apply')
    assert code == 0, err
    assert 'HOME.md' in result['applied_paths'] and not any('50_Reviews' in p for p in result['applied_paths'])
    assert [i['code'] for i in result['issues']] == ['review_extra_missing']


def test_import_spaced_repetition_schedules(vault, capsys):
    snap = read_snapshot(vault)
    card = next(c for c in snap.cards.values() if c.title == 'Lantern queue')
    path = vault / card.path
    text = path.read_text(encoding='utf-8')
    path.write_text(text.replace('Arrival order.', 'Arrival order.\n<!--SR:!2026-10-20,7,250-->', 1), encoding='utf-8')
    code, plan, err = run(capsys, vault, 'review', 'import-sr', '--dry-run', '--now', NOW)
    assert code == 0, err
    assert plan['found'] == 1 and plan['imported'] == 0 and plan['written'] is None
    code, done, _ = run(capsys, vault, 'review', 'import-sr', '--device', 'cli-test', '--now', NOW)
    assert done['imported'] == 1
    code, again, _ = run(capsys, vault, 'review', 'import-sr', '--device', 'cli-test', '--now', NOW)
    assert again['found'] == 0 and again['skipped'] == 1
    assert path.read_text(encoding='utf-8').count('<!--SR:') == 1  # card files are not changed


def test_diagrams_pass_through(capsys):
    assert cli.main(['diagrams', 'schema']) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema['title'] == 'Card diagram IR'
    with pytest.raises(SystemExit) as stop:
        cli.main(['diagrams', '--help'])
    assert stop.value.code == 0 and 'audit-extract' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(['status', '--bogus'])

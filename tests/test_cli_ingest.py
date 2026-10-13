"""`sb inbox`, `sb ingest`, `sb job packet|submit|advance` and `sb doctor` from the command line."""
from __future__ import annotations

import json

from secondbrain import cli, jobs
from tests.ingest_helpers import (ALL_REASONS, SOURCE_NAME, build_ingest_vault, first_candidate, first_review,
                                  repair_candidate, repair_review)


def run(capsys, root, *argv):
    code = cli.main(['--vault', str(root), *argv])
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip() else None), err


def test_inbox_commands(tmp_path, capsys):
    root = build_ingest_vault(tmp_path / 'vault')
    (root / '00_Inbox' / 'noise.md').write_text('line one\nline two\n', encoding='utf-8')
    code, items, _ = run(capsys, root, 'inbox', 'list')
    assert code == 0 and {i['name']: i['status'] for i in items} == {'noise.md': 'new', SOURCE_NAME: 'new'}
    code, item, _ = run(capsys, root, 'inbox', 'show', 'noise.md', '--head', '1')
    assert code == 0 and item['head'] == 'line one'
    code, skipped, _ = run(capsys, root, 'inbox', 'skip', item['sha256'][:12], '--reason', 'not knowledge')
    assert code == 0 and skipped['status'] == 'skipped'
    code, _, err = run(capsys, root, 'inbox', 'show', 'missing.md')
    assert code == 2 and 'No unique inbox item' in err


def test_host_flow_through_the_cli(tmp_path, capsys):
    root = build_ingest_vault(tmp_path / 'vault')
    code, prepared, _ = run(capsys, root, 'ingest', '--all', '--prepare-only')
    assert code == 0 and prepared[0]['status'] == 'prepared' and prepared[0]['chunks'] == ['c01']
    job_id = prepared[0]['job_id']
    code, _, err = run(capsys, root, 'ingest', '--all')
    assert code == 2 and 'executor.producer' in err  # API mode without endpoints
    code, waiting, _ = run(capsys, root, 'ingest', '--all', '--executor', 'host')
    assert code == 0 and waiting[0]['status'] == 'awaiting_submission'

    def answer(handoff, payload, model):
        path = tmp_path / f"{handoff['run_id']}.json"
        path.write_text(json.dumps(payload), encoding='utf-8')
        assert handoff['submit_cmd'].startswith(f'sb --vault {root} job submit {job_id}')
        code, submitted, err = run(capsys, root, 'job', 'submit', job_id, '--role', handoff['role'],
                                   '--run-id', handoff['run_id'], '--file', str(path), '--model', model)
        assert code == 0, err
        return submitted

    answer(waiting[0]['handoffs'][0], first_candidate(), 'gpt-test')
    code, _, err = run(capsys, root, 'job', 'packet', job_id, '--role', 'producer')
    assert code == 2 and 'No producer step is due' in err
    code, review, _ = run(capsys, root, 'job', 'packet', job_id, '--role', 'reviewer', '--chunk', 'c01')
    assert code == 0 and review['step'] == 'review'
    packet_text = open(review['packet_path'], encoding='utf-8').read()
    assert not any(reason in packet_text for reason in ALL_REASONS)
    answer(review, first_review(), 'claude-test')
    code, state, _ = run(capsys, root, 'job', 'advance', job_id, '--executor', 'host')
    repair = state['handoffs'][0]
    assert repair['step'] == 'repair'
    answer(repair, repair_candidate(), 'gpt-test')
    code, state, _ = run(capsys, root, 'job', 'advance', job_id, '--executor', 'host')
    answer(state['handoffs'][0], repair_review(), 'claude-test')
    code, state, _ = run(capsys, root, 'job', 'advance', job_id, '--executor', 'host', '--proposal-only')
    assert code == 0 and state['status'] == 'reviewed' and len(state['proposal']['accepted']) == 3
    code, state, _ = run(capsys, root, 'job', 'advance', job_id, '--executor', 'host')
    assert code == 0 and state['status'] == 'applied' and state['new_card_count'] == 3
    code, shown, _ = run(capsys, root, 'job', 'show', job_id)
    assert len(shown['run_records']) == 4 and shown['same_family'] is not True
    code, items, _ = run(capsys, root, 'inbox', 'list')
    assert items == []


def test_job_abandon_lets_a_new_run_start(tmp_path, capsys):
    root = build_ingest_vault(tmp_path / 'vault')
    code, waiting, _ = run(capsys, root, 'ingest', str(root / '00_Inbox' / SOURCE_NAME), '--executor', 'host')
    handoff = waiting[0]['handoffs'][0]
    job_id = waiting[0]['job_id']
    code, abandoned, _ = run(capsys, root, 'job', 'abandon', job_id, '--run-id', handoff['run_id'],
                             '--reason', 'sub-agent crashed')
    assert code == 0 and abandoned['status'] == 'abandoned'
    code, again, _ = run(capsys, root, 'job', 'packet', job_id, '--role', 'producer')
    assert code == 0 and again['run_id'] != handoff['run_id']
    assert {r['status'] for r in jobs.list_runs(root, job_id)} == {'abandoned', 'started'}


def test_ingest_reports_bad_files_without_stopping(tmp_path, capsys):
    root = build_ingest_vault(tmp_path / 'vault')
    (root / '00_Inbox' / 'scan.pdf').write_bytes(b'%PDF\xff\xfe')
    code, results, _ = run(capsys, root, 'ingest', '--all', '--prepare-only')
    assert code == 0
    by_status = {r['status']: r for r in results}
    assert 'converter' in by_status['failed']['error'] and by_status['prepared']['chunks'] == ['c01']


def test_doctor_reports_without_network(tmp_path, capsys, monkeypatch):
    root = build_ingest_vault(tmp_path / 'vault')
    cfg = tmp_path / 'sb.toml'
    cfg.write_text(f'''vault = "{root}"
[executor.producer]
kind = "openai"
base_url = "https://llm.example.test/v1"
model = "gpt-test"
api_key_env = "SB_DOCTOR_KEY"
[executor.reviewer]
kind = "anthropic"
base_url = "https://anthropic.example.test"
model = "claude-test"
api_key_env = "SB_DOCTOR_KEY_MISSING"
''', encoding='utf-8')
    monkeypatch.setenv('SB_DOCTOR_KEY', 'sk-doctor-test')
    code = cli.main(['--config', str(cfg), 'doctor'])
    out, err = capsys.readouterr()
    assert code == 0, err
    report = json.loads(out)
    assert report['libyaml'] in {True, False} and report['fts5'] is True
    assert report['producer']['key'] == 'available'
    assert report['reviewer']['key'].startswith('unavailable')
    assert report['families'] == {'producer_family': 'openai', 'reviewer_family': 'anthropic'}
    assert report['layout']['knowledge'] == '10_Knowledge' and 'live' not in report
    assert 'sk-doctor-test' not in out

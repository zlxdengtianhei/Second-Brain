from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from secondbrain.store import Change, Conflict, apply_changes, digest, recover, state_root, vault_lock


def vault(tmp_path):
    root = tmp_path.resolve()
    (root / '10_Knowledge/test').mkdir(parents=True)
    (root / '10_Knowledge/test/a.md').write_bytes(b'original a\n')
    (root / '10_Knowledge/test/b.md').write_bytes(b'original b\n')
    return root


def changes():
    return [Change('10_Knowledge/test/a.md', digest(b'original a\n'), b'new a\n'),
            Change('10_Knowledge/test/b.md', digest(b'original b\n'), b'new b\n')]


def test_interrupted_batch_resumes_without_losing_original_bytes(tmp_path):
    root = vault(tmp_path)
    with pytest.raises(RuntimeError, match='injected interruption'):
        apply_changes(root, 'interrupted', changes(), _crash_after=1)
    assert (root / '10_Knowledge/test/a.md').read_bytes() == b'new a\n'
    assert (root / '10_Knowledge/test/b.md').read_bytes() == b'original b\n'
    result = recover(root, 'interrupted')
    assert result['status'] == 'applied'
    assert (root / '10_Knowledge/test/b.md').read_bytes() == b'new b\n'
    assert apply_changes(root, 'interrupted', changes())['replayed']
    recover(root, 'interrupted', 'rollback')
    assert (root / '10_Knowledge/test/a.md').read_bytes() == b'original a\n'
    assert (root / '10_Knowledge/test/b.md').read_bytes() == b'original b\n'


def test_foreign_edit_prevents_all_writes_and_is_never_overwritten(tmp_path):
    root = vault(tmp_path)
    (root / '10_Knowledge/test/b.md').write_bytes(b'human edit\n')
    with pytest.raises(Conflict):
        apply_changes(root, 'conflict', changes())
    assert (root / '10_Knowledge/test/a.md').read_bytes() == b'original a\n'
    assert (root / '10_Knowledge/test/b.md').read_bytes() == b'human edit\n'


def test_foreign_edit_after_interruption_blocks_resume_and_rollback(tmp_path):
    root = vault(tmp_path)
    with pytest.raises(RuntimeError):
        apply_changes(root, 'conflict2', changes(), _crash_after=1)
    (root / '10_Knowledge/test/a.md').write_bytes(b'human after interruption\n')
    for action in ['resume', 'rollback']:
        with pytest.raises(Conflict):
            recover(root, 'conflict2', action)
    assert (root / '10_Knowledge/test/b.md').read_bytes() == b'original b\n'
    assert (root / '10_Knowledge/test/a.md').read_bytes() == b'human after interruption\n'


def test_lock_is_held_by_process_not_a_stale_marker(tmp_path):
    root = vault(tmp_path)
    with vault_lock(root):
        with pytest.raises(Conflict, match='另一个'):
            apply_changes(root, 'locked', changes())
    assert apply_changes(root, 'locked', changes())['status'] == 'applied'


def test_other_process_cannot_write_until_owner_releases_lock(tmp_path):
    root = vault(tmp_path)
    program = '''from pathlib import Path
import sys
from secondbrain.store import Change, Conflict, apply_changes, digest
try:
    apply_changes(Path(sys.argv[1]), 'child', [Change('10_Knowledge/test/a.md', digest(b'original a\\n'), b'child a\\n')])
except Conflict:
    raise SystemExit(3)
'''
    with vault_lock(root):
        busy = subprocess.run([sys.executable, '-c', program, str(root)], capture_output=True)
        assert busy.returncode == 3, busy.stderr.decode()
        assert (root / '10_Knowledge/test/a.md').read_bytes() == b'original a\n'
    released = subprocess.run([sys.executable, '-c', program, str(root)], capture_output=True)
    assert released.returncode == 0, released.stderr.decode()
    assert (root / '10_Knowledge/test/a.md').read_bytes() == b'child a\n'


def test_lock_and_journal_live_external_not_in_vault(tmp_path):
    root = vault(tmp_path)
    assert apply_changes(root, 'external', changes())['status'] == 'applied'
    # no job state / lock anywhere inside the vault
    assert not (root / '_data').exists()
    # lock file and journal are under the external state root
    state = state_root(root)
    assert (state / 'write.lock').is_file()
    assert (state / 'jobs' / 'external' / 'transaction.json').is_file()


def test_state_root_sha_is_a_pure_function_of_the_canonical_vault_path(tmp_path):
    root = vault(tmp_path)
    assert state_root(root) == state_root(root.resolve())


def test_state_dir_cannot_be_configured_into_the_vault(tmp_path, monkeypatch):
    root = vault(tmp_path)
    forbidden = root / 'state'
    monkeypatch.setenv('SECONDBRAIN_STATE_DIR', str(forbidden))
    with pytest.raises(ValueError, match='state directory'):
        apply_changes(root, 'boundary', changes())
    assert not forbidden.exists()


def test_state_root_symlink_cannot_swap_the_write_surface(tmp_path):
    root = vault(tmp_path)
    state = state_root(root)
    attacker = tmp_path / 'attacker-state'
    attacker.mkdir()
    state.parent.mkdir(parents=True)
    state.symlink_to(attacker, target_is_directory=True)
    with pytest.raises(OSError):
        apply_changes(root, 'swapped-root', [Change('10_Knowledge/test/a.md', digest(b'original a\n'), b'bad')])
    assert not (attacker / 'jobs').exists()
    assert not (attacker / 'write.lock').exists()


def test_state_jobs_symlink_cannot_redirect_journal(tmp_path):
    root = vault(tmp_path)
    state = state_root(root)
    state.mkdir(parents=True)
    attacker = tmp_path / 'attacker-jobs'
    attacker.mkdir()
    (state / 'jobs').symlink_to(attacker, target_is_directory=True)
    with pytest.raises(OSError):
        apply_changes(root, 'swapped-jobs', [Change('10_Knowledge/test/a.md', digest(b'original a\n'), b'bad')])
    assert not (attacker / 'swapped-jobs').exists()


@pytest.mark.parametrize('bad', ['../outside.md', '/tmp/outside.md', '10_Knowledge/../a.md',
                                '.git/config', '90_Meta/CLAUDE.md', '20_MOCs/a/b/c.md'])
def test_rejects_paths_outside_owned_surface(tmp_path, bad):
    root = vault(tmp_path)
    with pytest.raises(ValueError):
        apply_changes(root, 'bad', [Change(bad, None, b'bad')])


def test_symlink_parent_cannot_redirect_write(tmp_path):
    root = vault(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / '10_Knowledge/evil').symlink_to(outside, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        apply_changes(root, 'symlink', [Change('10_Knowledge/evil/b.md', None, b'bad')])
    assert not (outside / 'b.md').exists()


def test_source_is_immutable_and_new_file_can_be_rolled_back(tmp_path):
    root = vault(tmp_path)
    change = Change('40_Sources/inbox/source.md', None, b'source original')
    apply_changes(root, 'new-source', [change])
    with pytest.raises(Conflict, match='不可覆盖'):
        apply_changes(root, 'overwrite-source', [Change(change.relative_path, digest(change.after), b'bad')])
    recover(root, 'new-source', 'rollback')
    assert not (root / change.relative_path).exists()


def test_reused_job_id_cannot_replace_transaction(tmp_path):
    root = vault(tmp_path)
    apply_changes(root, 'fixed-id', changes())
    with pytest.raises(Conflict, match='另一个变更集'):
        apply_changes(root, 'fixed-id', [Change('10_Knowledge/test/a.md', digest(b'new a\n'), b'different')])


def test_corrupt_journal_is_detected_before_replay(tmp_path):
    root = vault(tmp_path)
    apply_changes(root, 'corrupt', changes())
    p = state_root(root) / 'jobs' / 'corrupt' / 'transaction.json'
    journal = json.loads(p.read_text())
    journal['changes'][0]['after'] = 'b3RoZXI='
    p.write_text(json.dumps(journal))
    with pytest.raises(ValueError, match='哈希'):
        recover(root, 'corrupt')


def test_paired_card_move_can_resume_and_rollback_exact_bytes(tmp_path):
    root = vault(tmp_path)
    old = b'---\nid: a\ndomain: test\npillar: null\ntopic: null\nsr-due: 2026-09-01\ncustom: preserve\n---\nunchanged body\n<!--SR:!2026-09-01,5,250-->\n'
    new = old.replace(b'domain: test', b'domain: other').replace(b'topic: null', b'topic: new')
    (root / '10_Knowledge/test/a.md').write_bytes(old)
    plan = [Change('10_Knowledge/other/a.md', None, new), Change('10_Knowledge/test/a.md', digest(old), None)]
    with pytest.raises(RuntimeError):
        apply_changes(root, 'move', plan, _crash_after=1)
    assert (root / '10_Knowledge/test/a.md').read_bytes() == old
    assert (root / '10_Knowledge/other/a.md').read_bytes() == new
    recover(root, 'move')
    assert not (root / '10_Knowledge/test/a.md').exists()
    assert apply_changes(root, 'move', plan)['replayed']
    recover(root, 'move', 'rollback')
    assert (root / '10_Knowledge/test/a.md').read_bytes() == old
    assert not (root / '10_Knowledge/other/a.md').exists()


@pytest.mark.parametrize('bad', ['delete_only', 'body_change', 'sr_change', 'wrong_domain'])
def test_move_cannot_be_used_for_arbitrary_deletion_or_content_edits(tmp_path, bad):
    root = vault(tmp_path)
    old = b'---\nid: a\ndomain: test\nsr-due: 2026-09-01\n---\nbody\n'
    (root / '10_Knowledge/test/a.md').write_bytes(old)
    new = old.replace(b'domain: test', b'domain: other')
    if bad == 'body_change': new += b'changed'
    if bad == 'sr_change': new = new.replace(b'2026-09-01', b'2027-09-01')
    if bad == 'wrong_domain': new = old
    plan = [Change('10_Knowledge/test/a.md', digest(old), None)]
    if bad != 'delete_only': plan.append(Change('10_Knowledge/other/a.md', None, new))
    with pytest.raises(ValueError): apply_changes(root, 'invalid-move', plan)
    assert (root / '10_Knowledge/test/a.md').read_bytes() == old
    assert not (root / '10_Knowledge/other/a.md').exists()


def test_title_filename_move_preserves_stable_id_and_review_progress(tmp_path):
    root = vault(tmp_path)
    name = '有标题的旧卡.md'
    old = b'---\nid: card_stable\ndomain: test\ntags: [review, flashcards/test, custom]\nsr-due: 2026-09-01\n---\nbody\n'
    new = old.replace(b'domain: test', b'domain: other').replace(b'flashcards/test', b'flashcards/other')
    source, target = f'10_Knowledge/test/{name}', f'10_Knowledge/other/{name}'
    (root / source).write_bytes(old)
    apply_changes(root, 'title-move', [Change(target, None, new), Change(source, digest(old), None)])
    assert (root / target).read_bytes() == new and not (root / source).exists()
    recover(root, 'title-move', 'rollback')
    assert (root / source).read_bytes() == old


def test_read_dependency_change_blocks_apply_and_resume_without_overwrite(tmp_path):
    root = vault(tmp_path)
    dependency = root / 'links.md'; dependency.write_bytes(b'original references')
    guards = {'links.md': digest(dependency.read_bytes())}
    with pytest.raises(RuntimeError):
        apply_changes(root, 'guarded', changes(), checks=guards, _crash_after=1)
    dependency.write_bytes(b'new incoming reference')
    with pytest.raises(Conflict, match='只读依赖'):
        recover(root, 'guarded')
    assert (root / '10_Knowledge/test/b.md').read_bytes() == b'original b\n'
    # Rollback restores our files without modifying a read-only dependency.
    recover(root, 'guarded', 'rollback')
    assert dependency.read_bytes() == b'new incoming reference'
def test_literal_latex_backslash_in_posix_card_filename(tmp_path):
    from secondbrain.store import Change, apply_changes, digest, read_bytes
    root = tmp_path.resolve()
    rel = '10_Knowledge/compilers/文法 $\\varepsilon$.md'
    path = root / rel
    path.parent.mkdir(parents=True)
    path.write_bytes(b'original')
    apply_changes(root, 'latex-title', [Change(rel, digest(b'original'), b'updated')])
    assert read_bytes(root, rel) == b'updated'

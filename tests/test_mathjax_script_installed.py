"""The formula checker's script is installed with the package, so an install that is not editable finds it too.

Before, ``checks.NodeMathJax.from_env()`` looked for ``tools/mathjax_check.js`` three folders above its own file. That
is there in a source checkout (``pip install -e``), not in an install from a wheel or from git, so ``sb export``
stopped before it asked any model. Now the script is package data next to ``checks.py``; ``SB_MATHJAX_CHECK`` still
names another one first. The test builds a wheel from a copy of the source, installs it into a new virtual
environment and asks there. Finding the script needs no Node.js; running it needs ``node`` and ``mathjax-full``
(``SB_MATHJAX_NODE_PATH``), and that half is skipped without them.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_export import real_checker

ROOT = Path(__file__).resolve().parents[1]
PROBE = r'''
import json, os, sys
from pathlib import Path
from secondbrain.export import checks
packaged = Path(checks.__file__).with_name('mathjax_check.js')
checker = checks.NodeMathJax.from_env()
out = {'module': checks.__file__, 'packaged': str(packaged), 'packaged_is_file': packaged.is_file(),
       'from_env': checker.script if checker else None}
if checker is not None and len(sys.argv) > 1:
    out['preflight'] = checks.preflight(checker)
print(json.dumps(out))
'''


def _run(cmd, **kw):
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=600, **kw)
    assert done.returncode == 0, f'{cmd[:3]} failed: {done.stderr[-1500:]}'
    return done


@pytest.fixture(scope='module')
def installed(tmp_path_factory):
    """The Python of a new virtual environment with a wheel built from a copy of this source installed in it."""
    tmp = tmp_path_factory.mktemp('wheel')
    source = tmp / 'source'
    shutil.copytree(ROOT / 'src', source / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.egg-info'))
    for name in ('pyproject.toml', 'README.md', 'LICENSE'):
        shutil.copy2(ROOT / name, source / name)
    uv = shutil.which('uv')
    has_pip = subprocess.run([sys.executable, '-m', 'pip', '--version'], capture_output=True).returncode == 0
    if not (uv or has_pip):
        pytest.skip('needs uv or pip to build and install a wheel')
    dist = tmp / 'dist'
    _run([uv, 'build', '--wheel', '--out-dir', str(dist), str(source)] if uv else
         [sys.executable, '-m', 'pip', 'wheel', '--no-deps', '-w', str(dist), str(source)])
    [wheel] = dist.glob('secondbrain-*.whl')
    env_dir = tmp / 'venv'
    _run([sys.executable, '-m', 'venv', '--without-pip', str(env_dir)])
    python = env_dir / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    _run([uv, 'pip', 'install', '--python', str(python), '--no-deps', str(wheel)] if uv else
         [sys.executable, '-m', 'pip', '--python', str(python), 'install', '--no-deps', str(wheel)])
    return python, env_dir


def probe(python, *args, **env):
    """What the installed package says, asked from outside the source tree, with only ``env`` of the checker's
    variables set."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith(('SB_MATHJAX_', 'PYTHON'))}
    done = _run([str(python), '-I', '-c', PROBE, *args], cwd=python.parent, env={**clean, **env})
    return json.loads(done.stdout)


def test_an_install_from_a_wheel_finds_the_checker_script(installed):
    python, env_dir = installed
    found = probe(python)
    assert Path(found['module']).resolve().is_relative_to(env_dir.resolve())  # the installed copy, not this source
    assert found['packaged_is_file'], 'the wheel does not hold the checker script next to checks.py'
    script = Path(found['packaged'])
    assert script.read_bytes() == (ROOT / 'src' / 'secondbrain' / 'export' / 'mathjax_check.js').read_bytes()
    if shutil.which('node'):  # without node there is no checker; the script is found all the same
        assert Path(found['from_env']).resolve() == script.resolve()
    other = env_dir / 'other_check.js'
    other.write_text('// another checker\n', encoding='utf-8')
    named = probe(python, SB_MATHJAX_CHECK=str(other))
    if shutil.which('node'):
        assert named['from_env'] == str(other)  # SB_MATHJAX_CHECK comes first


@pytest.mark.skipif(real_checker() is None,
                    reason='needs node and mathjax-full (SB_MATHJAX_NODE_PATH=<folder>/node_modules)')
def test_the_installed_checker_script_runs(installed):
    python, _ = installed
    found = probe(python, 'preflight', SB_MATHJAX_NODE_PATH=os.environ['SB_MATHJAX_NODE_PATH'])
    assert found['from_env'] and found['preflight'] is None


def test_the_check_script_is_kept_in_one_place():
    assert not (ROOT / 'tools' / 'mathjax_check.js').exists()
    assert 'export/mathjax_check.js' in (ROOT / 'pyproject.toml').read_text(encoding='utf-8')

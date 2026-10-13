"""Every `sb` command and flag shown in the READMEs and in docs/ exists in the CLI, and links resolve."""
from __future__ import annotations

import argparse
import contextlib
import io
import re
import shlex
from pathlib import Path

import pytest

from secondbrain import cli

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / 'README.md', ROOT / 'README.zh-CN.md', ROOT / 'examples' / 'README.md',
        *sorted((ROOT / 'docs').glob('*.md'))]


def _subcommands(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def _options(parser):
    return {opt for action in parser._actions for opt in action.option_strings}


def _choices(parser):
    return {opt: action.choices for action in parser._actions if action.choices
            for opt in action.option_strings}


def _check_values(where, command, tokens, choices):
    """A literal value after an option with fixed choices must be one of them."""
    for flag, value in zip(tokens, tokens[1:]):
        legal = choices.get(flag.split('=')[0])
        if legal and not value.startswith(('<', '-')) and '|' not in value:
            assert value in {str(c) for c in legal}, (where, command, flag, value)


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def commands():
    found = []
    for path in DOCS:
        text = path.read_text(encoding='utf-8')
        where = path.relative_to(ROOT).as_posix()
        found += [(where, m) for m in re.findall(r'`(sb [^`]+)`', text)]
        found += [(where, line.split(' #')[0].strip()) for line in text.splitlines()
                  if line.strip().startswith('sb ')]
    return found


def _diagrams_help(args: list[str]) -> tuple[int, str]:
    from secondbrain.diagrams.__main__ import main
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            code = main(args)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue()


def _check_diagrams(where, command, tokens):
    if len(tokens) < 2 or tokens[1].startswith(('<', '-')):
        return
    code, text = _diagrams_help([tokens[1], '--help'])
    assert code == 0, (where, command, text[-300:])
    for token in tokens[2:]:
        flag = token.split('=')[0]
        if flag.startswith('--') and flag != '--help':
            assert re.search(rf'(?<![\w-]){re.escape(flag)}(?![\w-])', text), (where, command, flag)


@pytest.mark.parametrize('name', ['README.md', 'README.zh-CN.md'])
def test_readme_shows_commands(name):
    assert sum(where == name for where, _ in commands()) > 10, name


def test_every_command_in_the_docs_exists():
    top = cli.parser()
    seen = commands()
    assert len(seen) > 30
    top_options = _options(top)
    for where, command in seen:
        tokens = _tokens(command)[1:]
        # Global options may come before the subcommand.
        while tokens and tokens[0] in top_options:
            tokens = tokens[2:]
        if not tokens or tokens[0].startswith('<'):
            continue
        sub = _subcommands(top)
        assert tokens[0] in sub, (where, command)
        if tokens[0] == 'diagrams':
            _check_diagrams(where, command, tokens)
            continue
        parser = sub[tokens[0]]
        nested = _subcommands(parser)
        if nested and len(tokens) > 1 and not tokens[1].startswith(('<', '-')):
            assert tokens[1] in nested, (where, command)
            parser = nested[tokens[1]]
        options = _options(parser) | top_options
        for token in tokens:
            flag = token.split('=')[0]
            if flag.startswith('--'):
                assert flag in options, (where, command, flag)
        _check_values(where, command, tokens, _choices(parser))


def test_readmes_link_to_each_other():
    english = (ROOT / 'README.md').read_text(encoding='utf-8')
    chinese = (ROOT / 'README.zh-CN.md').read_text(encoding='utf-8')
    assert '(README.zh-CN.md)' in english
    assert '(README.md)' in chinese
    # Each page shows the atlas GIF in its own language.
    assert '(docs/media/second-brain-atlas-en.gif)' in english and '-zh.gif' not in english
    assert '(docs/media/second-brain-atlas-zh.gif)' in chinese and '-en.gif' not in chinese


def test_relative_links_in_the_docs_resolve():
    for path in DOCS:
        text = path.read_text(encoding='utf-8')
        for target in re.findall(r'\]\(([^)\s]+)\)', text):
            if re.match(r'[a-z]+:', target) or target.startswith('#') or target.startswith('../../'):
                continue
            rel = target.split('#')[0]
            assert (path.parent / rel).exists(), (path.name, target)

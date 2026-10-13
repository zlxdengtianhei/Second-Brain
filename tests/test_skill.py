"""The Agent Skill stays in step with the CLI it drives."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

from secondbrain import cli

SKILL = Path(__file__).resolve().parents[1] / 'skills' / 'secondbrain'


def _subcommands(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def _options(parser):
    return {opt for action in parser._actions for opt in action.option_strings}


def commands():
    found = []
    for path in sorted(SKILL.rglob('*.md')):
        text = path.read_text(encoding='utf-8')
        found += [(path.name, m) for m in re.findall(r'`(sb [^`]+)`', text)]
        found += [(path.name, line.split('#')[0].strip()) for line in text.splitlines()
                  if line.strip().startswith('sb ')]
    return found


def test_frontmatter_follows_the_agent_skills_format():
    text = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
    head = yaml.safe_load(text.split('---\n', 2)[1])
    assert head['name'] == SKILL.name and re.fullmatch(r'[a-z0-9-]{1,64}', head['name'])
    assert 50 < len(head['description']) <= 1024
    for ref in re.findall(r'`(references/[^`]+)`', text):
        assert (SKILL / ref).is_file(), ref


def test_every_command_in_the_skill_exists():
    top = cli.parser()
    seen = commands()
    assert len(seen) > 10
    for where, command in seen:
        tokens = command.split()[1:]
        sub = _subcommands(top)
        assert tokens[0] in sub, (where, command)
        parser = sub[tokens[0]]
        nested = _subcommands(parser)
        if nested and len(tokens) > 1 and not tokens[1].startswith(('<', '-')):
            assert tokens[1] in nested, (where, command)
            parser = nested[tokens[1]]
        options = _options(parser) | _options(top)
        for token in tokens:
            flag = token.split('=')[0]
            if flag.startswith('--'):
                assert flag in options, (where, command, flag)

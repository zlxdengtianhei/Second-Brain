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


def test_subagent_texts_use_exactly_the_hand_off_values(tmp_path):
    from secondbrain.executors.base import Task
    from secondbrain.executors.host import HostExecutor
    packet = tmp_path / 'job' / 'packets' / 'run.md'
    packet.parent.mkdir(parents=True)
    handoff = HostExecutor().run(Task(job_id='j', run_id='r', role='producer', step='produce', prompt='',
                                      packet={}, packet_sha256='', output_schema={}, chunk='c01',
                                      packet_path=str(packet))).handoff
    for name in ('subagent-producer.md', 'subagent-reviewer.md'):
        text = (SKILL / 'references' / name).read_text(encoding='utf-8')
        block = text.split('```text\n', 1)[1].split('```', 1)[0]
        values = set(re.findall(r'\{([a-z_]+)\}', block))
        assert values == {'packet_path', 'output_path', 'submit_cmd'} and values <= set(handoff), name
        assert f'the {len(values)} ' in text.replace('three', '3'), name
    assert 'Fill in the three values' in (SKILL / 'SKILL.md').read_text(encoding='utf-8')


def test_every_inbox_status_is_listed():
    source = (Path(cli.__file__).parent / 'inbox.py').read_text(encoding='utf-8')
    statuses = set(re.findall(r"status='([a-z_]+)'", source)) | {'new', 'failed'}
    line = next(x for x in (SKILL / 'SKILL.md').read_text(encoding='utf-8').splitlines()
                if x.startswith('Each item has `status`'))
    assert statuses <= set(re.findall(r'`([a-z_]+)`', line)), statuses

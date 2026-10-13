import asyncio

import pytest

pytest.importorskip('mcp')

from mcp import Client
from secondbrain.mcp_server import create_server
from tests.helpers import build_vault


def names(server):
    async def read():
        async with Client(server) as client:
            listed = await client.list_tools()
            return {tool.name for tool in listed.tools}
    return asyncio.run(read())


def test_reader_has_no_write_tool_and_writer_adds_only_submit(tmp_path):
    root = build_vault(tmp_path)
    reader = {'status', 'read_task', 'read_cards', 'search', 'read_taxonomy'}
    assert names(create_server(root)) == reader
    assert names(create_server(root, writer=True)) == reader | {'job_submit'}

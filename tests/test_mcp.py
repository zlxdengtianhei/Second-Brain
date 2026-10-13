import asyncio
import json

import pytest

pytest.importorskip('mcp')

from mcp import Client  # noqa: E402
from secondbrain.mcp_server import create_server  # noqa: E402
from tests.helpers import build_vault  # noqa: E402
from tests.ingest_helpers import (ALL_REASONS, DIMMING_IR, SOURCE_NAME, build_ingest_vault,  # noqa: E402
                                  diagram_answer, first_candidate, first_review, packet_in, repair_candidate,
                                  repair_review)

READER = {'status', 'inbox_list', 'read_task', 'read_cards', 'search', 'read_taxonomy', 'job_show', 'job_packet'}
WRITER = {'ingest_prepare', 'job_submit', 'job_advance'}


def names(server):
    async def read():
        async with Client(server) as client:
            listed = await client.list_tools()
            return {tool.name: tool.annotations for tool in listed.tools}
    return asyncio.run(read())


def test_reader_has_no_vault_writes_and_writer_adds_submit_and_advance(tmp_path):
    root = build_vault(tmp_path)
    reader = names(create_server(root))
    assert set(reader) == READER
    assert all(reader[n].read_only_hint for n in READER - {'job_packet'})
    writer = names(create_server(root, writer=True))
    assert set(writer) == READER | WRITER
    assert writer['job_advance'].destructive_hint is True
    assert writer['job_submit'].destructive_hint is False


def test_host_flow_over_mcp(tmp_path):
    root = build_ingest_vault(tmp_path / 'vault')
    server = create_server(root, writer=True)

    async def flow():
        async with Client(server) as client:
            async def call(name, **args):
                result = await client.call_tool(name, args)
                assert not result.is_error, result.content
                if result.structured_content is None:  # dict results without an output schema
                    return json.loads(result.content[0].text)
                return result.structured_content.get('result', result.structured_content)

            [item] = await call('inbox_list')
            assert item['status'] == 'new'
            prepared = await call('ingest_prepare', path=str(root / item['path']))
            job = prepared['job_id']
            produce = await call('job_packet', job=job, role='producer')
            assert 'Lantern queues' in produce['text'] and 'packet_path' not in produce
            submitted = await call('job_submit', job=job, role='producer', run_id=produce['run_id'],
                                   payload=first_candidate(), model='gpt-test')
            assert submitted['written_to_knowledge'] is False
            review = await call('job_packet', job=job, role='reviewer')
            assert not any(reason in review['text'] for reason in ALL_REASONS)
            await call('job_submit', job=job, role='reviewer', run_id=review['run_id'], payload=first_review(),
                       model='claude-test')
            state = await call('job_advance', job=job, executor='host')
            [repair] = state['handoffs']
            repair = await call('job_packet', job=job, role='producer', chunk=repair['chunk'])
            await call('job_submit', job=job, role='producer', run_id=repair['run_id'], payload=repair_candidate(),
                       model='gpt-test')
            last = await call('job_packet', job=job, role='reviewer')
            await call('job_submit', job=job, role='reviewer', run_id=last['run_id'], payload=repair_review(),
                       model='claude-test')
            diagram = await call('job_packet', job=job, role='producer')
            assert diagram['chunk'] == 'dg01'
            await call('job_submit', job=job, role='producer', run_id=diagram['run_id'],
                       payload=diagram_answer(packet_in(diagram['text']), {'marker-dimming': DIMMING_IR}),
                       model='gpt-test')
            done = await call('job_advance', job=job, executor='host')
            assert done['status'] == 'applied' and done['new_card_count'] == 3
            shown = await call('job_show', job=job)
            assert len(shown['run_records']) == 5
            found = await call('search', query='lantern queue markers', limit=3)
            assert 'Lantern queue' in {hit['title'] for hit in found}
            assert await call('inbox_list') == []
    asyncio.run(flow())

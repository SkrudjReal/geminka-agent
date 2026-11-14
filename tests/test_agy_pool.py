import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agy_cli import AgyCliClient


@pytest.fixture
def pool(monkeypatch):
    client = AgyCliClient()
    client.max_sessions = 1
    spawned, closed = [], []

    async def spawn(*args, **kwargs):
        spawned.append((args, kwargs))
        return SimpleNamespace(returncode=None)

    async def close(session):
        closed.append(session)
        session.process.returncode = 0

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(client, '_launch_command', lambda *a: ['fake-agy'])
    monkeypatch.setattr(client, '_drain_stderr', AsyncMock())
    monkeypatch.setattr(client, '_close_session', close)
    return client, spawned, closed


@pytest.mark.asyncio
async def test_busy_pool_waits_then_evicts_idle_session(pool):
    client, spawned, closed = pool
    first = await client._get_session(1, 'gemini', 'low')
    pending = asyncio.create_task(client._get_session(2, 'gemini', 'low'))
    await asyncio.sleep(0)
    assert not pending.done() and len(spawned) == 1
    async with client._lease(first):
        await client._expire_idle()
        assert not closed
    second = await asyncio.wait_for(pending, 1)
    assert closed == [first] and len(client._sessions) == 1
    assert second.borrowers == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_idle_cleanup_preserves_conversation_and_resumes_exact_id(pool):
    client, spawned, closed = pool
    first = await client._get_session(1, 'gemini', 'low')
    first.started = True
    first.conversation_id = 'saved-conversation'
    first.instructions = 'persona'
    first.group_context_state = {'cursor': 100}
    async with client._lease(first):
        pass
    first.last_used -= client.idle_seconds + 1
    async with client._available:
        await client._expire_idle()
    assert not client._sessions and closed == [first]
    resumed = await client._get_session(1, 'gemini', 'low')
    assert spawned[-1][0][-2:] == ('--conversation', 'saved-conversation')
    assert resumed.started and resumed.instructions == 'persona'
    assert resumed.group_context_state == {'cursor': 100}
    assert spawned[-1][1]['start_new_session'] is True
    await client.aclose()


@pytest.mark.asyncio
async def test_borrowed_session_not_expired_before_lock_is_acquired(pool):
    client, _, closed = pool
    session = await client._get_session(1, 'gemini', 'low')
    session.last_used -= client.idle_seconds + 1
    await client._expire_idle()
    assert not closed
    await client.aclose()


@pytest.mark.asyncio
async def test_shutdown_wakes_waiting_requests(pool):
    client, _, _ = pool
    await client._get_session(1, 'gemini', 'low')
    pending = asyncio.create_task(client._get_session(2, 'gemini', 'low'))
    await asyncio.sleep(0)
    await client.aclose()
    with pytest.raises(RuntimeError, match='closed'):
        await asyncio.wait_for(pending, 1)


@pytest.mark.asyncio
async def test_background_limit_is_shared_between_clients(monkeypatch):
    first, second = AgyCliClient(), AgyCliClient()
    entered = []
    release = asyncio.Event()

    async def work(*args):
        entered.append(1)
        await release.wait()
        yield 'ok'

    monkeypatch.setattr(first, '_stream_once_unlimited', work)
    monkeypatch.setattr(second, '_stream_once_unlimited', work)

    async def consume(client):
        return [part async for part in client._stream_once('gemini', 'low', 'prompt')]

    tasks = [asyncio.create_task(consume(client)) for client in (first, second)]
    await asyncio.sleep(0)
    assert len(entered) == 1
    release.set()
    assert await asyncio.gather(*tasks) == [['ok'], ['ok']]
    await first.aclose()
    await second.aclose()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != 'posix', reason='Linux process-group lifecycle')
async def test_closing_process_also_terminates_children(monkeypatch):
    client = AgyCliClient()
    script = ('import subprocess,sys,time; '
              'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); '
              'print(p.pid,flush=True); time.sleep(60)')
    monkeypatch.setattr(client, '_launch_command', lambda *a: [sys.executable, '-c', script])
    try:
        session = await client._get_session(1, 'test', 'low')
        child = int(await asyncio.wait_for(session.process.stdout.readline(), 5))
        await client.aclose()
        for _ in range(20):
            status = Path(f'/proc/{child}/status')
            if not status.exists() or 'State:\tZ' in status.read_text():
                break
            await asyncio.sleep(.05)
        else:
            pytest.fail('AGY child process survived pool shutdown')
    finally:
        await client.aclose()

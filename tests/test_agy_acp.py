import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services.agy_acp import AgyAcpClient, _group_read_permission, _Peer, _VisibleOutput
from app.services.agy_cli import AgyNoResponseError


@pytest.mark.parametrize('tail', ['Нормальный ответ', ''])
@pytest.mark.parametrize('indent', ['', ' ', '\t', '\u00a0', '\ufeff '])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_provider_capacity_retries_hidden_across_all_chunk_boundaries(tail, indent, newline):
    diagnostic = indent + 'API error (attempt 1): UNAVAILABLE (code 503): No capacity available for model example' + newline
    source = diagnostic + '\n' + diagnostic.replace('attempt 1', 'attempt 2') + tail
    for boundary in range(len(source) + 1):
        visible = _VisibleOutput()
        result = visible.feed(source[:boundary]) + visible.feed(source[boundary:]) + visible.finish()
        assert result.strip() == tail
    visible = _VisibleOutput()
    assert (''.join(visible.feed(char) for char in source) + visible.finish()).strip() == tail
    visible = _VisibleOutput()
    assert visible.feed(diagnostic.rstrip()) + visible.finish() == ''
    quoted = '```text\n' + diagnostic + '```'
    visible = _VisibleOutput()
    assert visible.feed(quoted) + visible.finish() == quoted


@pytest.mark.parametrize('indent', ['', ' ', '\t'])
def test_provider_safety_diagnostics_are_hidden_at_every_chunk_boundary(indent):
    notice = (indent + '⚠ Your previous response was blocked by content safety filters: The model output could not be generated. '
              'This output contains sensitive words.\nPlease provide a compliant response.\nRetries remaining: 3\n')
    text = notice + notice.replace('3\n', '2\n') + 'Нет, такое не обсуждаю.'
    for boundary in range(len(text) + 1):
        visible = _VisibleOutput()
        assert visible.feed(text[:boundary]) + visible.feed(text[boundary:]) + visible.finish() == 'Нет, такое не обсуждаю.'
    quoted = '```text\n' + notice + '```'
    visible = _VisibleOutput()
    assert visible.feed(quoted) + visible.finish() == quoted


@pytest.mark.parametrize('allowed', [True, False])
async def test_permission_requests_fail_closed_for_restricted_peers(allowed):
    process = SimpleNamespace(stdout=asyncio.StreamReader(), stderr=asyncio.StreamReader())
    writes = []
    async def drain():
        pass
    process.stdin = SimpleNamespace(write=lambda data: writes.append(json.loads(data)), drain=drain)
    peer = _Peer(process, 1, allow_permissions=allowed)
    process.stdout.feed_data((json.dumps({'id': 1, 'method': 'session/request_permission',
                                         'params': {'options': [{'kind': 'allow_once', 'optionId': 'yes'}]}}) + '\n').encode())
    process.stdout.feed_eof()
    await peer.reader
    peer.stderr.cancel()
    await asyncio.gather(peer.stderr, return_exceptions=True)
    outcome = writes[0]['result']['outcome']
    assert outcome['outcome'] == ('selected' if allowed else 'cancelled')


@pytest.mark.parametrize('name,kind,args,expected', [
    ('collector_call', 'other', {'tool_name': 'search', 'arguments': {'public_only': False}}, True),
    ('collector_call', 'other', {'tool_name': 'group_memory_search'}, True),
    ('collector_tools', 'other', {}, True),
    ('keenable_search', 'other', {}, True),
    ('skill_read', 'other', {}, True),
    ('collector_call', 'execute', {'tool_name': 'search'}, False),
    ('collector_call', 'other', {'tool_name': 'get_user_history'}, False),
    ('collector_call', 'other', {'tool_name': []}, False),
    ('collector_call', 'other', {}, False),
    ('bot_api_call', 'other', {'method': 'send_message'}, False),
    ('memory_write', 'other', {}, False),
    ('memory_search', 'other', {}, False),
    ('run_command', 'execute', {}, False),
])
async def test_group_permission_only_allows_bridge_reads(name, kind, args, expected):
    tool = {'title': 'Run mcp_geminka-bot-api_' + name, 'kind': kind, 'rawInput': args}
    assert _group_read_permission(tool) is expected
    assert not _group_read_permission({**tool, 'title': 'Run mcp_other_' + name})
    process = SimpleNamespace(stdout=asyncio.StreamReader(), stderr=asyncio.StreamReader())
    writes = []
    async def drain():
        pass
    process.stdin = SimpleNamespace(write=lambda data: writes.append(json.loads(data)), drain=drain)
    peer = _Peer(process, 1, allow_permissions=False, allow_group_reads=True)
    process.stdout.feed_data((json.dumps({'id': 1, 'method': 'session/request_permission', 'params': {
        'toolCall': tool, 'options': [{'kind': 'allow_once', 'optionId': 'yes'}],
    }}) + '\n').encode())
    process.stdout.feed_eof()
    await peer.reader
    peer.stderr.cancel()
    await asyncio.gather(peer.stderr, return_exceptions=True)
    assert writes[0]['result']['outcome']['outcome'] == ('selected' if expected else 'cancelled')


@pytest.mark.parametrize('warning', _VisibleOutput.warnings)
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('indent', ['', ' ', '\t', '\u00a0'])
def test_empty_step_warning_is_removed_at_every_chunk_boundary(warning, newline, indent):
    source = f'Начало\n{indent}{warning}{newline}Продолжение <b>ответа</b>'
    for boundary in range(len(source) + 1):
        visible = _VisibleOutput()
        result = visible.feed(source[:boundary]) + visible.feed(source[boundary:]) + visible.finish()
        assert result == 'Начало\nПродолжение <b>ответа</b>'


@pytest.mark.parametrize('source', [
    'Обычный ответ', '⚠️ Важное предупреждение',
    'Что означает «⚠ Model produced invalid output.»?',
    '⚠ Model produced invalid output. Это цитата в строке.',
    '⚠ Model produced', '<code>⚠ Model produced invalid output.</code>',
    '```text\n⚠ Model produced invalid output.\n```',
    '  Обычный ответ\n\tс отступами\n\n', ' \t',
    '> API error (attempt 1): UNAVAILABLE (code 503)\n',
    'Пример: API error (attempt 1): UNAVAILABLE (code 503)',
    '  ```text\n API error (attempt 1): UNAVAILABLE (code 503)\n  ```',
])
def test_unrelated_warnings_and_quoted_diagnostics_are_preserved(source):
    visible = _VisibleOutput()
    assert ''.join(visible.feed(char) for char in source) + visible.finish() == source


@pytest.mark.parametrize('chunks', [
    ['⚠ Mo', 'del produced invalid output.', '\n', 'Готово'],
    [' ', 'API error (attempt ', '1): UNAVAILABLE (code 503)\r\n',
     '\tAPI error (attempt 2): UNAVAILABLE (code 503)', '\n', 'Готово'],
])
async def test_recovered_warning_does_not_replay_prompt_or_tools(acp, monkeypatch, chunks):
    client, calls, _ = acp
    session = await client._get_session(1, 'gemini-low', 'low', {'requester_id': 'summary'})
    peer = session.peer
    original = peer.call

    async def call(method, params):
        if method == 'session/prompt':
            calls.append((peer.process, {'method': method}))
            for chunk in chunks:
                peer.streams[session.conversation_id].put_nowait(chunk)
            return {'stopReason': 'end_turn'}
        return await original(method, params)

    monkeypatch.setattr(peer, 'call', call)
    try:
        result = ''.join([chunk async for chunk in client._send(session, 'hello')])
        assert result == 'Готово'
        assert sum(m['method'] == 'session/prompt' for _, m in calls) == 1
    finally:
        await client.aclose()


@pytest.mark.parametrize('warning', [
    '⚠ Model produced invalid output.', '\n⚠ Model produced invalid output.\n',
    ' ⚠ Model produced invalid output.',
    ' API error (attempt 1): UNAVAILABLE (code 503)\n',
    '\tAPI error (attempt 1): UNAVAILABLE (code 503)',
])
async def test_warning_only_turn_is_not_a_success_or_retryable(acp, monkeypatch, warning):
    client, _, _ = acp
    session = await client._get_session(1, 'gemini-low', 'low', {'requester_id': 'summary'})
    peer = session.peer
    original = peer.call

    async def call(method, params):
        if method == 'session/prompt':
            peer.streams[session.conversation_id].put_nowait(warning)
            return {'stopReason': 'end_turn'}
        return await original(method, params)

    monkeypatch.setattr(peer, 'call', call)
    try:
        with pytest.raises(AgyNoResponseError) as error:
            _ = [chunk async for chunk in client._send(session, 'hello')]
        assert not error.value.retryable
    finally:
        await client.aclose()


async def test_group_role_does_not_use_plan_mode_but_keeps_permission_denial(acp):
    client, calls, _ = acp
    try:
        session = await client._get_session(1, 'gemini-low', 'low', {'requester_id': '7', 'chat_id': '-123'})
        modes = [message['params']['modeId'] for _, message in calls if message.get('method') == 'session/set_mode']
        assert modes == ['accept-edits']
        assert session.peer.allow_permissions is False
        assert session.peer.allow_group_reads is True
        assert client._route({'requester_id': '7', 'chat_id': '-123'})[1] is True
    finally:
        await client.aclose()


@pytest.fixture
def acp(monkeypatch):
    client = AgyAcpClient()
    calls, processes = [], []

    async def spawn(*args, **kwargs):
        stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
        process = SimpleNamespace(returncode=None, stdout=stdout, stderr=stderr, pid=None)

        def emit(message):
            stdout.feed_data((json.dumps(message) + '\n').encode())

        def write(raw):
            message = json.loads(raw)
            calls.append((process, message))
            method, params = message.get('method'), message.get('params', {})
            if 'id' not in message or method is None:
                return
            result = {}
            if method == 'session/new':
                result = {'sessionId': f'session-{len(calls)}'}
            elif method == 'session/load':
                result = {'sessionId': params['sessionId']}
                emit({'method': 'session/update', 'params': {
                    'sessionId': params['sessionId'], 'update': {
                        'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'OLD REPLAY'},
                    },
                }})
            elif method == 'session/prompt':
                emit({'method': 'session/update', 'params': {
                    'sessionId': params['sessionId'], 'update': {
                        'sessionUpdate': 'agent_message_chunk',
                        'content': {'type': 'text', 'text': params['sessionId']},
                    },
                }})
                result = {'stopReason': 'end_turn'}
            emit({'jsonrpc': '2.0', 'id': message['id'], 'result': result})

        async def drain():
            pass

        async def wait():
            return process.returncode

        def terminate():
            process.returncode = 0
            stdout.feed_eof()
            stderr.feed_eof()

        process.stdin = SimpleNamespace(write=write, drain=drain)
        process.wait, process.terminate, process.kill = wait, terminate, terminate
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(client, '_sandbox_command', lambda *a, **kw: ['fake-agy'])
    return client, calls, processes


async def test_two_conversations_share_process_and_route_concurrent_output(acp):
    client, calls, processes = acp
    try:
        one = await client._get_session(1, 'gemini-low', 'low', {'requester_id': 'summary'})
        two = await client._get_session(2, 'gemini-low', 'low', {'requester_id': 'summary'})
        assert len(processes) == 1 and one.process is two.process
        assert one.conversation_id != two.conversation_id

        async def consume(session):
            async with client._lease(session):
                return ''.join([text async for text in client._send(session, 'hello')])

        assert await asyncio.gather(consume(one), consume(two)) == [one.conversation_id, two.conversation_id]
        await client._close_session(one)
        await client._close_session(one)
        assert two.process.returncode is None
        assert await consume(two) == two.conversation_id
    finally:
        await client.aclose()
    assert all(p.returncode == 0 for p in processes)


async def test_cancel_is_scoped_to_session_not_shared_process(acp, monkeypatch):
    client, _, _ = acp
    session = await client._get_session(1, 'gemini-low', 'low', {'requester_id': 'summary'})
    peer = session.peer
    entered, cancelled = asyncio.Event(), asyncio.Event()
    original_call = peer.call

    async def call(method, params):
        if method == 'session/prompt':
            entered.set()
            await cancelled.wait()
            return {'stopReason': 'cancelled'}
        return await original_call(method, params)

    async def notify(method, params):
        assert method == 'session/cancel' and params['sessionId'] == session.conversation_id
        cancelled.set()

    monkeypatch.setattr(peer, 'call', call)
    monkeypatch.setattr(peer, 'notify', notify)

    async def consume():
        return [x async for x in client._send(session, 'hello')]

    task = asyncio.create_task(consume())
    await entered.wait()
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set() and peer.process.returncode is None
        assert not peer.streams
    finally:
        await client.aclose()


async def test_rpc_timeout_is_non_retryable_and_does_not_leave_pending_future():
    process = SimpleNamespace(stdout=asyncio.StreamReader(), stderr=asyncio.StreamReader())

    async def drain():
        pass

    process.stdin = SimpleNamespace(write=lambda _: None, drain=drain)
    peer = _Peer(process, 0.01)
    try:
        with pytest.raises(AgyNoResponseError) as error:
            await peer.call('session/prompt', {})
        assert not error.value.retryable and not peer.pending
    finally:
        peer.reader.cancel()
        peer.stderr.cancel()
        await asyncio.gather(peer.reader, peer.stderr, return_exceptions=True)


async def test_park_load_preserves_exact_id_without_replaying_history(acp):
    client, calls, _ = acp
    try:
        context = {'requester_id': 'summary'}
        session = await client._get_session(1, 'gemini-low', 'low', context)
        session.started, session.instructions = True, 'PERSONA'
        session.group_context_state = {'cursor': 100}
        async with client._lease(session):
            pass
        await client._park(1, session)
        resumed = await client._get_session(1, 'gemini-low', 'low', context)
        assert resumed.conversation_id == session.conversation_id
        assert resumed.instructions == 'PERSONA' and resumed.group_context_state == {'cursor': 100}
        async with client._lease(resumed):
            result = ''.join([text async for text in client._send(resumed, 'next')])
        assert result == resumed.conversation_id and 'REPLAY' not in result
        load = next(m for _, m in calls if m['method'] == 'session/load')
        assert load['params']['sessionId'] == session.conversation_id
    finally:
        await client.aclose()


async def test_idle_cleanup_closes_shared_process_only_after_last_session(acp):
    client, _, processes = acp
    try:
        one = await client._get_session(1, 'gemini-low', 'low', {'requester_id': 'summary'})
        two = await client._get_session(2, 'gemini-low', 'low', {'requester_id': 'summary'})
        async with client._lease(one):
            pass
        one.last_used -= client.idle_seconds + 1
        await client._expire_idle()
        assert list(client._sessions) == [2] and processes[0].returncode is None
        async with client._lease(two):
            pass
        two.last_used -= client.idle_seconds + 1
        await client._expire_idle()
        assert not client._sessions and not client._peers
        assert processes[0].returncode == 0
    finally:
        await client.aclose()


async def test_group_users_get_per_session_tool_identity_but_share_server(acp):
    client, calls, processes = acp
    try:
        for uid in (11, 22):
            await client._get_session(uid, 'gemini-low', 'low', {
                'requester_id': str(uid), 'chat_id': '-123',
                'group_broker_url': 'http://localhost/mcp', 'group_broker_token': 'test-only',
            })
        assert len(processes) == 1
        envs = [dict((v['name'], v['value']) for v in m['params']['mcpServers'][0]['env'])
                for _, m in calls if m['method'] == 'session/new']
        assert [env['GEMINKA_REQUESTER_ID'] for env in envs] == ['11', '22']
        assert all(env['GEMINKA_GROUP_BROKER_TOKEN'] == 'test-only' for env in envs)
        assert all(env['GEMINKA_MEMORY_READ_ONLY'] == '1' for env in envs)
        assert client._route({'requester_id': 'summary', 'chat_id': '-123'}) != client._route({'requester_id': '11', 'chat_id': '-123'})
        assert client._route({'chat_id': '-456'}) != client._route({'chat_id': '-123'})
    finally:
        await client.aclose()


async def test_mcp_cache_budget_recycles_only_idle_server_and_retains_ids(acp):
    client, _, processes = acp
    context = {'requester_id': '11', 'chat_id': '-123'}
    try:
        one = await client._get_session(1, 'gemini-low', 'low', context)
        two = await client._get_session(2, 'gemini-low', 'low', context)
        for session in (one, two):
            session.started, session.instructions = True, 'PERSONA'
        async with client._lease(one):
            pass
        pending = asyncio.create_task(client._get_session(3, 'gemini-low', 'low', context))
        await asyncio.sleep(0)
        assert not pending.done() and processes[0].returncode is None
        async with client._lease(two):
            pass
        three = await asyncio.wait_for(pending, 1)
        assert processes[0].returncode == 0 and len(processes) == 2
        assert client._parked[1]['conversation_id'] == one.conversation_id
        assert client._parked[2]['conversation_id'] == two.conversation_id
        async with client._lease(three):
            pass
        resumed = await client._get_session(1, 'gemini-low', 'low', context)
        assert resumed.conversation_id == one.conversation_id
    finally:
        await client.aclose()


async def test_churn_never_retains_unbounded_servers_or_mcp_cache(acp):
    client, _, processes = acp
    try:
        for user_id in range(10):
            session = await client._get_session(user_id, 'gemini-low', 'low', {'requester_id': str(user_id), 'chat_id': '-123'})
            async with client._lease(session):
                pass
            assert len([p for p in processes if p.returncode is None]) == 1
            assert len(client._sessions) <= 2
            assert all(p.session_count <= 2 for p in client._peers.values())
    finally:
        await client.aclose()


async def test_eof_fails_all_pending_without_retry_or_private_details(acp):
    client, _, _ = acp
    process = SimpleNamespace(stdout=asyncio.StreamReader(), stderr=asyncio.StreamReader())

    async def drain():
        pass

    process.stdin = SimpleNamespace(write=lambda _: None, drain=drain)
    peer = _Peer(process, 1)
    task = asyncio.create_task(peer.call('session/prompt', {}))
    await asyncio.sleep(0)
    process.stdout.feed_eof()
    try:
        with pytest.raises(AgyNoResponseError) as error:
            await task
        assert not error.value.retryable
    finally:
        peer.stderr.cancel()
        await asyncio.gather(peer.reader, peer.stderr, return_exceptions=True)
        await client.aclose()

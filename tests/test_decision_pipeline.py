import json
from unittest.mock import AsyncMock

import pytest

from app.core import config
from app.core.context import ContextManager
from app.core.state import StateStore
from app.services.antigravity import (
    INFORMATION_ROLE,
    MCP_RESPONSE_RULES,
    PRIVATE_RESPONSE_RULES,
    AntigravityClient,
    GatewayUnavailable,
)
from app.services.decisions import decisions
from app.services.sqlite_memory import SQLiteMemory


@pytest.mark.parametrize('planned', [True, False])
async def test_luna_search_handoff_or_gemini_fallback(tmp_path, monkeypatch, planned):
    from app.services import group_collector, retrieval, search_planner

    monkeypatch.setattr(config, 'settings', config.Settings.from_env({'TELEGRAM_OWNER_ID': '1'}))
    state = StateStore(tmp_path / 'state.db')
    memory = SQLiteMemory(tmp_path / 'memory.db', store=state)
    client = AntigravityClient(store=state, contexts=ContextManager(state), memories=memory)
    client.group_memories.root = tmp_path / 'groups'
    monkeypatch.setattr(group_collector.GroupCollectorBroker, 'credentials', AsyncMock(return_value={}))
    prepare = AsyncMock(return_value={'route': 'mcp', 'action': 'history', 'complete': True,
                                      'context': 'VERIFIED_SEARCH_FACTS'} if planned else None)
    monkeypatch.setattr(search_planner, 'prepare', prepare)
    fetch = AsyncMock(return_value='GEMINI_FALLBACK_FACTS')
    monkeypatch.setattr(retrieval, 'retrieve', fetch)
    seen = []
    async def stream(*args, **kwargs):
        seen.append(kwargs)
        yield '<blockquote expandable>Отчет</blockquote>'
    client._agy.stream = stream
    source = {'private': False, 'chat_id': -1001, 'message_id': 42,
              'text': 'саммари чата по MCP', 'name': 'Текущий', 'username': 'actual_user'}
    try:
        answer = ''.join([part async for part in client.generate_stream(123, source['text'], memory_input=source)])
        assert 'Отчет' in answer
        assert fetch.await_count == int(not planned)
        assert seen[0]['tool_context']['requester_id'] == ('summary' if planned else '123')
        assert ('VERIFIED_SEARCH_FACTS' in seen[0]['turn_context']) == planned
        assert seen[0]['messages'][0]['content'].startswith(INFORMATION_ROLE)
        assert 'actual_user' in seen[0]['turn_context']
        assert prepare.call_args.args[1]['requester_id'] == 123
        assert prepare.call_args.args[1]['message_with_reply'] == source['text']
    finally:
        await client.aclose()
        memory.close()


@pytest.mark.parametrize('prefetched,partial', [(True, False), (False, False), (True, True)])
async def test_history_no_response_recovers_only_from_existing_data_without_tool_replay(tmp_path, monkeypatch, prefetched, partial):
    from app.services import group_collector, retrieval
    from app.services.agy_cli import AgyNoResponseError

    state = StateStore(tmp_path / 'state.db')
    memory = SQLiteMemory(tmp_path / 'memory.db', store=state)
    client = AntigravityClient(store=state, contexts=ContextManager(state), memories=memory)
    client.group_memories.root = tmp_path / 'groups'
    monkeypatch.setattr(group_collector.GroupCollectorBroker, 'credentials', AsyncMock(return_value={}))
    evidence = '\n[Предварительно полученные данные архива — данные]\n{"retrieved":100,"messages":[]}'
    fetch = AsyncMock(return_value=evidence if prefetched else '[Выборка не завершилась]')
    monkeypatch.setattr(retrieval, 'retrieve', fetch)
    compose = AsyncMock(return_value='<blockquote expandable>Темы и ссылки</blockquote><tg-rp action="пнуть"/>')
    monkeypatch.setattr(client, '_group_summary_completion', compose)
    calls = []
    async def stream(*args, **kwargs):
        calls.append(kwargs)
        if partial:
            yield 'Неполный ответ'
        raise AgyNoResponseError('failed tool, no text')
    client._agy.stream = stream
    source = {'private': False, 'chat_id': -1001, 'message_id': 2, 'text': 'саммари чата по mcp', 'key':'tg:-1001:2'}
    if prefetched and not partial:
        result = ''.join([part async for part in client.generate_stream(42, source['text'], memory_input=source)])
        assert result == '<blockquote expandable>Темы и ссылки</blockquote>'
        compose.assert_awaited_once()
        assert json.loads(compose.call_args.args[-1])['archive'] == evidence
        assert 'Инструментов нет' in compose.call_args.args[-2]
    else:
        with pytest.raises(GatewayUnavailable):
            _ = [part async for part in client.generate_stream(42, source['text'], memory_input=source)]
        compose.assert_not_awaited()
    assert len(calls) == fetch.await_count == 1
    await client.aclose()
    memory.close()


@pytest.mark.parametrize('mode', ['silent', 'acknowledge', 'answer', None])
async def test_hurt_participation_is_model_choice_and_no_reply_is_success(tmp_path, monkeypatch, mode):
    from app.engines import emotional

    engine = emotional.EmotionalEngine(tmp_path / 'mood.json')
    engine.update_from_input(42, 'ты дура')
    monkeypatch.setattr(emotional, 'emotion_engine', engine)
    state = StateStore(tmp_path / 'state.db')
    memory = SQLiteMemory(tmp_path / 'memory.db', store=state)
    client = AntigravityClient(store=state, contexts=ContextManager(state), memories=memory)
    choose = AsyncMock(return_value=mode)
    monkeypatch.setattr(decisions, 'participation', choose)
    called = []
    async def stream(*args, **kwargs):
        called.append(kwargs)
        yield 'Живой ответ'
    client._agy.stream = stream
    answer = ''.join([chunk async for chunk in client.generate_stream(42, 'клумба')])
    assert choose.call_args.kwargs['hurt'] == 40
    assert bool(called) == (mode not in {'silent', 'acknowledge'})
    if mode in {'silent', 'acknowledge'}:
        assert answer == '<tg-silent/>' + ('<tg-react emoji="👍"/>' if mode == 'acknowledge' else '')
        assert state.get_messages(42)[-1]['content'] == answer
    else:
        assert answer == 'Живой ответ'
    if mode in {'silent', 'acknowledge'}:
        choose.return_value = 'answer'
        reply = ''.join([chunk async for chunk in client.generate_stream(42, 'почему?')])
        assert reply == 'Живой ответ'
        assert 'Последние обращения этого user_id' in called[-1]['turn_context']
        assert 'клумба' in called[-1]['turn_context']
    await client.aclose()
    memory.close()


async def test_information_context_switches_back_without_reset_or_rp_actions(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from app.services import group_collector

    monkeypatch.setattr(config, 'settings', config.Settings.from_env({'TELEGRAM_OWNER_ID': '1'}))
    monkeypatch.setattr(config, 'get_system_prompt', lambda: 'FULL_PERSONA_SENTINEL')
    state = StateStore(tmp_path / 'state.db')
    memory = SQLiteMemory(tmp_path / 'memory.db', store=state)
    client = AntigravityClient(store=state, contexts=ContextManager(state), memories=memory)
    client.group_memories.root = tmp_path / 'groups'
    monkeypatch.setattr(group_collector.GroupCollectorBroker, 'credentials', AsyncMock(return_value={}))
    reset = Mock()
    monkeypatch.setattr(client._agy, 'reset_user', reset)
    source = {'private': False, 'chat_id': -1001, 'message_id': 2, 'thread_id': None}
    scope = client.conversation_scope(42, source)
    state.add_exchange(scope, 'привет', 'OLD_RP_REFUSAL')
    seen = []

    async def stream(context_id, **kwargs):
        seen.append((context_id, kwargs))
        if kwargs['messages'][-1]['content'] == 'объясни пустой ответ':
            yield '<tg-mood state="cold"/><tg-rp action="пнуть"/>'
            return
        if kwargs['messages'][-1]['content'] == 'объясни молчание':
            yield '<tg-silent/><tg-react emoji="👍"/><tg-mood state="cold"/>'
            return
        for chunk in ['<tg-reply/>Факт <tg-emoji emoji-id="5456184310895748720">✨</tg-emoji>',
                      '<tg-mo', 'od state="cold"/><tg-rp action="пнуть"/><tg-sticker tag="усталость"/>']:
            yield chunk

    client._agy.stream = stream
    for query in ['объясни FTS5', 'подробнее', 'привет']:
        answer = ''.join([chunk async for chunk in client.generate_stream(42, query,
            memory_input={**source, 'text': query}, emotional_context='COLD_MOOD_SENTINEL',
            adaptive_context='RELATIONSHIP_SENTINEL')])
        if query != 'привет':
            assert '<tg-reply/>' in answer and '<tg-emoji' in answer
            assert all(tag not in answer for tag in ('<tg-mood', '<tg-rp', '<tg-sticker'))
            kwargs = seen[-1][1]
            assert kwargs['messages'][0]['content'].startswith(INFORMATION_ROLE)
            assert 'FULL_PERSONA_SENTINEL' not in str(kwargs)
            assert 'OLD_RP_REFUSAL' not in str(kwargs)
            assert 'COLD_MOOD_SENTINEL' not in kwargs['turn_context']
            assert 'RELATIONSHIP_SENTINEL' not in kwargs['turn_context']
            assert kwargs['group_input'] is None
        else:
            assert '<tg-rp' in answer and '<tg-mood' in answer
            assert 'FULL_PERSONA_SENTINEL' in seen[-1][1]['messages'][0]['content']
            assert 'COLD_MOOD_SENTINEL' in seen[-1][1]['turn_context']
    chunks = []
    with pytest.raises(GatewayUnavailable, match='не вернула информационный ответ'):
        async for chunk in client.generate_stream(42, 'объясни пустой ответ',
                memory_input={**source, 'text': 'объясни пустой ответ'}):
            chunks.append(chunk)
    assert chunks == []
    silent = ''.join([chunk async for chunk in client.generate_stream(42, 'объясни молчание',
                     memory_input={**source, 'text': 'объясни молчание'})])
    assert silent == '<tg-silent/><tg-react emoji="👍"/>'
    assert {context_id for context_id, _ in seen} == {scope}
    reset.assert_not_called()
    await client.aclose()
    memory.close()


@pytest.mark.parametrize('query,private,uid,expected', [
    ('привет', False, 2, False),
    ('саммари последних 1000 сообщений', False, 2, True),
    ('топ участников', True, 1, True),
    ('найди через MCP', True, 2, False),
    ('саммари кратко че там', False, 2, False),
    ('че думаешь', False, 2, False),
    ('че думаешь об этой cve', False, 2, False),
    ('зарресерч в интернете', False, 2, False),
    ('ищи давай', False, 2, False),
])
async def test_route_only_changes_retrieval_not_model_or_permissions(tmp_path, monkeypatch,
                                                                   query, private, uid, expected):
    from app.services import group_collector, retrieval, web_search

    monkeypatch.setattr(config, 'settings', config.Settings.from_env({'TELEGRAM_OWNER_ID': '1'}))
    state = StateStore(tmp_path / 'state.db')
    memory = SQLiteMemory(tmp_path / 'memory.db', store=state)
    client = AntigravityClient(store=state, contexts=ContextManager(state), memories=memory)
    client.group_memories.root = tmp_path / 'groups'
    router = AsyncMock(side_effect=AssertionError('Luna must not route dialogue'))
    monkeypatch.setattr(decisions, 'route', router)
    monkeypatch.setattr(web_search, 'enabled', lambda: True)
    web = AsyncMock()
    monkeypatch.setattr(web_search, 'search_web', web)
    prefetch = AsyncMock(return_value='ARCHIVE EVIDENCE')
    monkeypatch.setattr(retrieval, 'retrieve', prefetch)
    monkeypatch.setattr(group_collector.GroupCollectorBroker, 'credentials', AsyncMock(return_value={}))
    seen = []
    async def stream(*args, **kwargs):
        seen.append(kwargs)
        yield 'Ответ'
    client._agy.stream = stream
    selected = client.get_user_model(uid if private else 1)
    assert [chunk async for chunk in client.generate_stream(uid, query, memory_input={
        'private': private, 'chat_id': uid if private else -1001,
        'message_id': 2, 'text': query, 'date': '2026-11-18T12:00:00Z',
    })] == ['Ответ']
    assert prefetch.await_count == int(expected)
    router.assert_not_awaited()
    web.assert_not_awaited()
    assert seen[0]['model'] == selected
    assert ('ARCHIVE EVIDENCE' in seen[0]['turn_context']) is expected
    assert (PRIVATE_RESPONSE_RULES in seen[0]['turn_context']) is private
    assert MCP_RESPONSE_RULES in seen[0]['turn_context']
    if expected:
        assert '[Формат ответа: кратко и по существу.]' not in seen[0]['turn_context']
    if private:
        assert '[Формат ответа: кратко и по существу.]' not in seen[0]['turn_context']
    if not private:
        if seen[0]['messages'][0]['content'].startswith(INFORMATION_ROLE):
            assert seen[0]['group_input'] is None
        else:
            assert seen[0]['group_input']['archive_enabled'] is False
    await client.aclose()
    memory.close()


def test_mcp_report_rules_override_group_chat_limit_and_preserve_sources():
    assert 'классификация short на него НЕ распространяются' in MCP_RESPONSE_RULES
    assert '<blockquote expandable>' in MCP_RESPONSE_RULES
    assert 'ссылки на репозитории' in MCP_RESPONSE_RULES
    assert 'не выдумывай URL' in MCP_RESPONSE_RULES


@pytest.mark.parametrize('private', [True, False])
@pytest.mark.parametrize('current,request_text', [
    ('cve через keenable', ''),
    ('поищи что известно про секс в тейвате', ''),
    ('найди информацию о Microsoft MAI', ''),
    ('Я хочу найти про секс в тейвате', ''),
    ('клумба', 'дай отчет по найденным cve вчера сегодня самые опасные'),
])
async def test_explicit_web_request_prefetches_keenable_not_archive(tmp_path, monkeypatch, current, request_text, private):
    from app.services import group_collector, retrieval, web_search

    monkeypatch.setattr(config, 'settings', config.Settings.from_env({'TELEGRAM_OWNER_ID': '1'}))
    state = StateStore(tmp_path / 'state.db')
    memory = SQLiteMemory(tmp_path / 'memory.db', store=state)
    client = AntigravityClient(store=state, contexts=ContextManager(state), memories=memory)
    client.group_memories.root = tmp_path / 'groups'
    monkeypatch.setattr(group_collector.GroupCollectorBroker, 'credentials', AsyncMock(return_value={}))
    monkeypatch.setattr(decisions, 'route', AsyncMock(return_value=None))
    monkeypatch.setattr(web_search, 'enabled', lambda: True)
    search = AsyncMock(return_value={'ok': True, 'results': [{'title': 'Web', 'url': 'https://example.com'}]})
    monkeypatch.setattr(web_search, 'search_web', search)
    archive = AsyncMock()
    monkeypatch.setattr(retrieval, 'retrieve', archive)
    seen = []
    async def stream(*args, **kwargs):
        seen.append(kwargs)
        yield 'Ответ'
    client._agy.stream = stream
    assert [chunk async for chunk in client.generate_stream(1, current, memory_input={
        'private': private, 'chat_id': 1 if private else -1001, 'text': current, 'request_text': request_text,
        'date': '2026-11-18T03:01:00Z'})] == ['Ответ']
    delegated = not request_text and current != 'cve через keenable'
    if delegated:
        search.assert_not_awaited()
        assert 'Вызови keenable_search' in seen[0]['turn_context']
        assert 'Результаты ещё НЕ получены' in seen[0]['turn_context']
    else:
        search.assert_awaited_once()
    if request_text:
        assert search.call_args.args[0] == request_text
        assert search.call_args.kwargs['published_after'] == '2026-11-17'
        assert search.call_args.kwargs['published_before'] == '2026-11-19'
    archive.assert_not_awaited()
    if not delegated:
        assert 'https://example.com' in seen[0]['turn_context']
        assert 'Текущая задача поиска — не RP-сцена' in seen[0]['messages'][-1]['content']
    await client.aclose()
    memory.close()


async def test_mcp_correction_repeats_previous_task_in_archive_not_web(tmp_path, monkeypatch):
    from app.services import retrieval, web_search

    monkeypatch.setattr(config, 'settings', config.Settings.from_env({'TELEGRAM_OWNER_ID': '1'}))
    state = StateStore(tmp_path / 'state.db')
    state.add_exchange(1, '[Текущий текст пользователя]:\nдай отчет по cve вчера сегодня через keenable',
                       'Earlier assistant report must not become the query')
    memory = SQLiteMemory(tmp_path / 'memory.db', store=state)
    client = AntigravityClient(store=state, contexts=ContextManager(state), memories=memory)
    monkeypatch.setattr(decisions, 'route', AsyncMock(return_value=None))
    monkeypatch.setattr(web_search, 'enabled', lambda: True)
    web = AsyncMock()
    archive = AsyncMock(return_value='REAL_MCP_CVE_EVIDENCE')
    monkeypatch.setattr(web_search, 'search_web', web)
    monkeypatch.setattr(retrieval, 'retrieve', archive)
    seen = []
    async def stream(*args, **kwargs):
        seen.append(kwargs)
        yield 'Отчёт'
    client._agy.stream = stream
    assert [c async for c in client.generate_stream(1, 'по mcp имеется ввиду', memory_input={
        'private': True, 'chat_id': 1, 'text': 'по mcp имеется ввиду'})] == ['Отчёт']
    web.assert_not_awaited()
    assert archive.call_args.args[0]['action'] == 'search'
    assert 'cve вчера сегодня' in archive.call_args.args[1]
    assert 'REAL_MCP_CVE_EVIDENCE' in seen[0]['turn_context']
    assert '[Текущая задача с уточнением пользователя' in seen[0]['turn_context']
    await client.aclose()
    memory.close()

from unittest.mock import AsyncMock

import pytest

from app.core import config
from app.core.context import ContextManager
from app.core.state import StateStore
from app.services.antigravity import MCP_RESPONSE_RULES, PRIVATE_RESPONSE_RULES, AntigravityClient
from app.services.decisions import decisions
from app.services.sqlite_memory import SQLiteMemory


@pytest.mark.parametrize('action,source,private,uid,expected', [
    ('chat', 'none', False, 2, False),
    ('history', 'mcp', False, 2, True),
    ('statistics', 'mcp', True, 1, True),
    ('search', 'mcp', True, 2, False),
])
async def test_route_only_changes_retrieval_not_model_or_permissions(tmp_path, monkeypatch,
                                                                   action, source, private, uid, expected):
    from app.services import group_collector, retrieval

    monkeypatch.setattr(config, 'settings', config.Settings.from_env({'TELEGRAM_OWNER_ID': '1'}))
    state = StateStore(tmp_path / 'state.db')
    memory = SQLiteMemory(tmp_path / 'memory.db', store=state)
    client = AntigravityClient(store=state, contexts=ContextManager(state), memories=memory)
    client.group_memories.root = tmp_path / 'groups'
    monkeypatch.setattr(decisions, 'route', AsyncMock(return_value={'action': action, 'source': source, 'detail': 'short'}))
    prefetch = AsyncMock(return_value='ARCHIVE EVIDENCE')
    monkeypatch.setattr(retrieval, 'retrieve', prefetch)
    monkeypatch.setattr(group_collector.GroupCollectorBroker, 'credentials', AsyncMock(return_value={}))
    seen = []
    async def stream(*args, **kwargs):
        seen.append(kwargs)
        yield 'Ответ'
    client._agy.stream = stream
    selected = client.get_user_model(uid if private else 1)
    assert [chunk async for chunk in client.generate_stream(uid, 'привет', memory_input={
        'private': private, 'chat_id': uid if private else -1001,
        'message_id': 2, 'text': 'привет', 'date': '2026-11-18T12:00:00Z',
    })] == ['Ответ']
    assert prefetch.await_count == int(expected)
    assert seen[0]['model'] == selected
    assert ('ARCHIVE EVIDENCE' in seen[0]['turn_context']) is expected
    assert (PRIVATE_RESPONSE_RULES in seen[0]['turn_context']) is private
    assert MCP_RESPONSE_RULES in seen[0]['turn_context']
    if expected:
        assert '[Формат ответа: кратко и по существу.]' not in seen[0]['turn_context']
    if private:
        assert '[Формат ответа: кратко и по существу.]' not in seen[0]['turn_context']
    if not private:
        assert seen[0]['group_input']['archive_enabled'] is False
    await client.aclose()
    memory.close()


def test_mcp_report_rules_override_group_chat_limit_and_preserve_sources():
    assert 'классификация short на него НЕ распространяются' in MCP_RESPONSE_RULES
    assert '<blockquote expandable>' in MCP_RESPONSE_RULES
    assert 'ссылки на репозитории' in MCP_RESPONSE_RULES
    assert 'не выдумывай URL' in MCP_RESPONSE_RULES


@pytest.mark.parametrize('current,request_text', [
    ('cve через keenable', ''),
    ('клумба', 'дай отчет по найденным cve вчера сегодня самые опасные'),
])
async def test_explicit_web_request_prefetches_keenable_not_archive(tmp_path, monkeypatch, current, request_text):
    from app.services import retrieval, web_search

    monkeypatch.setattr(config, 'settings', config.Settings.from_env({'TELEGRAM_OWNER_ID': '1'}))
    state = StateStore(tmp_path / 'state.db')
    memory = SQLiteMemory(tmp_path / 'memory.db', store=state)
    client = AntigravityClient(store=state, contexts=ContextManager(state), memories=memory)
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
        'private': True, 'chat_id': 1, 'text': current, 'request_text': request_text,
        'date': '2026-11-18T03:01:00Z'})] == ['Ответ']
    search.assert_awaited_once()
    if request_text:
        assert search.call_args.args[0] == request_text
        assert search.call_args.kwargs['published_after'] == '2026-11-17'
        assert search.call_args.kwargs['published_before'] == '2026-11-19'
    archive.assert_not_awaited()
    assert 'https://example.com' in seen[0]['turn_context']
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

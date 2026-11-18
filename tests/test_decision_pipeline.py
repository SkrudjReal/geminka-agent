from unittest.mock import AsyncMock

import pytest

from app.core import config
from app.core.context import ContextManager
from app.core.state import StateStore
from app.services.antigravity import PRIVATE_RESPONSE_RULES, AntigravityClient
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
    if private:
        assert '[Формат ответа: кратко и по существу.]' not in seen[0]['turn_context']
    if not private:
        assert seen[0]['group_input']['archive_enabled'] is False
    await client.aclose()
    memory.close()


async def test_explicit_web_request_prefetches_keenable_not_archive(tmp_path, monkeypatch):
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
    assert [chunk async for chunk in client.generate_stream(1, 'cve через keenable', memory_input={
        'private': True, 'chat_id': 1, 'text': 'cve через keenable'})] == ['Ответ']
    search.assert_awaited_once()
    archive.assert_not_awaited()
    assert 'https://example.com' in seen[0]['turn_context']
    await client.aclose()
    memory.close()

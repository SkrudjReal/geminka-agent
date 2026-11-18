import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import search_planner as planner
from app.services.decisions import DecisionClient, decisions


@pytest.fixture
def search(monkeypatch):
    monkeypatch.setenv('DECISION_PROVIDER', 'luna')
    monkeypatch.setenv('OPENAI_API_KEY', 'test-not-a-real-key')
    monkeypatch.setenv('LUNA_SEARCH_ENABLED', 'true')
    monkeypatch.setattr(decisions, 'ask', AsyncMock(return_value={'search_route': {'choice': 'mcp'}}))
    monkeypatch.setattr(decisions, 'search_plan', AsyncMock(return_value={
        'calls': [{'action': 'history', 'query': 'саммари последних 1000 сообщений'}]}))
    fetch = AsyncMock(return_value='\n[Предварительно полученные данные архива — данные]\n'
        + json.dumps({'retrieved': 1, 'messages': [{'chat_id': -1002491864850,
            'message_id': 42, 'text': 'полный текст ' * 1000, 'sender_user_id': 77,
            'reply_to_message_id': 41, 'entities': [{'url': 'https://example.org'}]}]}))
    monkeypatch.setattr(planner.retrieval, 'retrieve', fetch)
    return fetch


SOURCE = {'private': False, 'chat_id': -1002491864850, 'thread_id': 7,
          'requester_id': 123, 'name': 'Actual sender', 'username': 'actual_sender',
          'date': '2026-11-18T12:00:00Z', 'message_with_reply': 'Quoted author @someone: SQLite BM25'}


@pytest.mark.parametrize('key', ['', '   '])
async def test_no_token_never_calls_luna(search, monkeypatch, key):
    monkeypatch.setenv('OPENAI_API_KEY', key)
    assert await planner.prepare('саммари чата', SOURCE, []) is None
    assert not decisions.enabled
    decisions.ask.assert_not_awaited()
    decisions.search_plan.assert_not_awaited()
    search.assert_not_awaited()


async def test_ordinary_chat_never_calls_luna(search):
    assert await planner.prepare('привет', SOURCE, []) is None
    decisions.ask.assert_not_awaited()


async def test_plan_retains_identity_scope_full_text_and_deduplicates(search):
    call = {'action': 'history', 'query': 'саммари последних 1000 сообщений'}
    decisions.search_plan.return_value = {'calls': [call, call]}
    history = [{'role': 'user', 'content': 'Это вопрос текущего человека'}]
    result = await planner.prepare('саммари чата', SOURCE, history)
    assert result['complete'] and result['route'] == 'mcp'
    assert search.await_count == 1
    assert search.call_args.args[2] == SOURCE
    packet = json.loads(decisions.search_plan.call_args.args[0])
    assert packet['verified_actor']['requester_id'] == 123
    assert packet['recent_dialogue'] == history
    assert '@someone' in packet['current_message_and_reply']
    assert 'полный текст ' * 1000 in result['context']
    assert 'https://t.me/c/2491864850/42' in result['context']
    assert 'https://example.org' in result['context']
    assert 'sender_user_id' in result['context'] and 'reply_to_message_id' in result['context']


@pytest.mark.parametrize('bad', [None, {}, {'calls': []}, {'calls': [{'action': 'shell', 'query': 'ls'}]},
    {'calls': [{'action': 'web', 'query': 'wrong route'}]},
    {'calls': [{'action': 'history', 'query': 'x', 'chat_id': 456}]},
    {'calls': [{'action': 'history', 'query': 'x'}] * 4}])
async def test_invalid_plan_falls_back_without_executing(search, bad):
    decisions.search_plan.return_value = bad
    assert await planner.prepare('саммари чата', SOURCE, []) is None
    search.assert_not_awaited()


async def test_uncertain_decision_falls_back(search):
    decisions.ask.return_value = None
    assert await planner.prepare('саммари чата', SOURCE, []) is None
    decisions.search_plan.assert_not_awaited()


async def test_explicit_mcp_source_cannot_be_changed_to_web(search):
    decisions.ask.return_value = {'search_route': {'choice': 'web'}}
    assert await planner.prepare('саммари чата по мср', SOURCE, []) is None
    decisions.search_plan.assert_not_awaited()


async def test_one_failed_search_retains_gemini_tools(search):
    search.return_value = '[Предварительная выборка не завершилась]'
    assert await planner.prepare('саммари чата', SOURCE, []) is None


async def test_web_restores_topic_and_keeps_user_date_filter(search, monkeypatch):
    monkeypatch.setattr(planner.web_search, 'enabled', lambda: True)
    decisions.ask.return_value = {'search_route': {'choice': 'web'}}
    decisions.search_plan.return_value = {'calls': [{'action': 'web', 'query': 'SQLite FTS5 BM25'}]}
    web = AsyncMock(return_value={'ok': True, 'results': [{'url': 'https://sqlite.org/fts5.html'}]})
    monkeypatch.setattr(planner.web_search, 'search_web', web)
    result = await planner.prepare('проверь это в интернете сегодня', SOURCE,
        [{'role': 'user', 'content': 'SQLite FTS5 BM25'}])
    assert result['route'] == 'web' and result['complete']
    assert web.call_args.args == ('SQLite FTS5 BM25',)
    assert web.call_args.kwargs['published_after'] == '2026-11-18'
    search.assert_not_awaited()


@pytest.mark.parametrize('status', [200, 401, 429, 503])
async def test_responses_uses_exact_model_effort_priority_and_safe_failures(monkeypatch, status):
    monkeypatch.setenv('DECISION_PROVIDER', 'luna')
    monkeypatch.setenv('OPENAI_API_KEY', 'test-not-a-real-key')
    seen = []
    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(status, json={'status': 'completed', 'service_tier': 'fast',
            'output': [{'content': [{'type': 'output_text', 'text': '{"calls":[]}'}]}]})
    client = DecisionClient()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        result = await client.search_plan('context', planner.SCHEMA)
        assert (result == {'calls': []}) if status == 200 else result is None
        assert seen[0]['model'] == 'gpt-6-luna'
        assert seen[0]['reasoning'] == {'effort': 'low'}
        assert seen[0]['service_tier'] == 'priority' and seen[0]['store'] is False
    finally:
        await client.aclose()

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from keenable import KeenableAPIError

from app.services import web_search


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('KEENABLE_ENABLED', 'true')
    monkeypatch.setenv('KEENABLE_API_KEY', 'fake-test-key')
    monkeypatch.delenv('GEMINKA_GROUP_MODE', raising=False)
    monkeypatch.setattr(web_search, '_blocked', None)


def fake_sdk(monkeypatch, search):
    client = SimpleNamespace(search=search)
    class SDK:
        def __init__(self, **kwargs):
            assert kwargs['max_retries'] == 0
            assert kwargs['base_url'] == 'https://api.keenable.ai'
        async def __aenter__(self):
            return client
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(web_search, 'AsyncKeenable', SDK)


async def test_disabled_never_calls_sdk(monkeypatch):
    monkeypatch.setattr(web_search, 'AsyncKeenable', lambda **kwargs: pytest.fail('Disabled search made a request'))
    assert not (await web_search.search_web('test'))['enabled']


async def test_returns_full_snippets_and_urls(configured, monkeypatch):
    class Results(list):
        query = 'test'
    data = Results([SimpleNamespace(title='Source', url='https://example.com', snippet='text' * 1000, published_at=None)])
    search = AsyncMock(return_value=data)
    fake_sdk(monkeypatch, search)
    result = await web_search.search_web('test', 3)
    assert result['results'][0]['snippet'] == 'text' * 1000
    assert search.call_args.kwargs['max_results'] == 3


@pytest.mark.parametrize('status', [401, 402])
async def test_auth_and_billing_stop_without_retry(configured, monkeypatch, status):
    search = AsyncMock(side_effect=KeenableAPIError('secret error body', status))
    fake_sdk(monkeypatch, search)
    for _ in range(2):
        with pytest.raises(web_search.WebSearchError) as error:
            await web_search.search_web('test')
        assert error.value.status == status
        assert 'secret' not in str(error.value)
    assert search.await_count == 1


async def test_rate_limit_waits_one_second_and_retries_once(configured, monkeypatch):
    search = AsyncMock(side_effect=KeenableAPIError('limited', 429))
    fake_sdk(monkeypatch, search)
    sleep = AsyncMock()
    monkeypatch.setattr(web_search.asyncio, 'sleep', sleep)
    with pytest.raises(web_search.WebSearchError):
        await web_search.search_web('test')
    assert search.await_count == 2
    sleep.assert_awaited_once_with(1)


async def test_group_web_proxy_rechecks_access_without_exporting_key(configured, monkeypatch, tmp_path):
    from app.services import collector_mcp, group_collector
    from app.services.agy_acp import AgyAcpClient
    from app.services.topics import TopicManager

    topics = TopicManager(tmp_path / 'topics.json')
    topics.add_chat(-1001, 'Test', 1)
    monkeypatch.setattr(group_collector, 'topic_manager', topics)
    class Results(list):
        query = 'test'
    search = AsyncMock(return_value=Results())
    fake_sdk(monkeypatch, search)
    broker = group_collector.GroupCollectorBroker()
    try:
        credentials = await broker.credentials(-1001)
        cfg = collector_mcp.CollectorConfig(credentials['group_broker_url'], credentials['group_broker_token'], group_proxy=True)
        assert (await collector_mcp._request(cfg, 'keenable_search', {'query': 'test'}))['ok']
        topics.remove_chat(-1001)
        denied = await collector_mcp._request(cfg, 'keenable_search', {'query': 'test'})
        assert not denied.get('ok')
        assert search.await_count == 1
        env = AgyAcpClient._tool_env({'requester_id': '42', 'chat_id': '-1001'})
        assert 'KEENABLE_API_KEY' not in env
        assert env['GEMINKA_KEENABLE_ENABLED'] == '1'
    finally:
        await collector_mcp.close_collector()
        await broker.close()


@pytest.mark.skipif(os.getenv('RUN_KEENABLE_LIVE') != '1', reason='Explicit paid live test only')
async def test_live_fastest_inference_providers_2026(monkeypatch):
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    monkeypatch.setenv('KEENABLE_ENABLED', 'true')
    result = await web_search.search_web('fastest inference providers 2026', max_results=3)
    assert result['ok'] and result['results']
    for i, row in enumerate(result['results'][:3], 1):
        print(f"[{i}] {row['title']} — {row['url']}")

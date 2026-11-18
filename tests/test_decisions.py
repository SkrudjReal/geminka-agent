import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.decisions import DecisionClient, choice


async def test_participation_is_narrow_and_preserves_abstention():
    from unittest.mock import AsyncMock

    client = DecisionClient()
    client.ask = AsyncMock(return_value={'participation': {'choice': 'silent'}})
    assert await client.participation('клумба', user_id=42, hurt=80, apologies=0, history=[]) == 'silent'
    question = client.ask.call_args.args[1][0]
    assert {item['value'] for item in question['choices']} == {'silent', 'answer', 'acknowledge'}
    client.ask.return_value = None
    assert await client.participation('клумба', user_id=42, hurt=80, apologies=0, history=[]) is None
    await client.aclose()


def client_for(monkeypatch, handler, provider='luna'):
    monkeypatch.setenv('DECISION_PROVIDER', provider)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-not-a-secret')
    client = DecisionClient()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.loop = asyncio.get_running_loop()
    return client


async def test_luna_route_schema_cache_and_no_identity_changes(monkeypatch):
    calls = []
    def reply(request):
        body = json.loads(request.content)
        calls.append(body)
        assert request.url.path == '/v1/decisions'
        assert body['model'] == 'gpt-6-luna'
        return httpx.Response(200, json={'answers': [
            {'type': 'choice', 'name': name, 'choice': value, 'confidence': .95}
            for name, value in [('action', 'chat'), ('source', 'none'), ('detail', 'short')]]})
    client = client_for(monkeypatch, reply)
    assert (await client.route('Привет', scope='user1'))['source'] == 'none'
    await client.route('Привет', scope='user1')
    assert len(calls) == 1
    await client.route('Привет', scope='user2')
    assert len(calls) == 2
    await client.aclose()


@pytest.mark.parametrize('answer', [
    {'type': 'refusal', 'name': 'x'},
    {'type': 'choice', 'name': 'x', 'choice': 'evil', 'confidence': .99},
    {'type': 'choice', 'name': 'x', 'choice': 'yes', 'confidence': .2},
    {'type': 'choice', 'name': 'other', 'choice': 'yes', 'confidence': .99},
])
async def test_invalid_or_uncertain_is_not_a_decision(monkeypatch, answer):
    client = client_for(monkeypatch, lambda _: httpx.Response(200, json={'answers': [answer]}))
    assert await client.ask('hi', [choice('x', 'pick', {'yes': '', 'no': ''})]) is None
    await client.aclose()


async def test_laya_wire_protocol_and_abstention(monkeypatch):
    def reply(request):
        data = json.loads(request.content)
        assert request.url.path == '/v1/systemone'
        assert data['model'] == 'multilingual'
        assert data['state'] == 'привет'
        assert data['questions']['x']['criteria'] == {'yes': '', 'no': ''}
        return httpx.Response(200, json={'answers': {'x': {'type': 'choice', 'choice': 'yes',
            'answer_confidence': .95}}, 'usage': {}})
    client = client_for(monkeypatch, reply, 'laya')
    q = [choice('x', 'pick', {'yes': '', 'no': ''})]
    assert (await client.ask('привет', q))['x']['choice'] == 'yes'
    assert await client.approve_image(None, candidates=1) is None
    await client.aclose()


async def test_timeout_and_auth_error_do_not_block_chat(monkeypatch):
    calls = []
    def broken(request):
        calls.append(1)
        return httpx.Response(401, json={'error': 'secret must not be logged'})
    client = client_for(monkeypatch, broken)
    assert await client.route('привет') is None
    assert await client.route('ещё') is None
    assert len(calls) == 1
    await client.aclose()


async def test_slow_provider_has_total_deadline(monkeypatch):
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={})
    client = client_for(monkeypatch, slow)
    assert await client.ask('hi', [choice('x', 'pick', {'a': '', 'b': ''})], timeout=.01) is None
    await client.aclose()


async def test_laya_abstention_and_truncated_input_fall_back(monkeypatch):
    for data in [
        {'answers': {'x': {'type': 'choice', 'choice': 'yes', 'confidence': .99, 'abstain': True}}},
        {'answers': {}, 'usage': {'state_tokens_dropped': 3}},
    ]:
        client = client_for(monkeypatch, lambda _, data=data: httpx.Response(200, json=data), 'laya')
        assert await client.ask('hi', [choice('x', 'pick', {'yes': '', 'no': ''})]) is None
        await client.aclose()


async def test_observer_skips_and_image_predicates(monkeypatch, tmp_path):
    client = DecisionClient()
    client.ask = AsyncMock(return_value={'target': {'choice': 'skip'}})
    assert await client.observer_target([{'chat_id': -1, 'message_id': 2, 'eligible': True}]) == 0
    monkeypatch.setenv('DECISION_PROVIDER', 'luna')
    monkeypatch.setenv('OPENAI_API_KEY', 'test')
    path = tmp_path / 'image.jpg'
    path.write_bytes(b'pixels')
    client.ask = AsyncMock(return_value={'1': {'probability': .98}, '2': {'probability': .1}})
    assert await client.approve_image(path, candidates=2) == [1]
    assert client.ask.call_args.kwargs['images'][0].startswith('data:image/jpeg;base64,')


async def test_reranking_preserves_all_full_messages():
    client = DecisionClient()
    rows = [{'text': str(i) + 'x' * 2000} for i in range(8)]
    client.ask = AsyncMock(return_value={str(i): {'score': i / 4} for i in range(8)})
    ranked = await client.rank_messages('topic', rows)
    assert ranked == list(reversed(rows))
    for row in rows:
        assert row['text'] in client.ask.call_args.args[0]
    client.ask = AsyncMock(return_value=None)
    assert await client.rank_messages('topic', rows) == rows

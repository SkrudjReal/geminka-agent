from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot import guest
from app.services.antigravity import AntigravityClient


@pytest.fixture(autouse=True)
def reset_public():
    guest._busy.clear()
    guest._seen.clear()
    yield
    guest._busy.clear()
    guest._seen.clear()


def message(uid=11, text='привет @columbina_ai_bot', key='guest-id', original=None):
    return SimpleNamespace(guest_query_id=key, from_user=SimpleNamespace(id=uid, is_bot=False),
                           text=text, caption=None, reply_to_message=original)


def test_guest_has_no_inline_handler_or_buttons():
    assert guest.router.resolve_used_update_types() == ['guest_message']
    article = guest.answer_article('Ответ <3')
    assert article.reply_markup is None
    assert article.input_message_content.message_text == 'Ответ &lt;3'
    assert article.input_message_content.link_preview_options.is_disabled


def test_guest_preserves_html_quotes_and_premium_emoji():
    text = ('<b>Привет!</b> <tg-emoji emoji-id="5456184310895748720">✨</tg-emoji>'
            '\n<blockquote expandable>Цитата</blockquote>')
    content = guest.answer_article(text).input_message_content
    assert content.parse_mode == 'HTML'
    assert content.message_text == text


def test_guest_formats_markdown_without_exposing_internal_control_tags():
    content = guest.answer_article(
        '<think>hidden reasoning</think>**Привет**<tg-mood state="playful" delta="5"/>'
    ).input_message_content
    assert content.message_text == '<b>Привет</b>'


async def test_guest_replies_via_query_id_not_send_message_or_chat_membership():
    client = SimpleNamespace(inline_completion=AsyncMock(return_value='Привет!'))
    bot = SimpleNamespace(answer_guest_query=AsyncMock())
    await guest.guest_message(message(), bot, client)
    args = bot.answer_guest_query.call_args.args
    assert args[0] == 'guest-id' and args[1].reply_markup is None
    assert args[1].input_message_content.message_text == 'Привет!'
    assert guest.router.resolve_used_update_types() == ['guest_message']
    await guest.guest_message(message(), bot, client)
    client.inline_completion.assert_awaited_once()
    bot.answer_guest_query.assert_awaited_once()


async def test_guest_uses_only_supplied_reply_context():
    client = SimpleNamespace(inline_completion=AsyncMock(return_value='answer'))
    bot = SimpleNamespace(answer_guest_query=AsyncMock())
    original = SimpleNamespace(text='original context', caption=None)
    await guest.guest_message(message(original=original), bot, client)
    uid, prompt = client.inline_completion.call_args.args
    assert uid == 11 and 'original context' in prompt and '@columbina_ai_bot' in prompt
    import json

    identity = json.loads(client.inline_completion.call_args.kwargs['identity'])
    assert identity['user_id'] == 11


async def test_guest_can_choose_actual_silence_instead_of_ellipsis():
    client = SimpleNamespace(inline_completion=AsyncMock(return_value='<tg-silent/>'))
    bot = SimpleNamespace(answer_guest_query=AsyncMock())
    await guest.guest_message(message(), bot, client)
    bot.answer_guest_query.assert_not_awaited()
    assert not guest._busy


async def test_guest_uses_actual_caller_not_forwarded_sender():
    client = SimpleNamespace(inline_completion=AsyncMock(return_value='answer'))
    bot = SimpleNamespace(answer_guest_query=AsyncMock())
    event = message(uid=10002)
    event.guest_bot_caller_user = SimpleNamespace(id=10001, username='example_user',
                                                  full_name='Example User', is_bot=False)
    await guest.guest_message(event, bot, client)
    assert client.inline_completion.call_args.args[0] == 10001
    assert 'example_user' in client.inline_completion.call_args.kwargs['identity']
    assert '10002' not in client.inline_completion.call_args.kwargs['identity']


async def test_uncertain_guest_send_is_not_replayed():
    client = SimpleNamespace(inline_completion=AsyncMock(return_value='answer'))
    bot = SimpleNamespace(answer_guest_query=AsyncMock(side_effect=RuntimeError('uncertain delivery')))
    with pytest.raises(RuntimeError):
        await guest.guest_message(message(), bot, client)
    await guest.guest_message(message(), bot, client)
    bot.answer_guest_query.assert_awaited_once()
    assert not guest._busy


async def test_guest_rejects_missing_query_id_and_bot_loops():
    client = SimpleNamespace(inline_completion=AsyncMock())
    bot = SimpleNamespace(answer_guest_query=AsyncMock())
    await guest.guest_message(message(key=None), bot, client)
    msg = message()
    msg.from_user.is_bot = True
    await guest.guest_message(msg, bot, client)
    client.inline_completion.assert_not_called()
    bot.answer_guest_query.assert_not_called()


async def test_guest_busy_sender_does_not_launch_more_generations():
    guest._busy.add(11)
    client = SimpleNamespace(inline_completion=AsyncMock())
    bot = SimpleNamespace(answer_guest_query=AsyncMock())
    await guest.guest_message(message(), bot, client)
    client.inline_completion.assert_not_called()
    bot.answer_guest_query.assert_awaited_once()


async def test_public_completion_uses_isolated_no_mcp_profile_and_no_history(monkeypatch):
    client = AntigravityClient.__new__(AntigravityClient)
    seen = []

    async def stream(*args, **kwargs):
        seen.append((args, kwargs))
        yield 'x' * 10000

    client._agy = SimpleNamespace(stream=stream)
    client.get_user_model = lambda uid: 'gemini-3.8-flash'
    monkeypatch.setattr('app.services.antigravity.config.get_system_prompt', lambda: 'PERSONA')
    result = await client.inline_completion(11, 'hello')
    assert len(result) == 3000
    params = seen[0][1]
    assert params['debug'] is True
    assert params['tool_context'] == {'requester_id': 'summary', 'chat_id': str(-10**18 - 11)}
    assert len(params['messages']) == 2 and params['messages'][1]['content'] == 'hello'
    assert 'premium <tg-emoji>' in params['messages'][0]['content']
    assert 'user_id=11' in params['messages'][0]['content']
    assert 'только пример разметки' in params['messages'][0]['content']
    assert 'Use plain text only' not in params['messages'][0]['content']


@pytest.mark.parametrize('mode', ['silent', 'acknowledge'])
async def test_guest_participation_preserves_personal_distance_without_private_evidence(tmp_path, monkeypatch, mode):
    from app.engines import emotional
    from app.services.decisions import decisions

    engine = emotional.EmotionalEngine(tmp_path / 'mood.json')
    engine.update_from_input(42, 'ты дура')
    monkeypatch.setattr(emotional, 'emotion_engine', engine)
    choose = AsyncMock(return_value=mode)
    monkeypatch.setattr(decisions, 'participation', choose)
    client = AntigravityClient.__new__(AntigravityClient)
    client._agy = SimpleNamespace(stream=AsyncMock())
    assert await client.inline_completion(42, 'клумба') == '<tg-silent/>'
    client._agy.stream.assert_not_called()
    assert choose.call_args.kwargs['history'] == []
    assert 'ты дура' not in str(choose.call_args)

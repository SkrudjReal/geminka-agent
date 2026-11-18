from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, Sticker, User

from app.bot import helpers


async def test_reply_context_has_exact_message_dates_and_elapsed_seconds():
    chat = Chat(id=123, type='private')
    user = User(id=42, first_name='User', is_bot=False)
    original = Message(message_id=10, date=datetime(2026, 11, 18, 5, 25, tzinfo=timezone.utc),
                       chat=chat, from_user=user, text='Договорились')
    current = Message(message_id=11, date=datetime(2026, 11, 18, 6, 25, tzinfo=timezone.utc),
                      chat=chat, from_user=user, text='Час прошёл', reply_to_message=original)
    result = await helpers.extract_message_context(current, SimpleNamespace())
    assert '2026-11-18T05:25:00+00:00' in result
    assert '2026-11-18T06:25:00+00:00' in result
    assert '3600 секунд' in result
    assert result.endswith('Час прошёл')


@pytest.mark.parametrize('forum', [False, True])
async def test_reply_thread_only_isolates_real_forum_topics(monkeypatch, forum):
    from app.bot import handlers
    from app.services.antigravity import AntigravityClient

    message = Message(message_id=11, date=0, chat=Chat(id=-1001, type='supergroup'),
                      from_user=User(id=42, first_name='User', is_bot=False),
                      text='клумба ищи давай', message_thread_id=319514, is_topic_message=forum)
    monkeypatch.setattr(handlers, 'state_store', SimpleNamespace(is_debug_mode=lambda _: True))
    monkeypatch.setattr(handlers, 'group_message_allowed', lambda *_: True)
    monkeypatch.setattr(handlers, 'extract_message_context', AsyncMock(return_value='ищи давай'))
    monkeypatch.setattr(handlers.topic_manager, 'is_topic_active', lambda *_: False)
    monkeypatch.setattr(handlers.topic_manager, 'strip_prefix', lambda value: value.removeprefix('клумба').strip())
    monkeypatch.setattr(handlers.asset_harvester, 'format_emojis_prompt_context', lambda _: '')
    prepared = await handlers._prepare_message(message, SimpleNamespace(id=99), None)
    source = prepared['memory_input']
    assert source['thread_id'] == (319514 if forum else None)
    original = {**source, 'thread_id': None}
    assert (AntigravityClient.conversation_scope(42, source)
            == AntigravityClient.conversation_scope(42, original)) is (not forum)


async def test_full_replied_post_keeps_tail_and_current_request():
    chat = Chat(id=123, type='private')
    user = User(id=456, is_bot=False, first_name='User')
    post = 'Microsoft MAI ' + 'material ' * 800 + 'IMPORTANT_POST_TAIL'
    quoted = Message(message_id=10, date=0, chat=chat, from_user=user, text=post)
    message = Message(message_id=11, date=0, chat=chat, from_user=user,
                      text='саммари кратко че там', reply_to_message=quoted)
    context = await helpers.extract_message_context(message, SimpleNamespace())
    assert post in context
    assert context.endswith('саммари кратко че там')


async def test_captionless_video_does_not_inherit_request_or_identity_from_quote(monkeypatch):
    from aiogram.types import Video

    from app.bot import handlers
    from app.services.retrieval import explicit_plan

    chat = Chat(id=-1001, type='supergroup')
    user = User(id=42, first_name='In-san', is_bot=False)
    quoted = Message(message_id=10, date=0, chat=chat, from_user=user,
                     text='@other_owner сделай саммари чата по mcp')
    message = Message(message_id=11, date=0, chat=chat, from_user=user, reply_to_message=quoted,
                      video=Video(file_id='v', file_unique_id='v', width=32, height=32, duration=2))
    monkeypatch.setattr(handlers, 'state_store', SimpleNamespace(is_debug_mode=lambda _: True))
    monkeypatch.setattr(handlers, 'group_message_allowed', lambda *_: True)
    monkeypatch.setattr(handlers, 'extract_message_context', AsyncMock(return_value='VIDEO'))
    monkeypatch.setattr(handlers.asset_harvester, 'format_emojis_prompt_context', lambda _: '')
    result = await handlers._prepare_message(message, SimpleNamespace(id=99), None)
    assert 'медиа (video)' in result['memory_input']['request_text']
    assert explicit_plan(result['memory_input']['request_text'], None) is None
    assert result['memory_input']['name'] == 'In-san'


@pytest.mark.parametrize('text,author,expected', [
    ('клумба', 42, 'дай отчет по найденным cve вчера сегодня самые опасные'),
    ('клумба', 43, ''),
    ('клумба привет', 42, 'привет'),
])
async def test_bare_invocation_routes_own_replied_request_only(monkeypatch, text, author, expected):
    from app.bot import handlers

    chat = Chat(id=-1001, type='supergroup')
    quoted = Message(message_id=10, date=0, chat=chat,
                     from_user=User(id=author, first_name='Other', is_bot=False),
                     text='дай отчет по найденным cve вчера сегодня самые опасные')
    message = Message(message_id=11, date=0, chat=chat,
                      from_user=User(id=42, first_name='User', is_bot=False),
                      text=text, reply_to_message=quoted)
    monkeypatch.setattr(handlers, 'state_store', SimpleNamespace(is_debug_mode=lambda _: True))
    monkeypatch.setattr(handlers, 'group_message_allowed', lambda *_: True)
    monkeypatch.setattr(handlers, 'extract_message_context', AsyncMock(return_value='quoted request and current text'))
    monkeypatch.setattr(handlers.topic_manager, 'is_topic_active', lambda *_: False)
    monkeypatch.setattr(handlers.topic_manager, 'strip_prefix', lambda value: value.removeprefix('клумба').strip())
    monkeypatch.setattr(handlers.asset_harvester, 'format_emojis_prompt_context', lambda _: '')
    prepared = await handlers._prepare_message(message, SimpleNamespace(id=99), None)
    assert prepared['memory_input']['request_text'] == expected
    assert prepared['memory_input']['text'] == text
    assert prepared['memory_input']['name'] == 'User'


@pytest.mark.asyncio
async def test_reply_to_known_sticker_uses_json_without_download(monkeypatch) -> None:
    sticker = Sticker(
        file_id="sticker-file-id",
        file_unique_id="sticker-unique-id",
        type="regular",
        width=512,
        height=512,
        is_animated=False,
        is_video=False,
        emoji="❤️",
        set_name="ExamplePack",
    )
    quoted = Message(
        message_id=10,
        date=datetime.now(timezone.utc),
        chat=Chat(id=123, type="private"),
        from_user=User(id=896, is_bot=True, first_name="Columbina"),
        sticker=sticker,
    )
    message = Message(
        message_id=11,
        date=datetime.now(timezone.utc),
        chat=Chat(id=123, type="private"),
        from_user=User(id=456, is_bot=False, first_name="User"),
        text="Что это?",
        reply_to_message=quoted,
    )

    monkeypatch.setattr(
        helpers.asset_harvester,
        "get_sticker_metadata",
        lambda *_args, **_kwargs: {
            "description": "Персонаж смотрит с укором",
            "tags": ["осуждение"],
        },
    )

    class _Bot:
        async def get_file(self, _file_id):
            raise AssertionError("known sticker must not be downloaded")

    context = await helpers.extract_message_context(message, _Bot())

    assert "Персонаж смотрит с укором" in context
    assert "Что это?" in context

from unittest.mock import AsyncMock

import pytest
from aiogram.methods import SendMediaGroup, SendMessage
from aiogram.types import InputMediaPhoto, MessageEntity

from app.bot.chat_mute import block_muted_delivery
from app.bot.guest import answer_article
from app.services.public_output import SAFE_REPLY, protect_method, safe_public_text


@pytest.mark.parametrize('text', [
    '[план](file:///home/user/.geminka/brain/plan.md)',
    '<a href="file:///home/user/plan.md">план</a>',
    'Путь /home/user/project/config.py',
    '/tmp/private.log',
    'C:\\Users\\user\\secret.txt',
    'file%253A%252F%252F%252Fhome%252Fuser',
    'file&#58;///home/user/secret',
    '~/.geminka/instances/test',
    'fi\u200ble:///root/config',
    r'\/home\/user\/private',
])
def test_local_paths_are_not_published(text):
    assert safe_public_text(text) == SAFE_REPLY


@pytest.mark.parametrize('text', ['Привет))', 'https://t.me/username?profile',
                               '<blockquote>Цитата</blockquote>', 'Открой /mood',
                               'https://example.com/docs/guide'])
def test_regular_chat_and_public_links_are_unchanged(text):
    assert safe_public_text(text) == text


def test_entity_links_and_album_captions_are_protected():
    method = SendMessage(chat_id=-1, text='план', entities=[
        MessageEntity(type='text_link', offset=0, length=4, url='file:///home/private')])
    safe = protect_method(method)
    assert safe.text == SAFE_REPLY
    assert safe.entities is None
    album = SendMediaGroup(chat_id=-1, media=[InputMediaPhoto(media='id', caption='/home/user/private')])
    assert protect_method(album).media[0].caption == SAFE_REPLY
    assert method.text == 'план'


def test_guest_output_is_also_protected():
    assert answer_article('file:///home/private').input_message_content.message_text == SAFE_REPLY


@pytest.mark.asyncio
async def test_public_transport_sanitizes_but_keeps_owner_private_output():
    send = AsyncMock()
    await block_muted_delivery(send, None, SendMessage(chat_id=-987654321, text='file:///home/private'))
    assert send.call_args.args[1].text == SAFE_REPLY
    await block_muted_delivery(send, None, SendMessage(chat_id=123, text='file:///home/private'))
    assert send.call_args.args[1].text == 'file:///home/private'


@pytest.mark.parametrize('encoding', ['plain', 'base64', 'hex', 'url'])
def test_configured_secrets_are_blocked_without_logging_them(monkeypatch, caplog, encoding):
    import base64
    from urllib.parse import quote

    value = 'synthetic-test-secret-0123456789'
    monkeypatch.setenv('COLLECTOR_API_TOKEN', value)
    text = {'plain': value, 'base64': base64.b64encode(value.encode()).decode(),
            'hex': value.encode().hex(), 'url': quote(value, safe='')}[encoding]
    assert safe_public_text(text) == SAFE_REPLY
    assert value not in caplog.text
    assert text not in caplog.text


@pytest.mark.parametrize('text', [
    '123456789:' + 'A' * 35,
    'ghp_' + 'a' * 36,
    'sk-proj-' + 'a' * 30,
    '-----BEGIN OPENSSH PRIVATE KEY-----',
    'https://user:pass@example.com/upload',
])
def test_credentials_are_blocked_even_when_not_in_environment(text):
    assert safe_public_text(text) == SAFE_REPLY


def test_sensitive_media_url_is_rejected(monkeypatch):
    from aiogram.methods import SendPhoto

    monkeypatch.setenv('PRIVATE_TOKEN', 'synthetic-private-token-0123456789')
    with pytest.raises(ValueError, match='Sensitive media URL'):
        protect_method(SendPhoto(chat_id=-1, photo='https://example.com/synthetic-private-token-0123456789'))
    with pytest.raises(ValueError, match='Sensitive media URL'):
        protect_method(SendMediaGroup(chat_id=-1, media=[InputMediaPhoto(
            media='https://example.com/synthetic-private-token-0123456789')]))


def test_sensitive_quote_and_button_are_not_published(monkeypatch):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters

    secret = 'synthetic-private-token-0123456789'
    monkeypatch.setenv('PRIVATE_TOKEN', secret)
    method = SendMessage(chat_id=-1, text='test',
                         reply_parameters=ReplyParameters(message_id=1, quote=secret),
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                             InlineKeyboardButton(text='open', url='https://example.com/' + secret)]]))
    result = protect_method(method)
    assert result.reply_parameters.quote is None
    assert result.reply_markup is None

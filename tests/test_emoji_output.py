import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.emoji_output import plain_without_emoji, premium_only_text

CATALOG = [{'custom_emoji_id': '111', 'emoji': '😉'}, {'custom_emoji_id': '222', 'emoji': '✨'}]


def test_converts_known_emoji_and_removes_unknown():
    result = premium_only_text('Мальчик 🌟 Пф-ф 😉 Готово ✨', catalog=CATALOG)
    assert result == 'Мальчик  Пф-ф <tg-emoji emoji-id="111">😉</tg-emoji> Готово <tg-emoji emoji-id="222">✨</tg-emoji>'
    assert premium_only_text(result, catalog=CATALOG) == result


@pytest.mark.parametrize('symbol', ['👨‍👩‍👧‍👦', '🇷🇺', '👍🏽', '1️⃣', '❤️', '🫠'])
def test_removes_complete_sequences_without_damaging_text(symbol):
    assert premium_only_text('До ' + symbol + ' после 123 + 4 = 127 ))', catalog=[]) == 'До  после 123 + 4 = 127 ))'


def test_protects_code_links_reaction_and_valid_tags():
    source = ('`😉` ```py\nprint("😉")\n``` <code>😉</code> <pre>🌟</pre> '
              '<a href="https://example.com/😉">😉</a> <tg-react emoji="❤"/>'
              '<tg-emoji emoji-id=222>✨</tg-emoji>')
    result = premium_only_text(source, catalog=CATALOG)
    assert '`😉`' in result and 'print("😉")' in result and '<code>😉</code>' in result
    assert 'href="https://example.com/😉"' in result and '<pre>🌟</pre>' in result
    assert '<tg-react emoji="❤"/>' in result
    assert '<a href="https://example.com/😉"><tg-emoji emoji-id="111">😉</tg-emoji></a>' in result
    assert '<tg-emoji emoji-id="222">✨</tg-emoji>' in result


def test_disallowed_custom_emoji_is_not_downgraded_to_plain_unicode():
    result = premium_only_text('<tg-emoji emoji-id="999">😉</tg-emoji> 🌟', catalog=CATALOG)
    assert '999' not in result and 'emoji-id="111"' in result
    assert not re.sub(r'<tg-emoji[^>]*>.*?</tg-emoji>', '', result).strip()


def test_plain_fallback_removes_premium_wrappers_and_unicode():
    assert plain_without_emoji('<b>Привет</b> <tg-emoji emoji-id="111">😉</tg-emoji> 🌟') == 'Привет'
    assert plain_without_emoji('🌟') == '…'


async def test_initial_delivery_fallback_does_not_leak_plain_smileys():
    from app.services.streamer import TelegramStreamConsumer

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[ValueError('bad HTML'), SimpleNamespace(message_id=1)]))
    consumer = TelegramStreamConsumer(bot, chat_id=-1001, user_id=1)
    consumer.emoji_catalog = CATALOG
    consumer.allowed_emojis = {'111', '222'}
    assert await consumer._safe_send_initial('Привет 😉')
    fallback = bot.send_message.call_args.args[1]
    assert '😉' not in fallback and '<tg-emoji' not in fallback

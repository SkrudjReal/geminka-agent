from datetime import datetime, timezone

import pytest
from aiogram.types import Chat, Message, Sticker, User

from app.bot import helpers


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

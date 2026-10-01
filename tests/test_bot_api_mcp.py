from types import SimpleNamespace

import pytest

from app.core import config
from app.services import bot_api_mcp


@pytest.mark.asyncio
async def test_channel_send_stages_photo_reply_then_forward(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        config,
        "settings",
        config.Settings.from_env(
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "TELEGRAM_ALLOWED_USERS": "42",
                "TELEGRAM_OWNER_ID": "42",
            }
        ),
    )
    bot_api_mcp._PENDING_CHANNEL_POSTS.clear()
    bot_api_mcp._POST_PREVIEWS.clear()
    calls: list[tuple[str, dict]] = []
    preview_dir = tmp_path / "preview"
    preview_dir.mkdir()
    image_path = preview_dir / "candidate.jpg"
    image_path.write_bytes(b"test image")
    bot_api_mcp._POST_PREVIEWS["preview-1"] = {
        "channel_id": "@channel",
        "preview_dir": preview_dir,
        "candidates": [
            {
                "index": 2,
                "url": "https://images.example/post.jpg",
                "path": str(image_path),
                "query": "Columbina art",
            }
        ],
    }

    class FakeBot:
        async def get_chat(self, chat_id):
            assert chat_id == "@channel"
            return SimpleNamespace(type="channel", title="Channel")

    async def fake_call(bot, *, method, params, requester_id, default_chat_id=None):
        calls.append((method, dict(params)))
        return {"message_id": 77, "text": params.get("text", "")}

    monkeypatch.setattr(bot_api_mcp, "call_bot_api", fake_call)
    monkeypatch.setattr(bot_api_mcp, "_record_post_image", lambda *_: None)
    bot = FakeBot()

    draft = await bot_api_mcp._invoke(
        bot,
        "send_message",
        {
            "chat_id": "@channel",
            "text": "post #tag1 #tag2 #tag3",
            "parse_mode": "HTML",
            "post_preview_id": "preview-1",
            "post_image_index": 2,
        },
    )
    assert draft["channel_delivery"] == "draft_created"
    assert calls[0][0] == "send_photo"
    assert calls[0][1]["chat_id"] == 42
    assert calls[0][1]["caption"].endswith(
        '<tg-emoji emoji-id="5300994163100119559">🌸</tg-emoji> '
        "➢ @channel | #tag1 #tag2 #tag3"
    )

    reply = await bot_api_mcp._invoke(
        bot,
        "send_message",
        {
            "chat_id": 42,
            "reply_parameters": {"message_id": 77},
            "text": "thoughts",
        },
    )
    assert reply["channel_delivery"] == "commentary_created"

    forwarded = await bot_api_mcp._invoke(
        bot,
        "forward_message",
        {"chat_id": "@channel", "from_chat_id": 42, "message_id": 77},
    )
    assert forwarded["channel_delivery"] == "forwarded"
    assert [method for method, _ in calls] == [
        "send_photo",
        "send_message",
        "forward_message",
    ]
    assert not bot_api_mcp._PENDING_CHANNEL_POSTS

import pytest

from app.services.streamer import TelegramStreamConsumer, md_to_telegram_html, split_telegram_text


def test_html_inside_code_fence_is_escaped() -> None:
    rendered = md_to_telegram_html("```html\n<b>unsafe</b>\n```")
    assert "&lt;b&gt;unsafe&lt;/b&gt;" in rendered
    assert "<b>unsafe</b>" not in rendered


def test_long_text_is_split_within_limit() -> None:
    chunks = split_telegram_text(("word " * 3_000).strip(), limit=500)
    assert " ".join(chunks).replace("  ", " ") == ("word " * 3_000).strip()
    assert all(len(chunk) <= 500 for chunk in chunks)


class _SentMessage:
    message_id = 100


class _FakeBot:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def send_message(self, *args, **kwargs):
        self.calls.append(kwargs)
        return _SentMessage()


@pytest.mark.asyncio
async def test_final_stream_message_keeps_forum_thread_id() -> None:
    bot = _FakeBot()
    consumer = TelegramStreamConsumer(
        bot=bot,
        chat_id=-1004488980222,
        user_id=1224362805,
        target_message_id=41,
        message_thread_id=5,
    )

    async def tokens():
        # Short output skips the preview path and exercises the final send path.
        yield "hello"

    await consumer.stream_from_generator(tokens())

    assert bot.calls
    assert bot.calls[0]["message_thread_id"] == 5

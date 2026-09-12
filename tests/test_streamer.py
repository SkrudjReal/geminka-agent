import pytest

from app.core import config
from app.services.streamer import (
    TelegramStreamConsumer,
    md_to_telegram_html,
    resolve_local_file,
    split_telegram_text,
)


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


class _FileBot(_FakeBot):
    async def send_document(self, *args, **kwargs):
        self.calls.append(kwargs)


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


def test_resolve_local_file_stays_inside_project(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    report = tmp_path / "report.md"
    report.write_text("report", encoding="utf-8")
    secret = tmp_path / ".env"
    secret.write_text("TOKEN=secret", encoding="utf-8")

    assert resolve_local_file("report.md") == report.resolve()
    assert resolve_local_file(".env") is None
    assert resolve_local_file("../outside.txt") is None


@pytest.mark.asyncio
async def test_stream_sends_local_file_and_hides_control_tag(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    document_path = tmp_path / "PERSONA.md"
    document_path.write_text("persona", encoding="utf-8")
    bot = _FileBot()
    consumer = TelegramStreamConsumer(bot=bot, chat_id=123)

    async def tokens():
        yield '<tg-file path="PERSONA.md" caption="Persona **file**"/>'

    result = await consumer.stream_from_generator(tokens())

    assert result == ""
    assert len(bot.calls) == 1
    assert bot.calls[0]["document"].path == str(document_path)
    assert bot.calls[0]["caption"] == "Persona <b>file</b>"

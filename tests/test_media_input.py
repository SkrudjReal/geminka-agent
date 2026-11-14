import asyncio
import json
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram.types import Animation, Chat, Message, User, Voice
from PIL import Image

from app.bot import helpers
from app.services import media_input as media


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg unavailable")
async def test_real_middle_gif_frame(tmp_path):
    source = tmp_path / "animation.gif"
    frames = [Image.new("RGB", (32, 32), color) for color in ("red", "green", "blue")]
    frames[0].save(source, save_all=True, append_images=frames[1:], duration=1000, loop=0)
    destination = tmp_path / "middle.png"
    await media.middle_frame(source, destination)
    with Image.open(destination) as image:
        r, g, b = image.convert("RGB").getpixel((16, 16))
    assert g > r and g > b


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg unavailable")
async def test_real_telegram_mp4_middle_frame(tmp_path):
    source = tmp_path / "animation.mp4"
    await media.run_media("ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                          "color=c=green:s=32x32:d=2", "-c:v", "libx264", "-threads", "1", str(source))
    destination = tmp_path / "middle.png"
    await media.middle_frame(source, destination)
    with Image.open(destination) as image:
        r, g, b = image.convert("RGB").getpixel((16, 16))
    assert g > r and g > b


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg unavailable")
async def test_real_whisper_audio_decoder_compatibility(tmp_path):
    from faster_whisper.audio import decode_audio

    source = tmp_path / "voice.ogg"
    await media.run_media("ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                          "sine=frequency=440:duration=0.2", "-c:a", "libopus", str(source))
    # PyAV 19 removed metadata_errors; exercise the decoder, not just its import.
    assert len(decode_audio(str(source))) > 0


async def test_gemini_sends_real_audio_and_only_returns_transcript(tmp_path, monkeypatch):
    source = tmp_path / "voice.ogg"
    source.write_bytes(b"ogg-audio")
    monkeypatch.setenv("GEMINI_AUDIO_MODEL", "gemini-2.5-flash")
    def respond(request):
        assert request.headers["x-goog-api-key"] == "test-key"
        parts = json.loads(request.content)["contents"][0]["parts"]
        assert parts[1]["inlineData"] == {"mimeType": "audio/ogg", "data": "b2dnLWF1ZGlv"}
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"thought": True, "text": "not transcript"}, {"text": "Привет, мир!"}]}}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(media.httpx, "AsyncClient", lambda **kwargs: client)
    assert await media.gemini_transcribe(source, "test-key") == "Привет, мир!"


@pytest.mark.parametrize("with_key", [False, True])
async def test_whisper_fallback_without_key_or_on_gemini_failure(tmp_path, monkeypatch, with_key):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key" if with_key else "")
    monkeypatch.setattr(media, "duration", AsyncMock(return_value=5))
    gemini = AsyncMock(side_effect=httpx.ConnectError("offline"))
    monkeypatch.setattr(media, "gemini_transcribe", gemini)
    worker = AsyncMock(return_value=b'{"text": "hello"}')
    monkeypatch.setattr(media, "run_media", worker)
    assert await media.transcribe(tmp_path / "voice.ogg") == ("hello", "Whisper")
    assert gemini.await_count == int(with_key)
    assert "app.services.speech_worker" in worker.call_args.args


async def test_voice_cache_and_debug_no_download(tmp_path, monkeypatch):
    source = tmp_path / "download.ogg"
    source.write_bytes(b"audio")
    download = AsyncMock(return_value=source)
    monkeypatch.setattr(helpers, "download_telegram_file", download)
    transcribe = AsyncMock(return_value=("привет", "Whisper"))
    monkeypatch.setattr(media, "transcribe", transcribe)
    voice = SimpleNamespace(file_id="file", file_unique_id="unique", duration=5)
    await media.media_context(None, voice, directory=tmp_path, voice=True, persist=False)
    download.assert_not_awaited()
    first = await media.media_context(None, voice, directory=tmp_path, voice=True)
    second = await media.media_context(None, voice, directory=tmp_path, voice=True)
    assert first == second and "привет" in second
    assert download.await_count == transcribe.await_count == 1
    assert not source.exists()


async def test_long_voice_rejected_before_download(tmp_path, monkeypatch):
    download = AsyncMock()
    monkeypatch.setattr(helpers, "download_telegram_file", download)
    voice = SimpleNamespace(file_id="file", file_unique_id="unique", duration=601)
    with pytest.raises(ValueError, match="10 минут"):
        await media.media_context(None, voice, directory=tmp_path, voice=True)
    download.assert_not_awaited()


async def test_subprocess_timeout_is_killed():
    import sys

    with pytest.raises(asyncio.TimeoutError):
        await media.run_media(sys.executable, "-c", "import time; time.sleep(20)", timeout=0.1)


@pytest.mark.parametrize("quoted", [False, True])
@pytest.mark.parametrize("voice", [False, True])
async def test_direct_and_replied_media_reaches_prompt(monkeypatch, tmp_path, quoted, voice):
    user = User(id=42, is_bot=False, first_name="Alice")
    chat = Chat(id=-1001, type="supergroup")
    attachment = (Voice(file_id="file", file_unique_id="unique", duration=2) if voice else
                  Animation(file_id="file", file_unique_id="unique", duration=2, width=32, height=32))
    original = Message(message_id=1, date=0, chat=chat, from_user=user,
                       **{"voice" if voice else "animation": attachment})
    message = (Message(message_id=2, date=0, chat=chat, from_user=user, text="что тут?", reply_to_message=original)
               if quoted else original)
    describe = AsyncMock(return_value="MEDIA-CONTEXT")
    monkeypatch.setattr(helpers, "media_context", describe)
    monkeypatch.setattr(helpers, "inspect_custom_emojis", AsyncMock(return_value=[]))
    monkeypatch.setattr(helpers, "attachment_directory", lambda uid, cid: tmp_path / str(cid) / str(uid))
    result = await helpers.extract_message_context(message, None)
    assert "MEDIA-CONTEXT" in result
    assert describe.call_args.kwargs["voice"] is voice
    assert describe.call_args.kwargs["directory"] == tmp_path / "-1001" / "42"


@pytest.mark.parametrize('quoted', [False, True])
@pytest.mark.parametrize('known, persist', [(False, True), (True, True), (False, False)])
async def test_unknown_sticker_frame_in_group_uploads(monkeypatch, tmp_path, quoted, known, persist):
    from aiogram.types import Sticker

    user = User(id=42, is_bot=False, first_name='Alice')
    chat = Chat(id=-1001, type='supergroup')
    sticker = Sticker(file_id='file', file_unique_id='unique', type='regular', width=32, height=32,
                      is_animated=True, is_video=False)
    original = Message(message_id=1, date=0, chat=chat, from_user=user, sticker=sticker)
    message = (Message(message_id=2, date=0, chat=chat, from_user=user, text='что тут?', reply_to_message=original)
               if quoted else original)
    frame = tmp_path / 'unique.png'
    Image.new('RGB', (32, 32)).save(frame)
    download = AsyncMock(return_value=frame)
    monkeypatch.setattr(helpers.sticker_scanner, 'download_sticker_frame', download)
    monkeypatch.setattr(helpers.asset_harvester, 'get_sticker_metadata', lambda *a, **kw: {'description':'known'} if known else None)
    monkeypatch.setattr(helpers.asset_harvester, 'register_sticker', lambda *a, **kw: None)
    monkeypatch.setattr(helpers.asset_harvester, 'is_pack_fully_scanned', lambda *a: True)
    monkeypatch.setattr(helpers, 'inspect_custom_emojis', AsyncMock(return_value=[]))
    monkeypatch.setattr(helpers, 'attachment_directory', lambda *a: tmp_path / 'uploads')
    result = await helpers.extract_message_context(message, None, persist=persist)
    if persist and not known:
        download.assert_awaited_once()
        assert str(tmp_path / 'uploads/stickers/unique.png') in result
        assert (tmp_path / 'uploads/stickers/unique.png').is_file()
    else:
        download.assert_not_awaited()


async def test_requested_transcript_bypasses_llm_and_mcp(monkeypatch):
    from contextlib import asynccontextmanager

    from app.bot import handlers

    @asynccontextmanager
    async def typing(**kwargs):
        yield

    message = SimpleNamespace(
        chat=SimpleNamespace(type="private", id=42), from_user=SimpleNamespace(id=42),
        message_thread_id=None, voice=None, caption=None, text="клумба транскрибацию гс в blockquote expandable",
        reply_to_message=SimpleNamespace(voice=object()),
    )
    send = AsyncMock()
    monkeypatch.setattr(handlers, "check_auth", lambda uid: True)
    monkeypatch.setattr(handlers.state_store, "is_debug_mode", lambda uid: False)
    monkeypatch.setattr(handlers.ChatActionSender, "typing", typing)
    monkeypatch.setattr(helpers, "send_transcript", send)
    # No LLM client at all: this path must not call generation or collector.
    await handlers.handle_any_message(message, object(), None)
    send.assert_awaited_once()


async def test_full_transcript_is_split_into_balanced_expandable_quotes(monkeypatch, tmp_path):
    text = "<слова & цитаты> " * 500
    monkeypatch.setattr(media, "full_transcript", AsyncMock(return_value=text))
    monkeypatch.setattr(helpers, "attachment_directory", lambda *args: tmp_path)
    message = SimpleNamespace(
        voice=object(), from_user=SimpleNamespace(id=42), chat=SimpleNamespace(id=42, type="private"),
        answer=AsyncMock(),
    )
    await helpers.send_transcript(message, object())
    assert message.answer.await_count > 1
    from html import unescape
    chunks = []
    for call in message.answer.call_args_list:
        body = call.args[0]
        assert body.startswith("<blockquote expandable>") and body.endswith("</blockquote>")
        assert "&lt;слова &amp; цитаты&gt;" in body
        chunks.append(unescape(body.removeprefix("<blockquote expandable>").removesuffix("</blockquote>")))
    assert " ".join(chunks).strip() == text.strip()

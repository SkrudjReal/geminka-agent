import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.types import Chat, Document, Message, User

from app.bot import helpers
from app.core import config
from app.services import bot_api_mcp as bridge
from app.services.attachments import attachment_directory
from app.services.sandbox import sandbox_command


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DOWNLOADS_DIR", tmp_path / "downloads")
    monkeypatch.setattr(config, "settings", config.Settings.from_env({
        "TELEGRAM_ALLOWED_USERS": "42,43", "TELEGRAM_OWNER_ID": "42",
    }))
    monkeypatch.setenv("GEMINKA_REQUESTER_ID", "42")
    monkeypatch.setenv("GEMINKA_CHAT_ID", "42")
    return tmp_path


class DownloadBot:
    def __init__(self, data, fail=False):
        self.data = data
        self.fail = fail

    async def get_file(self, file_id):
        return SimpleNamespace(file_size=len(self.data), file_path="telegram/file")

    async def download_file(self, file_path, destination):
        destination.write(self.data)
        if self.fail:
            raise OSError("download interrupted")


def document(name="messages4.html"):
    return Document(file_id="telegram-file-id", file_unique_id="unique", file_name=name)


def message(doc, quoted=False):
    fields = dict(date=datetime.now(timezone.utc), chat=Chat(id=42, type="private"),
                  from_user=User(id=42, is_bot=False, first_name="User"))
    if quoted:
        return Message(message_id=2, text="Прочитай целиком", reply_to_message=Message(
            message_id=1, document=doc, **fields,
        ), **fields)
    return Message(message_id=1, caption="Прочитай целиком", document=doc, **fields)


@pytest.mark.parametrize("quoted", [False, True])
async def test_full_download_survives_prompt_limits_and_tail_is_readable(storage, quoted):
    source = ('<html><head><style>hidden CSS</style></head><body>'
              + '<div id="message1">Начало 🥰</div>'
              + '<div>Длинное сообщение</div>' * 9000
              + '<div id="message1858048">КОНЕЦ ФАЙЛА</div>'
              + '<script>hidden JS</script></body></html>').encode()
    context = await helpers.extract_message_context(message(document(), quoted), DownloadBot(source))
    files = (await bridge.attachment_list())["files"]
    assert len(files) == 1
    filename = files[0]["filename"]
    path = attachment_directory(42, 42) / filename
    assert path.read_bytes() == source
    assert path.stat().st_mode & 0o777 == 0o600
    assert filename in context and "telegram-file-id" in context
    assert "attachment_read" in context and "Прочитай целиком" in context
    assert len(context) < 1500
    chunks, offset = [], 0
    while True:
        result = await bridge.attachment_read(filename, offset=offset)
        assert result["ok"] and len(result["content"]) <= 12000
        chunks.append(result["content"])
        if result["eof"]:
            assert result["next_offset"] is None
            break
        assert result["next_offset"] > offset
        offset = result["next_offset"]
    complete = "".join(chunks)
    assert "КОНЕЦ ФАЙЛА" in complete and "[message1858048]" in complete
    assert "hidden CSS" not in complete and "hidden JS" not in complete
    raw = await bridge.attachment_read(filename, offset=len(source.decode()) - 50, view="raw")
    assert raw["eof"] and raw["content"].endswith("</html>")


async def test_access_is_scoped_and_paths_symlinks_and_bad_limits_are_rejected(storage, monkeypatch):
    context = await helpers._document_context(
        DownloadBot(b"hello"), document("notes.txt"), user_id=42, chat_id=42,
    )
    filename = json.loads(context.splitlines()[1])["filename"]
    outside = storage / "secret.txt"
    outside.write_text("secret")
    (attachment_directory(42, 42) / "link.txt").symlink_to(outside)
    for name in ("../secret.txt", str(outside), "link.txt", "..\\secret.txt"):
        assert not (await bridge.attachment_read(name))["ok"]
    for kwargs in ({"offset": -1}, {"limit": 24001}, {"limit": 0}, {"view": "execute"}):
        assert not (await bridge.attachment_read(filename, **kwargs))["ok"]
    monkeypatch.setenv("GEMINKA_REQUESTER_ID", "43")
    assert not (await bridge.attachment_read(filename))["ok"]
    assert (await bridge.attachment_list())["files"] == []
    monkeypatch.setenv("GEMINKA_REQUESTER_ID", "42")
    monkeypatch.setenv("GEMINKA_CHAT_ID", "-100123")
    assert not (await bridge.attachment_read(filename))["ok"]
    monkeypatch.delenv("GEMINKA_CHAT_ID")
    assert not (await bridge.attachment_list())["ok"]
    monkeypatch.setenv("GEMINKA_REQUESTER_ID", "99")
    assert not (await bridge.attachment_read(filename))["ok"]


async def test_debug_does_not_download_and_failed_or_oversized_files_are_cleaned(storage):
    class NoDownload:
        async def get_file(self, *_):
            raise AssertionError("debug must not download")

    result = await helpers.extract_message_context(message(document()), NoDownload(), persist=False)
    assert "debug" in result and not config.DOWNLOADS_DIR.exists()
    with pytest.raises(OSError, match="interrupted"):
        await helpers._document_context(DownloadBot(b"partial", fail=True), document(), user_id=42, chat_id=42)
    assert list(attachment_directory(42, 42).iterdir()) == []

    class Oversized(DownloadBot):
        async def get_file(self, *_):
            return SimpleNamespace(file_size=1, file_path="telegram/file")

    with pytest.raises(ValueError, match="размер"):
        await helpers._document_context(
            Oversized(b"x" * (config.settings.max_download_bytes + 1)), document(), user_id=42, chat_id=42,
        )
    assert list(attachment_directory(42, 42).iterdir()) == []


async def test_multiple_same_named_documents_remain_distinct_and_empty_file_is_readable(storage):
    for data in (b"first", b"second", b""):
        await helpers._document_context(DownloadBot(data), document("../../notes.txt"), user_id=42, chat_id=42)
    files = (await bridge.attachment_list(limit=2))["files"]
    assert len(files) == 2 and (await bridge.attachment_list(limit=2))["next_offset"] == 2
    all_files = (await bridge.attachment_list())["files"]
    assert len(all_files) == 3
    assert {(await bridge.attachment_read(f["filename"]))["content"] for f in all_files} == {"first", "second", ""}


def test_real_read_only_sandbox_can_read_full_attachment(storage):
    if not shutil.which("bwrap"):
        pytest.skip("bubblewrap unavailable")
    directory = attachment_directory(42, 42)
    directory.mkdir(parents=True)
    (directory / "full.txt").write_text("x" * 100000 + "END")
    root = Path(__file__).resolve().parents[1]
    script = f"""
from pathlib import Path
from app.core import config
from app.services.attachments import read_attachment
config.DOWNLOADS_DIR = Path({str(config.DOWNLOADS_DIR)!r})
result = read_attachment(42, 42, 'full.txt', offset=100000)
assert result['content'] == 'END' and result['eof']
print('read-only full-tail OK')
"""
    command = sandbox_command([sys.executable, "-c", script], root, read_only=True)
    command[command.index("--"):command.index("--")] = ["--ro-bind", str(storage), str(storage)]
    result = subprocess.run(command,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "read-only full-tail OK" in result.stdout

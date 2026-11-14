import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import config
from app.services import bot_api_mcp as bridge
from app.services.sandbox import agy_sandbox_command
from app.services.streamer import TelegramStreamConsumer
from app.services.topics import TopicManager


@pytest.mark.parametrize("reply", [False, True])
async def test_llm_chooses_answer_or_reply_even_with_a_late_tag(reply):
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(
        message_id=7, chat=SimpleNamespace(id=-1001), message_thread_id=None,
        from_user=None, reply_to_message=None, text="Ответ", caption=None,
    )))
    consumer = TelegramStreamConsumer(bot=bot, chat_id=-1001, user_id=777, target_message_id=123)

    async def tokens():
        yield "Достаточно длинный ответ без заранее известного способа отправки."
        assert bot.send_message.await_count == 0
        if reply:
            yield "<tg-reply/>"

    await consumer.stream_from_generator(tokens())
    bot.send_message.assert_awaited_once()
    params = bot.send_message.call_args.kwargs["reply_parameters"]
    assert (params.message_id if params else None) == (123 if reply else None)


async def test_group_mcp_never_exposes_private_memory_or_bot_api(tmp_path, monkeypatch):
    topics = TopicManager(tmp_path / "topics.json")
    topics.add_chat(-1001, "Group", 1)
    monkeypatch.setattr("app.services.topics.topic_manager", topics)
    monkeypatch.setattr(bridge, "_requester_id", lambda: 777)
    monkeypatch.setenv("GEMINKA_GROUP_MODE", "1")
    monkeypatch.setenv("GEMINKA_CHAT_ID", "-1001")
    assert bridge._skill_access() and bridge._collector_access()
    assert not (await bridge.personalization_profile())["ok"]
    assert not (await bridge.bot_api_call("send_message", {}))["ok"]
    monkeypatch.setattr(bridge, "_requester_id", lambda: config.settings.owner_user_id)
    assert not (await bridge.bot_api_call("send_message", {}))["ok"]


def test_group_mod_has_private_profile_and_hides_owner_files(tmp_path, monkeypatch):
    if not shutil.which("bwrap"):
        pytest.skip("bubblewrap unavailable")
    host = tmp_path / "host"
    source = host / ".gemini/antigravity-cli"
    source.mkdir(parents=True)
    (source / "bin").mkdir()
    (source / "bin/webm_encoder").write_text("readonly executable")
    (source / "bin/agentapi").write_text("shared script must not be changed")
    (source / "antigravity-oauth-token").write_text("fake-provider-token")
    (host / "private.txt").write_text("OWNER SECRET")
    root = tmp_path / "project"
    (root / "app").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "app/public.py").write_text("# Public application code")
    (root / ".env").write_text("BOT_TOKEN=PRIVATE")
    (root / "data/memory.db").write_text("PRIVATE MEMORY")
    (root / "data/default_emojis.json").write_text("{}")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: host))
    script = f"""
from pathlib import Path
root=Path({str(root)!r})
assert not (root/'.env').exists()
assert not (root/'data/memory.db').exists()
assert not Path({str(host / 'private.txt')!r}).exists()
assert (root/'data/default_emojis.json').is_file()
assert Path.home().name.endswith('-mod-1001')
assert not (root/'data/agy_sandbox').exists()
binary=Path.home()/'.gemini/antigravity-cli/bin'
settings=binary.parent/'settings.json'
replacement=settings.with_suffix('.tmp')
replacement.write_text(settings.read_text())
replacement.replace(settings)
(binary/'agentapi').write_text('isolated agentapi wrapper')
try:
    (binary/'webm_encoder').write_text('overwrite')
except OSError:
    pass
else:
    raise AssertionError('shared binary became writable')
try:
    (root/'app/public.py').write_text('overwrite')
except OSError:
    pass
else:
    raise AssertionError('group edited application code')
(Path.cwd()/'scratch.txt').write_text('isolated workspace')
print('group boundary OK')
"""
    command = agy_sandbox_command([sys.executable, "-c", script], root, group_id=-1001, restricted=False)
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "group boundary OK" in result.stdout
    assert (root / ".env").read_text() == "BOT_TOKEN=PRIVATE"
    assert (source / "bin/agentapi").read_text() == "shared script must not be changed"

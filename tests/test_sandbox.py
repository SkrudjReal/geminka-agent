import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.filters import CommandObject

from app.bot import handlers
from app.core import config
from app.core.state import StateStore
from app.services.agy_cli import AgyCliClient, AgyCliError, _Session
from app.services.antigravity import AntigravityClient
from app.services.sandbox import sandbox_command


def test_policy_default_persistence_and_reset(tmp_path):
    path = tmp_path / "state.db"
    store = StateStore(path)
    assert store.is_sandbox_enabled()
    store.set_sandbox_enabled(False)
    store.clear_preferences(42)
    assert not StateStore(path).is_sandbox_enabled()
    store.set_sandbox_enabled(True)
    assert StateStore(path).is_sandbox_enabled()


@pytest.mark.asyncio
async def test_owner_command_and_denied_users(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "settings", config.Settings.from_env({
        "TELEGRAM_ALLOWED_USERS": "42,43", "TELEGRAM_OWNER_ID": "42",
    }))
    store = StateStore(tmp_path / "state.db")
    client = AntigravityClient(store=store)
    session = _Session(SimpleNamespace(returncode=None), "gemini", "low", asyncio.Lock())
    client._agy._sessions[42] = session
    client._agy._one_shots[1] = session
    close = AsyncMock()
    monkeypatch.setattr(client._agy, "_close_session", close)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=43), chat=SimpleNamespace(type="private"), answer=AsyncMock(),
    )
    await handlers.cmd_sandbox(message, CommandObject(command="sandbox", args="off"), client)
    assert store.is_sandbox_enabled()
    close.assert_not_called()
    with pytest.raises(PermissionError):
        await client.set_sandbox_mode(43, False)
    message.from_user.id = 42
    await handlers.cmd_sandbox(message, CommandObject(command="sandbox", args="status"), client)
    assert store.is_sandbox_enabled()
    await handlers.cmd_sandbox(message, CommandObject(command="sandbox", args="bad"), client)
    assert store.is_sandbox_enabled()
    await handlers.cmd_sandbox(message, CommandObject(command="sandbox", args="off"), client)
    assert not store.is_sandbox_enabled() and not client._agy.sandbox_enabled
    assert not client._agy._sessions and not client._agy._one_shots
    await handlers.cmd_sandbox(message, CommandObject(command="sandbox"), client)
    assert store.is_sandbox_enabled() and client._agy.sandbox_enabled
    message.chat.type = "supergroup"
    await handlers.cmd_sandbox(message, CommandObject(command="sandbox", args="off"), client)
    assert store.is_sandbox_enabled()
    await client.aclose()


def test_launch_flags_and_fail_closed(monkeypatch, tmp_path):
    client = AgyCliClient(project_dir=tmp_path)
    monkeypatch.setattr("app.services.agy_cli.agy_sandbox_command", lambda args, root: ["bwrap", *args])
    args = client._launch_command("gemini", "high")
    assert args[0] == "bwrap" and "--sandbox" in args
    assert args[args.index("--mode") + 1] == "accept-edits"
    assert "--dangerously-skip-permissions" not in args
    client.sandbox_enabled = False
    assert "--sandbox" not in client._launch_command("gemini", "high")
    assert client._launch_command("gemini", "high")[0] == client.command
    client.sandbox_enabled = True
    monkeypatch.undo()
    monkeypatch.setattr("app.services.sandbox.shutil.which", lambda name: None)
    with pytest.raises(AgyCliError, match="bubblewrap"):
        client._launch_command("gemini", "high")


def test_real_filesystem_boundary():
    if not shutil.which("bwrap"):
        pytest.skip("Bubblewrap is not installed")
    code = """
from pathlib import Path
import sys
root, outside = map(Path, sys.argv[1:])
(root / 'allowed.txt').write_text('inside')
for path in (outside, root / 'escape', root / '..' / 'outside.txt'):
    try:
        path.write_text('escaped')
    except OSError:
        pass
    else:
        raise AssertionError(f'write escaped sandbox: {path}')
"""
    # /tmp is deliberately ephemeral inside the sandbox; test host writes in /var/tmp.
    with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
        root = Path(directory) / "project"
        root.mkdir()
        outside = Path(directory) / "outside.txt"
        outside.write_text("unchanged")
        (root / "escape").symlink_to(outside)
        result = subprocess.run(
            sandbox_command([sys.executable, "-c", code, str(root), str(outside)], root),
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert (root / "allowed.txt").read_text() == "inside"
        assert outside.read_text() == "unchanged"

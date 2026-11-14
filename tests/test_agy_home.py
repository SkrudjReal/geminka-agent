import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.sandbox import agy_sandbox_command


def test_summary_launch_disables_mcp_without_affecting_normal_launch(tmp_path, monkeypatch):
    from app.services.agy_cli import AgyCliClient

    calls = []

    def wrap(command, project_dir, **kwargs):
        calls.append(kwargs)
        return command

    monkeypatch.setattr("app.services.agy_cli.agy_sandbox_command", wrap)
    client = AgyCliClient(project_dir=tmp_path)
    client._launch_command("gemini", "low", {"requester_id": "summary"})
    assert calls[-1] == {"read_only": True, "tools_enabled": False}
    client._launch_command("gemini", "high", {"requester_id": "123"})
    assert "tools_enabled" not in calls[-1]


@pytest.mark.parametrize("restricted", [True, False])
@pytest.mark.parametrize("tools_enabled", [True, False])
def test_clean_home_keeps_history_not_global_configuration(tmp_path, monkeypatch, restricted, tools_enabled):
    if not shutil.which("bwrap"):
        pytest.skip("bubblewrap unavailable")
    home = tmp_path / "host"
    source = home / ".gemini/antigravity-cli"
    source.mkdir(parents=True)
    (source / "settings.json").write_text('{"hooks":{"bad":true}}')
    (source / "antigravity-oauth-token").write_text("fake-token")
    root = tmp_path / "project"
    runtime = root / "data/agy_sandbox"
    runtime.mkdir(parents=True)
    (root / "AGENTS.md").write_text("Unrelated identity")
    (runtime / "conversations").mkdir()
    (runtime / "conversations/keep").write_text("history")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    script = f"""
import json, os
from pathlib import Path
h = Path.home()
assert h.is_relative_to(Path({str(home / '.geminka')!r}))
assert Path.cwd() == h / 'workspace'
assert (h / 'AGENTS.md').read_text() == ''
assert Path({str(root / 'AGENTS.md')!r}).read_text() == ''
cli = h / '.gemini/antigravity-cli'
assert (cli / 'conversations/keep').read_text() == 'history'
assert (cli / 'antigravity-oauth-token').read_text() == 'fake-token'
assert 'hooks' not in json.loads((cli / 'settings.json').read_text())
assert not (h / '.gemini/config/hooks.json').exists()
assert list(json.loads((h / '.gemini/config/mcp_config.json').read_text())['mcpServers']) == {(['geminka-bot-api'] if tools_enabled else [])!r}
assert list((cli / 'builtin').iterdir()) == []
print('isolated')
"""
    result = subprocess.run(
        agy_sandbox_command([sys.executable, "-c", script], root, restricted=restricted, tools_enabled=tools_enabled),
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated"
    assert (root / "AGENTS.md").read_text() == "Unrelated identity"
    assert (runtime / "conversations/keep").read_text() == "history"
    # Only generated project settings are writable for AGY's atomic validation.
    runtime_settings = json.loads((runtime / "settings.json").read_text())
    assert "hooks" not in runtime_settings
    assert runtime_settings["allowNonWorkspaceAccess"] is True

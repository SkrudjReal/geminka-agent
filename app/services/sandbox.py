"""Linux filesystem write boundary for AGY and its child tools."""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


class SandboxError(RuntimeError):
    pass


def _write_config(path: Path, content: str) -> None:
    """Concurrent foreground/summary launches must never read truncated JSON."""
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".config-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sandbox_command(command: list[str], project_dir: Path, *, read_only: bool = False) -> list[str]:
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise SandboxError("Sandbox требует bubblewrap (bwrap). Установи: sudo apt install bubblewrap")
    root = project_dir.resolve(strict=True)
    if root == Path("/") or root == Path.home().resolve():
        raise SandboxError("Sandbox должен указывать на отдельную папку проекта.")
    return [
        bwrap, "--die-with-parent", "--new-session", "--unshare-pid",
        "--unshare-ipc", "--unshare-uts", "--cap-drop", "ALL",
        "--ro-bind", "/", "/",
        "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
        "--ro-bind" if read_only else "--bind", str(root), str(root),
        "--chdir", str(root), "--", *command,
    ]


def agy_sandbox_command(
    command: list[str], project_dir: Path, *, restricted: bool = True, read_only: bool = False,
    tools_enabled: bool = True,
) -> list[str]:
    """Use a clean CLI home, retaining each project's existing conversation storage."""
    wrapped = sandbox_command(command, project_dir, read_only=read_only)
    if not restricted:
        # Keep project-specific AGY state without imposing host write restrictions.
        wrapped[wrapped.index("--ro-bind")] = "--bind"
    source = Path.home() / ".gemini" / "antigravity-cli"
    if not source.is_dir():
        raise SandboxError("Сначала установи и авторизуй agy CLI: его runtime-папка не найдена.")
    runtime = project_dir.resolve() / "data" / "agy_sandbox"
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not runtime.resolve().is_relative_to(project_dir.resolve()):
        raise SandboxError("Runtime sandbox не может ссылаться за пределы проекта.")
    # Never inherit settings, hooks, plugins, skills, or arbitrary MCP servers.
    for name in (
        "antigravity_state.pbtxt", "installation_id",
        "antigravity-oauth-token", "jetski_state.pbtxt",
    ):
        src = source / name
        dest = runtime / name
        if src.is_file() and not dest.exists():
            dest.symlink_to(src)
    identity = hashlib.sha256(str(project_dir.resolve()).encode()).hexdigest()[:16]
    home = Path.home() / ".geminka" / "instances" / (identity if tools_enabled else identity + "-summary")
    cli = home / ".gemini" / "antigravity-cli"
    config = home / ".gemini" / "config"
    workspace = home / "workspace"
    for directory in (home, cli, config, workspace, home / "mcp", home / "empty-builtin"):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    empty = home / "AGENTS.md"
    _write_config(home.parents[1] / "AGENTS.md", "")
    _write_config(empty, "")
    _write_config(workspace / "AGENTS.md", "")
    settings = home / "settings.json"
    _write_config(settings, json.dumps({
        "allowNonWorkspaceAccess": True,
        "permissions": {"allow": ["command(*)", "mcp(geminka-bot-api/*)"]},
        "trustedWorkspaces": [str(workspace), str(project_dir.resolve())],
    }))
    uv = shutil.which("uv")
    if not uv:
        raise SandboxError("Для MCP Geminka требуется uv в PATH.")
    servers = {
        "geminka-bot-api": {
            "command": uv,
            "args": ["--directory", str(project_dir.resolve()), "run", "--no-sync", "python", "-m", "app.services.bot_api_mcp"],
            "disabled": False,
            "env": {"GEMINKA_MCP_PROCESS": "1"},
        },
    } if tools_enabled else {}
    _write_config(config / "mcp_config.json", json.dumps({"mcpServers": servers}))
    mounts = []
    mounts.extend([
        "--bind", str(home), str(home),
        "--bind", str(runtime), str(cli),
        "--ro-bind", str(settings), str(cli / "settings.json"),
        "--bind", str(home / "mcp"), str(cli / "mcp"),
        "--ro-bind", str(home / "empty-builtin"), str(cli / "builtin"),
        "--ro-bind", str(home / "empty-builtin"), str(cli / "plugin_data"),
        "--setenv", "HOME", str(home),
        "--setenv", "XDG_CONFIG_HOME", str(home / ".config"),
        "--setenv", "XDG_DATA_HOME", str(home / ".local/share"),
        "--setenv", "XDG_CACHE_HOME", str(home / ".cache"),
        "--setenv", "GEMINKA_MEMORY_READ_ONLY", "1" if read_only else "0",
    ])
    # Symlink targets may otherwise be hidden by /tmp's private mount (e.g. tests).
    for name in ("antigravity_state.pbtxt", "installation_id", "antigravity-oauth-token", "jetski_state.pbtxt"):
        target = runtime / name
        if target.is_symlink() and target.is_file():
            original = target.resolve(strict=True)
            mounts.extend(["--ro-bind", str(original), str(original)])
    wrapped[wrapped.index("--chdir") + 1] = str(workspace)
    # The installed agent executable is shared, but bundled skills/plugins aren't.
    for name in ("bin",):
        if (source / name).is_dir():
            mounts.extend(["--ro-bind", str(source / name), str(cli / name)])
    # --add-dir may discover repository instructions: hide them only in this process.
    for name in ("AGENTS.md", "GEMINI.md", "CLAUDE.md"):
        path = project_dir / name
        if path.is_file():
            mounts.extend(["--ro-bind", str(empty), str(path)])
    # Prevent tool calls from rewriting the owner's policy or credentials.
    for path in ((project_dir / ".env", project_dir / "data" / "state.db") if restricted else ()):
        for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
            if candidate.is_file():
                mounts.extend(["--ro-bind", str(candidate), str(candidate)])
    index = wrapped.index("--")
    wrapped[index:index] = mounts
    return wrapped

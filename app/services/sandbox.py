"""Linux filesystem write boundary for AGY and its child tools."""

import shutil
from pathlib import Path


class SandboxError(RuntimeError):
    pass


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
) -> list[str]:
    """Redirect mutable AGY state into the project; keep installed tools read-only."""
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
    # Copy only configuration/auth, never existing conversations, logs or model caches.
    for name in (
        "settings.json", "antigravity_state.pbtxt", "installation_id",
        "antigravity-oauth-token", "jetski_state.pbtxt",
    ):
        src = source / name
        dest = runtime / name
        if src.is_file() and not dest.exists():
            shutil.copy2(src, dest)
            dest.chmod(0o600)
    if (source / "mcp").is_dir() and not (runtime / "mcp").exists():
        shutil.copytree(source / "mcp", runtime / "mcp")
    mounts = ["--bind", str(runtime), str(source)]
    for name in ("bin", "builtin"):
        if (source / name).is_dir():
            mounts.extend(["--ro-bind", str(source / name), str(source / name)])
    # Prevent tool calls from rewriting the owner's policy or credentials.
    for path in ((project_dir / ".env", project_dir / "data" / "state.db") if restricted else ()):
        for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
            if candidate.is_file():
                mounts.extend(["--ro-bind", str(candidate), str(candidate)])
    index = wrapped.index("--")
    wrapped[index:index] = mounts
    return wrapped

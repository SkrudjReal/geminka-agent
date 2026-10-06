"""Register one systemd user unit per canonical project directory."""

import fcntl
import os
import re
import subprocess
import sys
from pathlib import Path


def unit_value(value: str) -> str:
    if any(char in value for char in "\n\r\0"):
        raise ValueError("Project paths must not contain control characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def unit_text(project: Path) -> str:
    working_directory = unit_value(str(project))[1:-1].replace(" ", r"\x20")
    return f"""# Geminka project: {project}
[Unit]
Description=Geminka Telegram AI Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={working_directory}
ExecStart=:{unit_value(str(project / 'scripts/start.sh'))}
Restart=on-failure
RestartPreventExitStatus=2
RestartSec=3
KillMode=control-group
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def select_service(project: Path, directory: Path) -> str:
    marker = f"# Geminka project: {project}\n"
    for candidate in sorted(directory.glob("*.service")):
        if not candidate.is_symlink() and candidate.is_file():
            if candidate.read_text().startswith(marker):
                return candidate.name
    base = re.sub(r"[^A-Za-z0-9_.-]+", "-", project.name).strip(".-") or "geminka"
    base = base[:180]
    suffix = 0
    while True:
        name = f"{base}{suffix or ''}.service"
        path = directory / name
        if not path.exists() and not path.is_symlink():
            return name
        suffix += 1


def deploy(project: Path, directory: Path) -> str:
    project = project.resolve(strict=True)
    text = unit_text(project)
    directory.mkdir(parents=True, exist_ok=True)
    # Serialize selection and registration across concurrent project launches.
    with (directory / ".geminka-deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        name = select_service(project, directory)
        temporary = directory / f".{name}.tmp"
        temporary.write_text(text)
        temporary.replace(directory / name)
        def systemctl(*args):
            subprocess.run(["systemctl", "--user", *args], check=True, stdout=sys.stderr)

        systemctl("daemon-reload")
        loaded = subprocess.run(
            ["systemctl", "--user", "show", name, "--property=LoadState", "--value"],
            check=True, capture_output=True, text=True,
        )
        if loaded.stdout.strip() != "loaded":
            raise RuntimeError(f"Systemd rejected {name}; existing service was not stopped")
        legacy = directory / "geminka.service"
        if name != legacy.name and legacy.is_file() and not legacy.is_symlink():
            old = legacy.read_text().splitlines()
            if (
                f"WorkingDirectory={project}" in old
                and f"ExecStart={project}/scripts/start.sh" in old
            ):
                systemctl("disable", "--now", legacy.name)
        systemctl("enable", name)
        systemctl("restart", name)
    return name


if __name__ == "__main__":
    project = Path(__file__).resolve().parent.parent
    directory = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user"
    print(deploy(project, directory))

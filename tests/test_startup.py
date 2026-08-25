from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_missing_token_exits_nonzero_without_traceback() -> None:
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.update(
        {
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_ALLOWED_USERS": "",
            "TELEGRAM_ALLOW_ALL_USERS": "false",
        }
    )
    result = subprocess.run(
        [sys.executable, "main.py"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 2
    assert "TELEGRAM_BOT_TOKEN is required" in combined
    assert "Traceback" not in combined

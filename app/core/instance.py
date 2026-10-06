"""Prevent two local Geminka processes from polling the same Telegram bot."""

import fcntl
import hashlib
import os
from pathlib import Path

from app.core.config import ConfigurationError


def acquire_bot_lock(token: str, directory: Path | None = None):
    root = directory or Path(f"/tmp/geminka-{os.getuid()}")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.getuid():
        raise ConfigurationError("Unsafe Geminka instance-lock directory")
    root.chmod(0o700)
    # Key by bot ID, not the secret: rotating a token must not bypass the lock.
    identity = token.split(":", 1)[0]
    digest = hashlib.sha256(identity.encode()).hexdigest()
    fd = os.open(root / f"{digest}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    handle = os.fdopen(fd, "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise ConfigurationError(
            "Этот Telegram-бот уже запущен локально. Для второй Geminka нужен другой BotFather-токен."
        ) from None
    return handle

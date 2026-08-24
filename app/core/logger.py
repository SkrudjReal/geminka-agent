"""Logging configuration and Sensitive Data masking filter for Geminka."""

import logging
import re
from typing import List

from app.core import config


class SensitiveDataFilter(logging.Filter):
    """Masks bot tokens, API keys, and sensitive authorization credentials in all log records."""

    def __init__(self, patterns: List[str] = None):
        super().__init__()
        self.patterns = patterns or []
        # Mask Telegram Bot Token format: 123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
        self.token_regex = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{35}\b")
        # Mask Bearer tokens
        self.bearer_regex = re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._sanitize(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._sanitize(v) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._sanitize(v) if isinstance(v, str) else v for v in record.args)
        return True

    def _sanitize(self, text: str) -> str:
        if not isinstance(text, str):
            return text
        sanitized = self.token_regex.sub("[REDACTED_BOT_TOKEN]", text)
        sanitized = self.bearer_regex.sub("Bearer [REDACTED_TOKEN]", sanitized)
        if config.BOT_TOKEN and config.BOT_TOKEN in sanitized:
            sanitized = sanitized.replace(config.BOT_TOKEN, "[REDACTED_BOT_TOKEN]")
        return sanitized


def setup_logging(level: int = logging.INFO) -> None:
    """Configures project-wide logging with security filters and unified formatting."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    formatter = logging.Formatter(log_format)

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(SensitiveDataFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Silence overly verbose third-party loggers
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

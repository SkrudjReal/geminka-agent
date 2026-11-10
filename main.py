#!/usr/bin/env python3
"""Root entrypoint for Geminka AI Agent."""

def run():
    # Reject invalid credentials before importing bot services or opening SQLite.
    import sys

    from app.core.config import ConfigurationError, settings

    try:
        settings.validate_startup()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None

    from app.main import run as run_bot

    run_bot()

if __name__ == "__main__":
    run()

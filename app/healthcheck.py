"""Container readiness check for the configured OMP dependency."""

from __future__ import annotations

import sys

import httpx

from app.core import config


def main() -> int:
    try:
        response = httpx.get(f"{config.settings.omp_base_url}/models", timeout=3.0)
        return 0 if response.status_code == 200 else 1
    except httpx.HTTPError:
        return 1


if __name__ == "__main__":
    sys.exit(main())

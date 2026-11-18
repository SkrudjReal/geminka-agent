"""Unit tests never spend the owner's live decision API credits."""

import pytest
import pytest_asyncio


@pytest.fixture(autouse=True)
def no_live_decisions(monkeypatch):
    monkeypatch.setenv('DECISION_PROVIDER', 'off')
    monkeypatch.setenv('KEENABLE_ENABLED', 'false')


@pytest_asyncio.fixture(autouse=True)
async def close_optional_clients():
    yield
    from app.services.collector_mcp import close_collector
    from app.services.decisions import decisions

    await close_collector()
    await decisions.aclose()
    decisions.cooldown = 0

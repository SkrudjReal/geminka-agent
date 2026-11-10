"""Opt-in two-turn AGY benchmark. Never sends Telegram messages or archives user memory."""

import argparse
import asyncio
import json
import time

from app.core import config
from app.core.state import state_store
from app.services.agy_cli import AgyCliClient
from app.services.harvester import asset_harvester


async def main():
    user_id = config.settings.owner_user_id
    preferences = state_store.get_preferences(user_id) if user_id else {}
    model = preferences.get("model") or config.settings.default_model
    effort = preferences.get("reasoning") or config.settings.reasoning_effort
    persona = config.get_system_prompt()
    catalog = asset_harvester.format_emojis_prompt_context(user_id or 0)
    client = AgyCliClient(timeout_seconds=75)
    print(json.dumps({"model": model, "effort": effort, "persona_chars": len(persona), "catalog_chars": len(catalog)}), flush=True)
    try:
        for label in ("cold", "warm"):
            started, first_at, size = time.monotonic(), None, 0
            async for chunk in client.stream(
                -987654321, model=model, effort=effort,
                messages=[
                    {"role": "system", "content": persona + "\n\n" + catalog},
                    {"role": "user", "content": "Технический замер. Не вызывай инструменты, не отправляй сообщения, не меняй файлы. Ответь только словом ОК."},
                ],
                tool_context={"requester_id": "benchmark", "chat_id": "0"},
            ):
                first_at = first_at or time.monotonic()
                size += len(chunk)
            print(json.dumps({
                "turn": label, "first_token_ms": round((first_at - started) * 1000) if first_at else None,
                "completed_ms": round((time.monotonic() - started) * 1000), "reply_chars": size,
            }), flush=True)
    finally:
        await client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True, help="Explicitly allow two model requests")
    parser.parse_args()
    asyncio.run(main())

"""Real MemPalace smoke test on synthetic data; never starts Telegram polling.

Run: uv run python -m scripts.check_palace_memory [--gateway]
"""

import argparse
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.context import ContextManager
from app.core.state import StateStore
from app.services.antigravity import AntigravityClient
from app.services.palace_memory import PalaceMemory


async def check(root: Path, gateway: bool):
    memory = PalaceMemory(root / "memory")
    samples = [
        "Я мечтаю посетить Киото и увидеть японские храмы.",
        "Я работаю программистом и изучаю язык Rust.",
        "Мне нравится зелёный чай с жасмином.",
        "Сегодня я починил велосипед и заменил колёса.",
        "На ужин приготовил грибной суп.",
    ]
    for index, text in enumerate(samples):
        await asyncio.to_thread(memory.archive, 1, text, key=f"sample-{index}", pending=False)
    await asyncio.to_thread(memory.archive, 2, "Секрет другого пользователя: Париж.", pending=False)
    recall = await asyncio.to_thread(
        memory.format_rag_context, 1, "В какой город я мечтаю поехать?", 2
    )
    assert "Киото" in recall and "Париж" not in recall
    print("PASS: real multilingual vector retrieval and user isolation")
    # Reopen through a new adapter; no in-process facts/profile cache is needed.
    reopened = PalaceMemory(root / "memory")
    assert reopened.count(1) == len(samples)
    print("PASS: persistent archive reopen")
    legacy = StateStore(root / "legacy.db")
    legacy.add_exchange(3, "Старое сообщение", "Старый ответ")
    legacy.add_memory(3, "Любит лес", "user_custom")
    imported = await asyncio.to_thread(memory.migrate_legacy, legacy.path)
    assert imported["messages"] == 2 and imported["facts"] == 1
    assert (await asyncio.to_thread(memory.migrate_legacy, legacy.path))["messages"] == 0
    assert len(legacy.get_messages(3)) == 2
    print("PASS: real SQLite-to-MemPalace migration, idempotency and source preservation")
    if not gateway:
        return
    store = StateStore(root / "state.db")
    client = AntigravityClient(store=store, contexts=ContextManager(store), memories=memory)
    try:
        assert await client.check_omp_health(), "OMP gateway is offline"
        await asyncio.to_thread(
            memory.archive, 1, "Я люблю чай. Мечтаю посетить Киото.", key="portrait-source"
        )
        memory.schedule_analysis(1, lambda system, text: client._memory_completion(1, system, text))
        await memory._tasks[1]
        assert memory.portrait(1) and not memory._pending(1)
        print("PASS: live model extraction, evidence validation and portrait persistence")
        # First generation establishes a separate test cascade.
        await collect(client, "Ответь одним словом: готово", "turn-1")
        assert store.get_conversation_id(1), "Gateway did not return a session ID"
        await asyncio.to_thread(
            memory.add_memory, 1, "Мой вымышленный питомец — дракон по имени Зефирий."
        )
        response = await collect(
            client, "Как зовут моего вымышленного дракона? Ответь только именем.", "turn-2"
        )
        assert "зефир" in response.lower(), response
        print("PASS: fresh memory reaches the model inside an existing Antigravity session")
    finally:
        await client.aclose()


async def collect(client, prompt, key):
    return "".join(
        [
            token
            async for token in client.generate_stream(
                1,
                prompt,
                memory_input={"text": prompt, "key": key, "private": True},
            )
        ]
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", action="store_true", help="Also make real OMP model requests")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="geminka-memory-check-") as temporary:
        asyncio.run(check(Path(temporary), args.gateway))

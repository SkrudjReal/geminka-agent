"""Owner's sticker exclusions and default pack distribution."""

import random

PREFERRED_PACK = "corny_columbina"
BLOCKED_UNIQUE_IDS = frozenset({
    "AgADwCAAAr3PwVM", "AgADvSEAAv0FuFM", "AgADpSQAAmS0uVM",
    "AgAD0yAAAr3AuFM", "AgAD9B4AAs8HwVM",
})
BLOCKED_POSITIONS = frozenset({3, 4, 5, 7, 8})


def allowed(item):
    return item.get("file_unique_id") not in BLOCKED_UNIQUE_IDS and not (
        item.get("set_name") == PREFERRED_PACK and item.get("pack_position") in BLOCKED_POSITIONS
    )


def pack_pool(items, rng=random, personal=()):
    items = [item for item in items if allowed(item)]
    personal = [item for item in personal if allowed(item)]
    if personal and (not items or rng.random() < 0.8):
        return personal
    preferred = [item for item in items if item.get("set_name") == PREFERRED_PACK]
    other = [item for item in items if item.get("set_name") != PREFERRED_PACK]
    if preferred and other:
        return preferred if rng.random() < 0.8 else other
    return preferred or other


async def check_delivery(bot, file_id, metadata=None):
    if metadata is None:
        file = await bot.get_file(file_id)
        metadata = {"file_unique_id": file.file_unique_id}
    if not allowed(metadata):
        raise ValueError("Этот стикер запрещён владельцем")

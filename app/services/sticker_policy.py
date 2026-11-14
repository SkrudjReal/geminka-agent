"""Owner's sticker exclusions and default pack distribution."""

import random

PREFERRED_PACK = "pk_4966866_by_Ctikerubot"
BLOCKED_UNIQUE_IDS = frozenset({
    "AgADwCAAAr3PwVM", "AgADvSEAAv0FuFM", "AgADpSQAAmS0uVM",
    "AgAD0yAAAr3AuFM", "AgAD9B4AAs8HwVM",
})


def allowed(item):
    return item.get("file_unique_id") not in BLOCKED_UNIQUE_IDS and item.get("set_name") != "corny_columbina"


def pack_pool(items, rng=random, personal=()):
    items = [item for item in items if allowed(item)]
    personal = [item for item in personal if allowed(item)]
    if personal and (not items or rng.random() < 0.8):
        return personal
    return items


async def check_delivery(bot, file_id, metadata=None):
    if metadata is None:
        file = await bot.get_file(file_id)
        metadata = {"file_unique_id": file.file_unique_id}
    if not allowed(metadata):
        raise ValueError("Этот стикер запрещён владельцем")

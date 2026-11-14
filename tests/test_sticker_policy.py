from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.sticker_policy import (
    BLOCKED_UNIQUE_IDS,
    PREFERRED_PACK,
    allowed,
    check_delivery,
    pack_pool,
)


@pytest.mark.parametrize("roll,expected", [(.799, "personal"), (.8, PREFERRED_PACK)])
def test_personal_packs_take_priority(roll, expected):
    pool = pack_pool([{"set_name": PREFERRED_PACK}],
                     SimpleNamespace(random=lambda: roll), personal=[{"set_name": "personal"}])
    assert pool[0]["set_name"] == expected


def test_blocked_personal_stickers_cannot_enter_pool():
    assert pack_pool([], personal=[{"file_unique_id": next(iter(BLOCKED_UNIQUE_IDS))}]) == []


@pytest.mark.asyncio
async def test_non_triggering_group_sticker_is_saved(monkeypatch):
    from unittest.mock import Mock

    from aiogram.types import Chat, Message, Sticker, User

    from app.bot import middlewares
    from app.services.harvester import asset_harvester
    from app.services.sticker_scanner import sticker_scanner

    user = User(id=42, first_name="Alice", is_bot=False)
    event = Message(message_id=1, date=0, chat=Chat(id=-1001, type="supergroup"), from_user=user,
                    sticker=Sticker(file_id="fid", file_unique_id="uid", type="regular",
                                    width=512, height=512, is_animated=False, is_video=False, set_name="AlicePack"))
    register, scan = Mock(), Mock()
    monkeypatch.setattr(asset_harvester, "register_sticker", register)
    monkeypatch.setattr(sticker_scanner, "schedule_scan", scan)
    monkeypatch.setattr(middlewares, "group_message_allowed", lambda *args: False)
    monkeypatch.setattr(middlewares.topic_manager, "is_group_configured", lambda _: True)
    bot, handler = SimpleNamespace(id=99), AsyncMock()
    await middlewares.OwnerAuthMiddleware()(handler, event, {"event_from_user": user, "bot": bot})
    assert register.call_args.args == (42, "fid", "uid")
    scan.assert_called_once_with(bot, "AlicePack", 42)
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_uses_only_interlocutor_packs(monkeypatch):
    from app.services import sticker_policy, streamer

    original = {"file_id": "original", "set_name": PREFERRED_PACK}
    mine = {"file_id": "mine", "set_name": "mine"}
    foreign = {"file_id": "foreign", "set_name": "foreign"}
    monkeypatch.setattr(streamer, "get_bot_stickers", lambda: [original])
    monkeypatch.setattr(streamer.asset_harvester, "get_sticker_metadata", lambda _: original)
    monkeypatch.setattr(streamer.asset_harvester, "get_user_stickers", lambda uid: [original, mine, foreign])
    monkeypatch.setattr(streamer.asset_harvester, "data", {
        "user_preferences": {"123": {"favorite_packs": ["mine"]}, "456": {"favorite_packs": ["foreign"]}}})
    monkeypatch.setattr(sticker_policy.random, "random", lambda: 0)
    assert await streamer.current_sticker_file_id(SimpleNamespace(), "original", 123) == "mine"


@pytest.mark.parametrize("roll,preferred", [(0, True), (.7999, True), (.8, False), (.99, False)])
def test_pack_probability(roll, preferred):
    pool = pack_pool([{"set_name": PREFERRED_PACK}, {"set_name": "original"}],
                     SimpleNamespace(random=lambda: roll))
    assert (pool[0]["set_name"] == PREFERRED_PACK) is preferred


@pytest.mark.parametrize("unique", list(BLOCKED_UNIQUE_IDS))
def test_blocked_ids_cannot_enter_pool(unique):
    item = {"file_unique_id": unique, "set_name": PREFERRED_PACK}
    assert not allowed(item)
    assert pack_pool([item]) == []


@pytest.mark.parametrize("position", [3, 4, 5, 7, 8])
def test_blocked_pack_positions(position):
    assert not allowed({"set_name": PREFERRED_PACK, "pack_position": position})
    assert allowed({"set_name": "original", "pack_position": position})


@pytest.mark.asyncio
async def test_direct_unknown_file_id_is_checked_before_send():
    bot = SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(file_unique_id=next(iter(BLOCKED_UNIQUE_IDS)))))
    with pytest.raises(ValueError):
        await check_delivery(bot, "opaque-bot-scoped-file-id")
    bot.get_file.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("roll,expected", [(.79, "live-new"), (.8, "live-old")])
async def test_delivery_applies_pack_weight_to_selected_sticker(monkeypatch, roll, expected):
    from app.services import sticker_policy, streamer

    old = {"file_id": "old", "file_unique_id": "old-id", "set_name": "original"}
    new = {"file_id": "new", "file_unique_id": "new-id", "set_name": PREFERRED_PACK}
    monkeypatch.setattr(streamer, "get_bot_stickers", lambda: [old, new])
    monkeypatch.setattr(streamer.asset_harvester, "get_sticker_metadata", lambda _: old)
    monkeypatch.setattr(streamer, "_LIVE_STICKER_IDS", {})
    monkeypatch.setattr(sticker_policy.random, "random", lambda: roll)

    async def get_set(name):
        selected = new if name == PREFERRED_PACK else old
        return SimpleNamespace(stickers=[SimpleNamespace(file_unique_id=selected["file_unique_id"],
                                                        file_id="live-" + selected["file_id"])])

    bot = SimpleNamespace(id=1, get_sticker_set=get_set)
    assert await streamer.current_sticker_file_id(bot, "old") == expected

"""Tests for StickerPackScanner service: grid rendering, badge numbering, and JSON catalog updates."""

import json
from pathlib import Path
from PIL import Image

import pytest

from app.services.sticker_scanner import StickerPackScanner


@pytest.fixture
def temp_scanner(tmp_path: Path):
    assets_file = tmp_path / "user_assets.json"
    bot_stickers_file = tmp_path / "bot_stickers.json"
    cache_dir = tmp_path / "stickers_cache"

    assets_file.write_text(json.dumps({
        "stickers": {
            "test_fid_1": {
                "file_id": "test_fid_1",
                "file_unique_id": "unique_1",
                "emoji": "🌸",
                "set_name": "TestPack",
                "tags": ["initial_tag"]
            }
        },
        "sticker_packs": {}
    }), encoding="utf-8")

    bot_stickers_file.write_text(json.dumps([
        {
            "index": 0,
            "file_id": "test_fid_1",
            "file_unique_id": "unique_1",
            "emoji": "🌸",
            "description": "old description",
            "tags": ["old_tag"]
        }
    ]), encoding="utf-8")

    scanner = StickerPackScanner(
        assets_file=assets_file,
        bot_stickers_file=bot_stickers_file,
        cache_dir=cache_dir,
    )
    return scanner, assets_file, bot_stickers_file, tmp_path


def test_render_grid_sheet(temp_scanner):
    scanner, _, _, tmp_path = temp_scanner

    # Create dummy sticker images
    items = []
    for i in range(1, 7):
        img_path = tmp_path / f"sticker_{i}.png"
        img = Image.new("RGBA", (100, 100), (255, i * 30, 100, 255))
        img.save(img_path)
        items.append((i, img_path, "✨"))

    out_grid = scanner.render_grid_sheet(items, "TestPack", sheet_index=1, tile_size=150, cols=3)

    assert out_grid.exists()
    assert out_grid.stat().st_size > 0

    with Image.open(out_grid) as grid_img:
        # 3 cols, 2 rows (6 items)
        assert grid_img.width > 3 * 150
        assert grid_img.height > 2 * 150


def test_save_scan_results(temp_scanner):
    scanner, assets_file, bot_stickers_file, tmp_path = temp_scanner

    summary = (
        "Уникальный авторский набор стикеров, объединяющий эстетику ретро-аниме и современные интернет-мемы. "
        "Каждый стикер передаёт выразительную эмоциональную реакцию, от смущённой нежности до игривой дерзости, "
        "что делает его идеальным инструментом для глубокого и живого общения в Telegram."
    )

    metadata = [
        {
            "file_id": "test_fid_1",
            "file_unique_id": "unique_1",
            "emoji": "🌸",
            "description": "Нежная Коломбина улыбается сквозь кружевную чёлку",
            "tags": ["коломбина", "нежность", "милота"],
            "grid_index": 1,
        }
    ]

    grid_path = tmp_path / "TestPack_sheet_1.png"
    grid_path.write_bytes(b"PNGFAKE")

    scanner.save_scan_results(
        set_name="TestPack",
        set_title="Тестовый Пак",
        stickers_metadata=metadata,
        pack_summary=summary,
        grid_paths=[grid_path],
    )

    # Verify user_assets.json
    with open(assets_file, encoding="utf-8") as f:
        ua = json.load(f)

    pack = ua["sticker_packs"]["TestPack"]
    assert pack["scanned"] is True
    assert pack["title"] == "Тестовый Пак"
    assert pack["summary"] == summary
    assert len(pack["grid_images"]) == 1

    sticker = ua["stickers"]["test_fid_1"]
    assert sticker["description"] == "Нежная Коломбина улыбается сквозь кружевную чёлку"
    assert "коломбина" in sticker["tags"]
    assert "initial_tag" in sticker["tags"]
    assert sticker["grid_index"] == 1

    # Verify bot_stickers.json
    with open(bot_stickers_file, encoding="utf-8") as f:
        bs = json.load(f)

    assert bs[0]["description"] == "Нежная Коломбина улыбается сквозь кружевную чёлку"
    assert "коломбина" in bs[0]["tags"]
    assert "old_tag" in bs[0]["tags"]


@pytest.mark.asyncio
async def test_skip_already_scanned(temp_scanner):
    scanner, assets_file, _, _ = temp_scanner

    # Mark TestPack as already scanned with summary
    with open(assets_file, "r+", encoding="utf-8") as f:
        ua = json.load(f)
        ua["sticker_packs"]["AlreadyScanned"] = {
            "scanned": True,
            "summary": "Existing summary"
        }
        f.seek(0)
        json.dump(ua, f)
        f.truncate()

    # Pass dummy bot
    res = await scanner.scan_sticker_pack(None, "AlreadyScanned", force=False)
    assert res is True

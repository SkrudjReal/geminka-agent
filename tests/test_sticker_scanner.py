"""Tests for StickerPackScanner service: grid rendering, badge numbering, and JSON catalog updates."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.services.harvester import AssetHarvester
from app.services.sticker_scanner import StickerPackScanner


async def test_failed_vision_does_not_invent_description(temp_scanner, monkeypatch):
    scanner, assets, _, tmp = temp_scanner
    frame = tmp / 'frame.png'
    Image.new('RGBA', (20, 20)).save(frame)

    async def download(*args):
        return frame

    async def vision(**kwargs):
        raise ValueError('missing sticker descriptions')

    async def get_set(name):
        return SimpleNamespace(title=name, stickers=[SimpleNamespace(
            file_id='missing', file_unique_id='unique', emoji='🌟')])

    async def no_sleep(*args):
        pass

    monkeypatch.setattr(scanner, 'download_sticker_frame', download)
    monkeypatch.setattr(scanner, 'scan_grid_with_vision', vision)
    monkeypatch.setattr('app.services.sticker_scanner.asyncio.sleep', no_sleep)
    assert not await scanner.scan_sticker_pack(SimpleNamespace(get_sticker_set=get_set), 'Failed')
    data = json.loads(assets.read_text())
    assert not data['sticker_packs']['Failed']['scanned']
    assert 'missing' not in data['stickers']


def test_harvester_write_preserves_scanner_descriptions(temp_scanner):
    scanner, assets, _, _ = temp_scanner
    harvester = AssetHarvester(assets)
    scanner.save_scan_results('TestPack', 'Test', [{
        'file_id':'test_fid_1', 'file_unique_id':'unique_1',
        'description':'Description added in background', 'emoji':'🌸'
    }], 'summary', [])
    harvester.record_sent_sticker(1, 'test_fid_1')
    assert json.loads(assets.read_text())['stickers']['test_fid_1']['description'] == 'Description added in background'


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
            "fully_synced": True,
            "summary": "Existing summary"
        }
        f.seek(0)
        json.dump(ua, f)
        f.truncate()

    # Pass dummy bot
    res = await scanner.scan_sticker_pack(None, "AlreadyScanned", force=False)
    assert res is True


def test_harvester_refreshes_json_written_by_scanner(tmp_path: Path):
    assets_file = tmp_path / "user_assets.json"
    bot_stickers_file = tmp_path / "bot_stickers.json"
    assets_file.write_text(
        json.dumps(
            {
                "stickers": {
                    "fid": {
                        "file_id": "fid",
                        "file_unique_id": "uid",
                        "emoji": "✨",
                        "set_name": "Pack",
                    }
                },
                "sticker_packs": {},
            }
        ),
        encoding="utf-8",
    )
    harvester = AssetHarvester(assets_file)
    scanner = StickerPackScanner(assets_file, bot_stickers_file, tmp_path / "cache")

    scanner.save_scan_results(
        set_name="Pack",
        set_title="Pack",
        stickers_metadata=[
            {
                "file_id": "fid",
                "file_unique_id": "uid",
                "emoji": "✨",
                "description": "Персонаж смотрит с укором",
                "tags": ["осуждение"],
            }
        ],
        pack_summary="Pack summary",
        grid_paths=[],
    )

    metadata = harvester.get_sticker_metadata("fid", "uid")
    assert metadata is not None
    assert metadata["description"] == "Персонаж смотрит с укором"


def test_save_scan_results_populates_empty_bot_catalog(tmp_path: Path):
    assets_file = tmp_path / "user_assets.json"
    bot_stickers_file = tmp_path / "bot_stickers.json"
    assets_file.write_text(json.dumps({"stickers": {}, "sticker_packs": {}}), encoding="utf-8")
    bot_stickers_file.write_text("[]", encoding="utf-8")
    scanner = StickerPackScanner(assets_file, bot_stickers_file, tmp_path / "cache")

    scanner.save_scan_results(
        set_name="Pack",
        set_title="Pack",
        stickers_metadata=[
            {
                "file_id": "fid",
                "file_unique_id": "uid",
                "emoji": "✨",
                "description": "Новый стикер",
                "tags": ["новый"],
            }
        ],
        pack_summary="Pack summary",
        grid_paths=[],
    )

    catalog = json.loads(bot_stickers_file.read_text(encoding="utf-8"))
    assert catalog[0]["file_unique_id"] == "uid"
    assert catalog[0]["description"] == "Новый стикер"

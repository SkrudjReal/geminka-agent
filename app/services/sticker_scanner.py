"""Sticker & Custom Emoji Pack Scanner with Visual Grid (Contact Sheet) Generation.

Downloads entire sticker/emoji sets from Telegram, combines tiles into numbered
contact sheets with visual badges, and performs multimodal AI scanning to generate
per-item descriptions, tags, and a comprehensive ~120-word pack summary.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.core import config
from app.core.files import atomic_write_json, load_json

logger = logging.getLogger("geminka-scanner")

# Default grid rendering parameters
DEFAULT_TILE_SIZE = 240
DEFAULT_COLS = 5
DEFAULT_MAX_PER_SHEET = 25
DEFAULT_PADDING = 14
DEFAULT_BG_COLOR = (24, 24, 37, 255)  # Mocha / Dark Slate
DEFAULT_BADGE_BG = (17, 17, 27, 230)
DEFAULT_BADGE_OUTLINE = (137, 180, 250, 255)  # Soft cyan/blue
DEFAULT_TEXT_COLOR = (245, 224, 220, 255)


class StickerPackScanner:
    def __init__(
        self,
        assets_file: Path = config.USER_ASSETS_FILE,
        bot_stickers_file: Path = config.STICKERS_FILE,
        cache_dir: Path = config.STICKERS_CACHE_DIR,
    ):
        self.assets_file = assets_file
        self.bot_stickers_file = bot_stickers_file
        self.cache_dir = cache_dir
        self.grids_dir = cache_dir / "grids"
        self.grids_dir.mkdir(parents=True, exist_ok=True)
        self._pack_locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, set_name: str) -> asyncio.Lock:
        if set_name not in self._pack_locks:
            self._pack_locks[set_name] = asyncio.Lock()
        return self._pack_locks[set_name]

    def _load_font(self, size: int = 20) -> ImageFont.ImageFont:
        font_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]
        for candidate in font_candidates:
            if os.path.exists(candidate):
                try:
                    return ImageFont.truetype(candidate, size)
                except Exception:
                    pass
        return ImageFont.load_default()

    async def download_sticker_frame(self, bot: Any, sticker: Any) -> Optional[Path]:
        """Downloads a Telegram sticker and converts it to a standard PNG frame."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        unique_id = getattr(sticker, "file_unique_id", None) or getattr(sticker, "file_id", "sticker")
        png_path = self.cache_dir / f"{unique_id}.png"

        if png_path.exists() and png_path.stat().st_size > 0:
            return png_path

        # Determine file extension based on sticker format
        is_video = getattr(sticker, "is_video", False)
        is_animated = getattr(sticker, "is_animated", False)

        try:
            file_obj = await bot.get_file(sticker.file_id)
            if not file_obj.file_path:
                return None

            raw_ext = ".webm" if is_video else (".tgs" if is_animated else ".webp")
            raw_path = self.cache_dir / f"{unique_id}{raw_ext}"

            await bot.download_file(file_obj.file_path, raw_path)

            if is_video or raw_ext == ".webm":
                # Extract first frame via ffmpeg
                cmd = [
                    "ffmpeg", "-y", "-i", str(raw_path),
                    "-vframes", "1", str(png_path)
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
                if png_path.exists() and png_path.stat().st_size > 0:
                    return png_path
            elif not is_animated:
                # Static WebP -> PNG conversion
                with Image.open(raw_path) as img:
                    img.convert("RGBA").save(png_path, "PNG")
                return png_path
            else:
                # TGS format: check if thumbnail exists
                thumb = getattr(sticker, "thumbnail", None)
                if thumb and getattr(thumb, "file_id", None):
                    thumb_obj = await bot.get_file(thumb.file_id)
                    if thumb_obj.file_path:
                        await bot.download_file(thumb_obj.file_path, png_path)
                        return png_path
        except Exception as e:
            logger.warning(f"Error downloading/converting sticker {unique_id}: {e}")

        return None

    def render_grid_sheet(
        self,
        items: List[Tuple[int, Path, str]],
        set_name: str,
        sheet_index: int,
        tile_size: int = DEFAULT_TILE_SIZE,
        cols: int = DEFAULT_COLS,
    ) -> Path:
        """Renders a single contact sheet grid with numbered badges (#1, #2, ...) for each item."""
        count = len(items)
        if count == 0:
            raise ValueError("No items to render in grid sheet")

        rows = math.ceil(count / cols)
        pad = DEFAULT_PADDING
        grid_w = cols * tile_size + (cols + 1) * pad
        grid_h = rows * tile_size + (rows + 1) * pad

        sheet_img = Image.new("RGBA", (grid_w, grid_h), DEFAULT_BG_COLOR)
        draw = ImageDraw.Draw(sheet_img)
        font = self._load_font(size=22)

        for rel_idx, (global_idx, img_path, emoji_char) in enumerate(items):
            c = rel_idx % cols
            r = rel_idx // cols
            x = pad + c * (tile_size + pad)
            y = pad + r * (tile_size + pad)

            # Paste sticker centered in the tile cell
            try:
                if img_path and img_path.exists():
                    with Image.open(img_path) as tile:
                        tile = tile.convert("RGBA")
                        tile.thumbnail((tile_size - 16, tile_size - 16), Image.Resampling.LANCZOS)
                        ox = x + (tile_size - tile.width) // 2
                        oy = y + (tile_size - tile.height) // 2
                        sheet_img.paste(tile, (ox, oy), tile)
            except Exception as exc:
                logger.warning(f"Failed to paste tile #{global_idx} ({img_path}): {exc}")

            # Draw visual badge in the top-left corner
            badge_text = f"#{global_idx}"
            bbox = draw.textbbox((0, 0), badge_text, font=font)
            bw = bbox[2] - bbox[0] + 16
            bh = 30
            bx = x + 6
            by = y + 6

            draw.rounded_rectangle(
                [bx, by, bx + bw, by + bh],
                radius=6,
                fill=DEFAULT_BADGE_BG,
                outline=DEFAULT_BADGE_OUTLINE,
                width=2,
            )
            draw.text((bx + 8, by + 4), badge_text, fill=DEFAULT_TEXT_COLOR, font=font)

        out_path = self.grids_dir / f"{set_name}_sheet_{sheet_index}.png"
        sheet_img.save(out_path, "PNG")
        logger.info(f"Rendered grid sheet {out_path.name} with {count} items.")
        return out_path

    async def scan_grid_with_vision(
        self,
        grid_path: Path,
        items_on_sheet: List[Tuple[int, str]],  # (global_index, emoji)
        set_title: str,
        set_name: str,
        is_first_sheet: bool = True,
    ) -> Dict[str, Any]:
        """Calls Gemini / Antigravity multimodal vision to analyze the numbered sticker grid."""
        items_desc = ", ".join([f"#{idx} ({emoji})" for idx, emoji in items_on_sheet])

        pack_summary_prompt = ""
        if is_first_sheet:
            pack_summary_prompt = (
                "2. Составить общее связное описание всего стикерпака в целом: ровно 1 развёрнутый абзац примерно на 100-140 слов (~120 слов), "
                "детально описывающий визуальный стиль, атмосферу, происхождение персонажей (аниме, игра, мем, реализм) и то, "
                "для каких диалоговых ситуаций этот пак лучше всего подходит."
            )

        pack_summary_json = ""
        if is_first_sheet:
            pack_summary_json = '"pack_summary": "Развёрнутый связный абзац примерно на 120 слов с описанием стиля, тематики, происхождения и ситуаций использования всего стикерпака...",'

        first_idx = items_on_sheet[0][0] if items_on_sheet else 1

        prompt = f"""[VISION АНАЛИЗ ПРОНУМЕРОВАННОЙ СЕТКИ СТИКЕРОВ / КАСТОМНЫХ ЭМОДЗИ]
Файл изображения на диске: {grid_path.resolve()}
Стикерпак: «{set_title}» (ID: {set_name}).
На этой картинке расположена сетка стикеров. В левом верхнем углу каждого стикера нарисован номер с решёткой (#{items_on_sheet[0][0]} .. #{items_on_sheet[-1][0]}).
Стикеры на этом листе: {items_desc}.

Твоя задача:
1. Описать КАЖДЫЙ стикер по его номеру:
   - Кто персонаж / мем / объект
   - Какая эмоция, действие, поза или надпись на стикере
   - Короткое и точное описание (1 предложение)
   - 3-5 ключевых тегов (персонаж, эмоция, контекст)

{pack_summary_prompt}

Ответь ИСКЛЮЧИТЕЛЬНО в формате строгого валидного JSON (без лишнего текста вокруг):
{{
  {pack_summary_json}
  "stickers": [
    {{
      "index": {first_idx},
      "character": "Имя персонажа или суть объекта",
      "description": "Краткое точное описание происходящего на стикере",
      "tags": ["тег1", "тег2", "тег3"]
    }}
  ]
}}
"""
        # Call OMP Gateway
        base_url = config.settings.omp_base_url.rstrip("/")
        url = f"{base_url}/chat/completions" if not base_url.endswith("/v1") else f"{base_url}/chat/completions"

        payload = {
            "model": config.settings.default_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "max_tokens": 4096,
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"OMP Vision HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            raw_content = data["choices"][0]["message"]["content"]

        # Parse JSON output from model response
        cleaned = raw_content.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

        try:
            parsed = json.loads(cleaned)
            return parsed
        except Exception as err:
            logger.warning(f"Failed to parse vision JSON for {grid_path.name}: {err}\nRaw:\n{cleaned[:300]}")
            # Fallback regex extraction of pack_summary if JSON was slightly malformed
            summary_match = re.search(r'"pack_summary"\s*:\s*"([^"]+)"', cleaned)
            return {
                "pack_summary": summary_match.group(1) if summary_match else "",
                "stickers": [],
            }

    def save_scan_results(
        self,
        set_name: str,
        set_title: str,
        stickers_metadata: List[Dict[str, Any]],
        pack_summary: str,
        grid_paths: List[Path],
    ) -> None:
        """Atomically persists scan descriptions and pack summary to user_assets.json and bot_stickers.json."""
        # 1. Update user_assets.json
        user_assets = load_json(self.assets_file, {})
        user_assets.setdefault("stickers", {})
        user_assets.setdefault("sticker_packs", {})

        now = time.time()
        p_info = user_assets["sticker_packs"].setdefault(set_name, {})
        p_info["set_name"] = set_name
        p_info["title"] = set_title or p_info.get("title", set_name)
        p_info["sticker_count"] = len(stickers_metadata)
        p_info["scanned"] = True
        p_info["scanned_at"] = now
        p_info["fully_synced"] = True
        p_info["last_sync"] = now
        p_info["grid_images"] = [str(p.resolve()) for p in grid_paths]
        if pack_summary:
            p_info["summary"] = pack_summary
            p_info["description"] = pack_summary

        # Index sticker entries by file_id
        for sm in stickers_metadata:
            fid = sm.get("file_id")
            if not fid:
                continue
            entry = user_assets["stickers"].setdefault(fid, {})
            entry["file_id"] = fid
            entry["file_unique_id"] = sm.get("file_unique_id", "")
            entry["emoji"] = sm.get("emoji", "✨")
            entry["set_name"] = set_name
            if sm.get("description"):
                entry["description"] = sm["description"]
            if sm.get("tags"):
                existing_tags = set(entry.get("tags", []))
                existing_tags.update(sm["tags"])
                entry["tags"] = sorted(list(existing_tags))
            if "grid_index" in sm:
                entry["grid_index"] = sm["grid_index"]

        atomic_write_json(self.assets_file, user_assets)
        logger.info(f"Updated user_assets.json for pack '{set_name}' ({len(stickers_metadata)} stickers).")

        # 2. Update bot_stickers.json if it exists and contains items from this pack
        if self.bot_stickers_file.exists():
            try:
                bot_stickers = load_json(self.bot_stickers_file, [])
                if isinstance(bot_stickers, list):
                    modified = False
                    meta_by_unique = {sm["file_unique_id"]: sm for sm in stickers_metadata if sm.get("file_unique_id")}
                    meta_by_fid = {sm["file_id"]: sm for sm in stickers_metadata if sm.get("file_id")}

                    for bs in bot_stickers:
                        fuid = bs.get("file_unique_id")
                        fid = bs.get("file_id")
                        match = meta_by_unique.get(fuid) or meta_by_fid.get(fid)
                        if match:
                            if match.get("description"):
                                bs["description"] = match["description"]
                            if match.get("tags"):
                                existing_tags = set(bs.get("tags", []))
                                existing_tags.update(match["tags"])
                                bs["tags"] = sorted(list(existing_tags))
                            modified = True

                    if modified:
                        atomic_write_json(self.bot_stickers_file, bot_stickers)
                        logger.info(f"Updated bot_stickers.json with enriched scan descriptions.")
            except Exception as e:
                logger.warning(f"Failed to update bot_stickers.json: {e}")

    async def scan_sticker_pack(
        self,
        bot: Any,
        set_name: str,
        user_id: Optional[int] = None,
        force: bool = False,
    ) -> bool:
        """Full pipeline: downloads set, generates numbered contact sheet grids, scans with Vision AI, and saves."""
        if not set_name or set_name == "unknown":
            return False

        lock = self._get_lock(set_name)
        async with lock:
            user_assets = load_json(self.assets_file, {})
            p_info = user_assets.get("sticker_packs", {}).get(set_name, {})
            if not force and p_info.get("scanned") and p_info.get("summary"):
                logger.debug(f"Pack '{set_name}' is already scanned. Skipping.")
                return True

            logger.info(f"Starting visual scan pipeline for pack '{set_name}'...")

            try:
                sticker_set = await bot.get_sticker_set(set_name)
            except Exception as e:
                logger.warning(f"Could not fetch sticker set '{set_name}' from Telegram: {e}")
                return False

            stickers = sticker_set.stickers
            if not stickers:
                logger.warning(f"Sticker set '{set_name}' has 0 stickers.")
                return False

            set_title = getattr(sticker_set, "title", set_name)
            logger.info(f"Pack '{set_title}' ({set_name}) contains {len(stickers)} stickers. Downloading frames...")

            # 1. Download & convert all stickers to PNG frames
            items_to_render: List[Tuple[int, Path, str, Any]] = []
            for idx, s in enumerate(stickers, start=1):
                png_path = await self.download_sticker_frame(bot, s)
                emoji_char = s.emoji or "✨"
                items_to_render.append((idx, png_path, emoji_char, s))

            # 2. Partition into contact sheets (e.g. 25 per sheet)
            batch_size = DEFAULT_MAX_PER_SHEET
            num_sheets = math.ceil(len(items_to_render) / batch_size)
            grid_paths: List[Path] = []
            overall_pack_summary = ""
            enriched_stickers: List[Dict[str, Any]] = []

            for sheet_idx in range(1, num_sheets + 1):
                start_i = (sheet_idx - 1) * batch_size
                end_i = start_i + batch_size
                batch = items_to_render[start_i:end_i]

                sheet_tuples = [(idx, p, em) for (idx, p, em, _) in batch]
                grid_path = self.render_grid_sheet(sheet_tuples, set_name, sheet_idx)
                grid_paths.append(grid_path)

                # 3. Vision scanning
                items_on_sheet = [(idx, em) for (idx, _, em, _) in batch]
                scan_res = await self.scan_grid_with_vision(
                    grid_path=grid_path,
                    items_on_sheet=items_on_sheet,
                    set_title=set_title,
                    set_name=set_name,
                    is_first_sheet=(sheet_idx == 1),
                )

                if sheet_idx == 1 and scan_res.get("pack_summary"):
                    overall_pack_summary = scan_res["pack_summary"].strip()

                parsed_by_index = {item.get("index"): item for item in scan_res.get("stickers", [])}

                for idx, png_path, emoji_char, sticker_obj in batch:
                    item_scan = parsed_by_index.get(idx, {})
                    char_name = item_scan.get("character") or ""
                    desc = item_scan.get("description") or ""
                    if char_name and desc:
                        full_desc = f"{char_name}: {desc}"
                    else:
                        full_desc = desc or char_name or f"Стикер {emoji_char}"

                    tags = item_scan.get("tags") or []
                    if emoji_char and emoji_char not in tags:
                        tags.append(emoji_char)

                    enriched_stickers.append({
                        "file_id": sticker_obj.file_id,
                        "file_unique_id": sticker_obj.file_unique_id,
                        "emoji": emoji_char,
                        "description": full_desc,
                        "tags": tags,
                        "grid_index": idx,
                    })

            # Fallback if pack_summary was missing
            if not overall_pack_summary:
                overall_pack_summary = (
                    f"Коллекция стикеров «{set_title}» содержит {len(stickers)} выразительных фрагментов. "
                    f"Набор выполнен в едином визуальном стиле и охватывает широкий спектр живых реакций, "
                    f"эмоциональных акцентов и характерных жестов, идеально подходящих для оживлённой переписки."
                )

            # 4. Atomically persist to JSON
            self.save_scan_results(
                set_name=set_name,
                set_title=set_title,
                stickers_metadata=enriched_stickers,
                pack_summary=overall_pack_summary,
                grid_paths=grid_paths,
            )

            logger.info(f"✅ Successfully scanned and enriched pack '{set_name}' with {len(enriched_stickers)} items.")
            return True


sticker_scanner = StickerPackScanner()

#!/usr/bin/env python3
"""Downloads the columbina_by_geminka sticker pack from Telegram, converts to PNG, and creates a ZIP archive."""

import asyncio
import io
import os
import shutil
import sys
import zipfile
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from aiogram import Bot
from app.core import config

PACK_NAME = "columbina_by_geminka"
OUTPUT_DIR = Path("data") / PACK_NAME
ZIP_PATH = Path("data") / f"{PACK_NAME}.zip"


async def main():
    print(f"Connecting to Telegram to fetch pack: {PACK_NAME}...")
    bot = Bot(token=config.settings.bot_token)

    try:
        s_set = await bot.get_sticker_set(PACK_NAME)
        print(f"Pack Title: {s_set.title}")
        print(f"Total Stickers: {len(s_set.stickers)}")

        if OUTPUT_DIR.exists():
            shutil.rmtree(OUTPUT_DIR)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        png_dir = OUTPUT_DIR / "png"
        webp_dir = OUTPUT_DIR / "webp"
        png_dir.mkdir(exist_ok=True)
        webp_dir.mkdir(exist_ok=True)

        download_slots = asyncio.Semaphore(5)

        async def process_sticker(index: int, sticker):
            async with download_slots:
                file_obj = await bot.get_file(sticker.file_id)
                if not file_obj.file_path:
                    print(f"Error: no file_path for #{index}")
                    return None

                raw_bytes = io.BytesIO()
                await bot.download_file(file_obj.file_path, raw_bytes)
                raw_bytes.seek(0)

                emoji_char = sticker.emoji or "✨"
                base_name = f"{index:02d}_columbina_{sticker.file_unique_id}"

                # Save original webp
                webp_file = webp_dir / f"{base_name}.webp"
                webp_file.write_bytes(raw_bytes.getvalue())

                # Convert to PNG with RGBA
                raw_bytes.seek(0)
                png_file = png_dir / f"{base_name}.png"
                with Image.open(raw_bytes) as img:
                    img.convert("RGBA").save(png_file, "PNG")

                return {
                    "index": index,
                    "emoji": emoji_char,
                    "unique_id": sticker.file_unique_id,
                    "png": png_file.name,
                    "webp": webp_file.name,
                }

        tasks = [process_sticker(i, s) for i, s in enumerate(s_set.stickers, start=1)]
        results = await asyncio.gather(*tasks)

        valid_results = [r for r in results if r]
        print(f"Successfully processed {len(valid_results)}/{len(s_set.stickers)} stickers.")

        # Create ZIP archive
        print(f"Creating ZIP archive at {ZIP_PATH}...")
        if ZIP_PATH.exists():
            ZIP_PATH.unlink()

        with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
            # Put PNG files directly in root for easy viewing
            for r in valid_results:
                png_p = png_dir / r["png"]
                zf.write(png_p, arcname=f"png/{r['png']}")
                webp_p = webp_dir / r["webp"]
                zf.write(webp_p, arcname=f"webp/{r['webp']}")

            # Also create a summary index.txt
            summary_lines = [
                f"Стикерпак: {s_set.title} (@{PACK_NAME})",
                f"Всего стикеров: {len(valid_results)}",
                "",
                "Список файлов:",
            ]
            for r in valid_results:
                summary_lines.append(f"{r['index']:02d}. {r['emoji']} -> png/{r['png']}")

            zf.writestr("info.txt", "\n".join(summary_lines) + "\n")

        size_mb = ZIP_PATH.stat().st_size / (1024 * 1024)
        print(f"ZIP archive created successfully: {ZIP_PATH} ({size_mb:.2f} MB)")

    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

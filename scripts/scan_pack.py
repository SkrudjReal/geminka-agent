#!/usr/bin/env python3
"""CLI utility to scan Telegram sticker and custom emoji packs into numbered grids with Vision AI."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot
from app.core import config
from app.core.files import load_json
from app.services.sticker_scanner import sticker_scanner


async def main():
    parser = argparse.ArgumentParser(description="Scan Telegram sticker pack into contact sheets with Vision AI.")
    parser.add_argument("pack_name", nargs="?", help="Telegram sticker pack name/slug (e.g. DistortionOfSubjectivePerception)")
    parser.add_argument("--force", action="store_true", help="Force rescan even if already scanned")
    parser.add_argument("--all-unscanned", action="store_true", help="Scan all unscanned packs from data/user_assets.json")
    args = parser.parse_args()

    token = config.settings.telegram_bot_token.get_secret_value()
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN is not configured.")
        sys.exit(1)

    bot = Bot(token=token)

    try:
        if args.all_unscanned:
            user_assets = load_json(config.USER_ASSETS_FILE, {})
            packs = user_assets.get("sticker_packs", {})
            print(f"Checking {len(packs)} packs in user assets...")
            for set_name, p_info in packs.items():
                if args.force or not p_info.get("scanned"):
                    print(f"\n--- Scanning pack: {set_name} ---")
                    success = await sticker_scanner.scan_sticker_pack(bot, set_name, force=args.force)
                    print(f"Result for {set_name}: {'SUCCESS' if success else 'FAILED'}")
                else:
                    print(f"Skipping {set_name} (already scanned).")
        elif args.pack_name:
            print(f"Scanning pack: {args.pack_name} (force={args.force})...")
            success = await sticker_scanner.scan_sticker_pack(bot, args.pack_name, force=args.force)
            if success:
                print(f"\n✅ Pack '{args.pack_name}' successfully scanned and saved!")
            else:
                print(f"\n❌ Failed to scan pack '{args.pack_name}'.")
                sys.exit(1)
        else:
            parser.print_help()
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

"""User Asset Harvester & Dynamic Emoji/Sticker Memory for Geminka.

Collects, catalogs, and stores:
1. Telegram Premium Custom Emojis sent by the user (IDs, characters, set_names).
2. Sticker packs and individual stickers sent by the user.

Allows Columbina to dynamically mirror and use the user's own custom emojis and sticker packs!
"""

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core import config
from app.core.files import atomic_write_json, load_json

logger = logging.getLogger("geminka-assets")

USER_ASSETS_FILE = config.USER_ASSETS_FILE


class AssetHarvester:
    def __init__(self, storage_file: Path = USER_ASSETS_FILE):
        self.storage_file = storage_file
        self.data: Dict[str, Any] = {
            "custom_emojis": {},  # custom_emoji_id -> {id, emoji, set_name, count, last_used, users: []}
            "stickers": {},        # file_id -> {file_id, emoji, set_name, count, last_used, tags: [], users: []}
            "sticker_packs": {},  # set_name -> {set_name, sample_file_id, sticker_count, last_used}
            "user_preferences": {},  # user_id -> {favorite_emojis: [], favorite_packs: []}
        }
        self.load()

    def load(self) -> None:
        if self.storage_file.exists():
            try:
                saved = load_json(self.storage_file, {})
                for k in ["custom_emojis", "stickers", "sticker_packs", "user_preferences"]:
                    if k in saved:
                        self.data[k] = saved[k]
                logger.info(
                    f"AssetHarvester loaded {len(self.data['custom_emojis'])} custom emojis, "
                    f"{len(self.data['stickers'])} stickers, {len(self.data['sticker_packs'])} packs."
                )
            except Exception as e:
                logger.warning(f"Failed to load user assets: {e}")

    def save(self) -> None:
        try:
            atomic_write_json(self.storage_file, self.data)
        except Exception as e:
            logger.warning(f"Failed to save user assets: {e}")

    def register_custom_emoji(
        self,
        user_id: int,
        custom_emoji_id: str,
        emoji_char: str = "✨",
        set_name: Optional[str] = None,
    ) -> None:
        """Records a custom emoji sent by user."""
        cid = str(custom_emoji_id).strip()
        if not cid:
            return

        now = time.time()
        uid_str = str(user_id)

        if cid not in self.data["custom_emojis"]:
            self.data["custom_emojis"][cid] = {
                "custom_emoji_id": cid,
                "emoji": emoji_char or "✨",
                "set_name": set_name or "",
                "count": 1,
                "first_seen": now,
                "last_used": now,
                "users": [uid_str],
            }
        else:
            item = self.data["custom_emojis"][cid]
            item["count"] = item.get("count", 0) + 1
            item["last_used"] = now
            if emoji_char and (not item.get("emoji") or item.get("emoji") == "✨"):
                item["emoji"] = emoji_char
            if set_name and not item.get("set_name"):
                item["set_name"] = set_name
            if uid_str not in item.get("users", []):
                item.setdefault("users", []).append(uid_str)

        # Track in user preferences
        u_pref = self.data["user_preferences"].setdefault(uid_str, {"favorite_emojis": [], "favorite_packs": []})
        if cid not in u_pref["favorite_emojis"]:
            u_pref["favorite_emojis"].append(cid)

        self.save()

    def register_sticker(
        self,
        user_id: int,
        file_id: str,
        emoji: str = "🌸",
        emoji_char: Optional[str] = None,
        set_name: Optional[str] = None,
        is_animated: bool = False,
        is_video: bool = False,
    ) -> None:
        """Records a sticker sent by user."""
        emoji_val = emoji_char or emoji or "🌸"
        fid = str(file_id).strip()
        if not fid:
            return

        now = time.time()
        uid_str = str(user_id)
        pack_name = set_name or "unknown_pack"

        if fid not in self.data["stickers"]:
            self.data["stickers"][fid] = {
                "file_id": fid,
                "emoji": emoji_val,
                "set_name": pack_name,
                "is_animated": is_animated,
                "is_video": is_video,
                "count": 1,
                "first_seen": now,
                "last_used": now,
                "users": [uid_str],
                "tags": [emoji_val] if emoji_val else [],
            }
        else:
            item = self.data["stickers"][fid]
            item["count"] = item.get("count", 0) + 1
            item["last_used"] = now
            if emoji_val and emoji_val not in item.get("tags", []):
                item.setdefault("tags", []).append(emoji_val)
            if uid_str not in item.get("users", []):
                item.setdefault("users", []).append(uid_str)

        # Track pack info
        if pack_name:
            if pack_name not in self.data["sticker_packs"]:
                self.data["sticker_packs"][pack_name] = {
                    "set_name": pack_name,
                    "sample_file_id": fid,
                    "sticker_count": 1,
                    "last_used": now,
                }
            else:
                p_item = self.data["sticker_packs"][pack_name]
                p_item["sticker_count"] = p_item.get("sticker_count", 0) + 1
                p_item["last_used"] = now

            u_pref = self.data["user_preferences"].setdefault(uid_str, {"favorite_emojis": [], "favorite_packs": []})
            if pack_name not in u_pref["favorite_packs"]:
                u_pref["favorite_packs"].append(pack_name)

        self.save()

    def get_user_custom_emojis(self, user_id: int, limit: int = 15) -> List[Dict[str, Any]]:
        """Returns the most active custom emojis for the user (or global ones)."""
        uid_str = str(user_id)
        user_emojis = []
        for item in self.data["custom_emojis"].values():
            if uid_str in item.get("users", []):
                user_emojis.append(item)

        # Sort by usage count descending
        user_emojis.sort(key=lambda x: (x.get("count", 0), x.get("last_used", 0)), reverse=True)
        return user_emojis[:limit]

    def get_user_stickers(self, user_id: int) -> List[Dict[str, Any]]:
        """Returns all collected stickers sent by the user, sorted by recency and count."""
        uid_str = str(user_id)
        u_stickers = []
        for item in self.data["stickers"].values():
            if uid_str in item.get("users", []):
                u_stickers.append(item)

        u_stickers.sort(key=lambda x: (x.get("count", 0), x.get("last_used", 0)), reverse=True)
        return u_stickers

    def format_emojis_prompt_context(self, user_id: int) -> str:
        """Formats the learned user custom emojis and sticker packs into prompt markup instructions."""
        emojis = self.get_user_custom_emojis(user_id, limit=12)
        stickers = self.get_user_stickers(user_id)

        blocks = []
        if emojis:
            lines = ["[Кастомные Telegram Premium эмодзи собеседника (используй их в ответах для отзеркаливания)]:"]
            for item in emojis:
                cid = item["custom_emoji_id"]
                char = item.get("emoji", "✨")
                pack = item.get("set_name", "")
                pack_hint = f" (пак: {pack})" if pack else ""
                lines.append(f'• `<tg-emoji emoji-id="{cid}">{char}</tg-emoji>`{pack_hint}')
            lines.append("• ПРАВИЛО: отдавай приоритет именно этим кастомным эмодзи собеседника!")
            blocks.append("\n".join(lines))

        if stickers:
            pack_summary: Dict[str, List[str]] = {}
            for s in stickers:
                p = s.get("set_name", "general")
                em = s.get("emoji", "🌸")
                if p not in pack_summary:
                    pack_summary[p] = []
                if em not in pack_summary[p]:
                    pack_summary[p].append(em)

            st_lines = ["[Твоя коллекция стикеров из паков собеседника (подбирай подходящий по смыслу и контексту)]:"]
            for p, em_list in list(pack_summary.items())[:6]:
                em_str = " ".join(em_list[:12])
                st_lines.append(f'• Пак `{p}`: эмодзи [{em_str}] -> тег `<tg-sticker pack="{p}" emoji="..."/>`')
            st_lines.append("• ПРАВИЛО ПОДБОРА СТИКЕРОВ: не присылай вслепую в точности тот же стикер, что прислал пользователь! Подбирай подходящий, дополняющий по смыслу и остроумный стикер из этой коллекции.")
            blocks.append("\n".join(st_lines))

        return "\n\n".join(blocks)

    def find_best_matching_sticker(
        self,
        user_id: int,
        tag: Optional[str] = None,
        emoji: Optional[str] = None,
        pack: Optional[str] = None,
    ) -> Optional[str]:
        """Finds the best matching sticker file_id from the user's collected stickers."""
        stickers = self.get_user_stickers(user_id)
        if not stickers:
            return None

        # 1. Match by emoji + pack
        if emoji and pack:
            for s in stickers:
                if s.get("emoji") == emoji and pack.lower() in s.get("set_name", "").lower():
                    return s["file_id"]

        # 2. Match by emoji
        if emoji:
            for s in stickers:
                if s.get("emoji") == emoji:
                    return s["file_id"]

        # 3. Match by pack
        if pack:
            for s in stickers:
                if pack.lower() in s.get("set_name", "").lower():
                    return s["file_id"]

        # 4. Match by tag in tags/set_name
        if tag:
            t_low = tag.lower().strip()
            for s in stickers:
                if any(t_low in str(x).lower() for x in s.get("tags", [])):
                    return s["file_id"]
                if t_low in s.get("set_name", "").lower():
                    return s["file_id"]

        # 5. Return the user's most frequently used sticker
        return stickers[0]["file_id"]


asset_harvester = AssetHarvester()

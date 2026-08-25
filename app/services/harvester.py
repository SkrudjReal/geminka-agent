"""User Asset Harvester & Dynamic Emoji/Sticker Memory for Geminka.

Collects, catalogs, and stores:
1. Telegram Premium Custom Emojis sent by the user (IDs, characters, set_names).
2. Sticker packs and individual stickers sent by the user.

Allows Columbina to dynamically mirror and use the user's own custom emojis and sticker packs!
"""

import logging
import random
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
            "recent_sent_stickers": {},  # user_id -> [file_ids of last 20 sent stickers]
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

    def record_sent_sticker(self, user_id: int, file_id: str) -> None:
        """Records a sticker sent by the bot for recency and frequency penalty tracking."""
        uid_str = str(user_id)
        recent = self.data.setdefault("recent_sent_stickers", {}).setdefault(uid_str, [])
        recent.append(file_id)
        if len(recent) > 20:
            self.data["recent_sent_stickers"][uid_str] = recent[-20:]
        self.save()

    def get_recent_sent_stickers(self, user_id: int, limit: int = 20) -> List[str]:
        """Returns the list of recently sent sticker file_ids for the user."""
        uid_str = str(user_id)
        recent = self.data.get("recent_sent_stickers", {}).get(uid_str, [])
        return recent[-limit:]

    async def ingest_full_sticker_pack(
        self,
        bot: Any,
        user_id: int,
        set_name: str,
    ) -> None:
        """Fetches all stickers in the pack via Telegram Bot API get_sticker_set and saves them to JSON."""
        if not set_name or set_name == "unknown":
            return

        p_info = self.data.get("sticker_packs", {}).get(set_name, {})
        if p_info.get("fully_synced") and (time.time() - p_info.get("last_sync", 0) < 86400):
            return

        try:
            sticker_set = await bot.get_sticker_set(set_name)
            uid_str = str(user_id)
            now = time.time()
            for s in sticker_set.stickers:
                fid = s.file_id
                emoji_val = s.emoji or "✨"
                if fid not in self.data["stickers"]:
                    self.data["stickers"][fid] = {
                        "file_id": fid,
                        "emoji": emoji_val,
                        "set_name": set_name,
                        "is_animated": s.is_animated,
                        "is_video": s.is_video,
                        "count": 0,
                        "first_seen": now,
                        "last_used": now,
                        "users": [uid_str],
                        "tags": [emoji_val] if emoji_val else [],
                    }
                else:
                    item = self.data["stickers"][fid]
                    if uid_str not in item.get("users", []):
                        item.setdefault("users", []).append(uid_str)
                    if emoji_val and emoji_val not in item.get("tags", []):
                        item.setdefault("tags", []).append(emoji_val)

            self.data["sticker_packs"][set_name] = {
                "set_name": set_name,
                "title": getattr(sticker_set, "title", set_name),
                "sticker_count": len(sticker_set.stickers),
                "fully_synced": True,
                "last_sync": now,
                "last_used": now,
            }
            u_pref = self.data["user_preferences"].setdefault(uid_str, {"favorite_emojis": [], "favorite_packs": []})
            if set_name not in u_pref["favorite_packs"]:
                u_pref["favorite_packs"].append(set_name)

            self.save()
            logger.info(f"Ingested full sticker pack '{set_name}' ({len(sticker_set.stickers)} stickers) for user {user_id}.")
        except Exception as e:
            logger.warning(f"Failed to ingest full sticker pack '{set_name}': {e}")

    def find_best_matching_sticker(
        self,
        user_id: int,
        tag: Optional[str] = None,
        emoji: Optional[str] = None,
        pack: Optional[str] = None,
    ) -> Optional[str]:
        """Finds the best matching sticker file_id from the user's collected stickers with a 50% penalty per recent usage."""
        stickers = self.get_user_stickers(user_id)
        if not stickers:
            return None

        candidates = []
        # Priority 1: Match by emoji + pack
        if emoji and pack:
            candidates = [
                s for s in stickers
                if s.get("emoji") == emoji and pack.lower() in s.get("set_name", "").lower()
            ]

        # Priority 2: Match by emoji only
        if not candidates and emoji:
            candidates = [s for s in stickers if s.get("emoji") == emoji]

        # Priority 3: Match by pack only
        if not candidates and pack:
            candidates = [s for s in stickers if pack.lower() in s.get("set_name", "").lower()]

        # Priority 4: Match by tag in tags / set_name
        if not candidates and tag:
            t_low = tag.lower().strip()
            candidates = [
                s for s in stickers
                if any(t_low in str(x).lower() for x in s.get("tags", [])) or t_low in s.get("set_name", "").lower()
            ]

        # Priority 5: Fallback to all user stickers
        if not candidates:
            candidates = stickers

        recent_history = self.get_recent_sent_stickers(user_id, limit=20)

        # Calculate weights for each candidate: 50% penalty per usage in last 20 messages (w = 1.0 * (0.5 ** count))
        weights = []
        for s in candidates:
            fid = s["file_id"]
            recent_count = recent_history.count(fid)
            w = 1.0 * (0.5 ** recent_count)
            weights.append(w)

        chosen = random.choices(candidates, weights=weights, k=1)[0]
        return chosen["file_id"]


asset_harvester = AssetHarvester()

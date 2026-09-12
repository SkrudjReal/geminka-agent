"""User Asset Harvester & Dynamic Emoji/Sticker Memory for Geminka.

Collects, catalogs, and stores:
1. Telegram Premium Custom Emojis sent by the user (IDs, characters, set_names).
2. Sticker packs and individual stickers sent by the user.

Allows Columbina to dynamically mirror and use the user's own custom emojis and sticker packs!
"""

import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core import config
from app.core.files import atomic_write_json, load_json
from app.core.state import state_store

logger = logging.getLogger("geminka-assets")

USER_ASSETS_FILE = config.USER_ASSETS_FILE

SYNONYMS_DICT: Dict[str, List[str]] = {
    "обнимашки": ["обним", "объят", "ласк", "hug", "прижимает"],
    "обнять": ["обним", "объят", "ласк", "hug", "прижимает"],
    "объятия": ["обним", "объят", "ласк", "hug", "прижимает"],
    "поцелуй": ["целу", "поцелу", "чмок", "kiss", "губ"],
    "поцеловать": ["целу", "поцелу", "чмок", "kiss", "губ"],
    "чмок": ["целу", "поцелу", "чмок", "kiss"],
    "сон": ["сон", "спат", "подушк", "зева", "sleep", "посапывает", "устал"],
    "спать": ["сон", "спат", "подушк", "зева", "sleep", "посапывает"],
    "любовь": ["люб", "сердц", "нежност", "романтик", "love"],
    "смех": ["смех", "рж", "лол", "кек", "улыбк", "хаха", "haha"],
    "слезы": ["слез", "плач", "рыда", "груст", "тоск", "печал", "cry"],
    "плачет": ["слез", "плач", "рыда", "груст", "тоск", "печал", "cry"],
    "смущение": ["смущ", "красне", "румян", "blush", "неловк"],
    "привет": ["привет", "здравствуй", "машет", "хай", "hello", "hi"],
    "пока": ["пока", "проща", "bye"],
    "злость": ["зл", "ярост", "гнев", "бесит", "angry"],
    "шок": ["удивл", "шок", "глаза", "ого", "wow"],
}


def extract_stems(text: str) -> List[str]:
    """Extracts search tokens, stems, and synonyms from a search query or tag."""
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", text.lower())
    stems: List[str] = []
    for w in words:
        if len(w) >= 3:
            if w in SYNONYMS_DICT:
                stems.extend(SYNONYMS_DICT[w])
            else:
                stems.append(w[:5] if len(w) >= 5 else w)
    return list(dict.fromkeys(stems))


class AssetHarvester:
    def __init__(self, storage_file: Path = USER_ASSETS_FILE):
        self.storage_file = storage_file
        self._storage_mtime_ns: int | None = None
        self.data: Dict[str, Any] = {
            "custom_emojis": {},  # custom_emoji_id -> {id, emoji, set_name, count, last_used, users: []}
            "stickers": {},        # file_id -> {file_id, emoji, set_name, count, last_used, tags: [], users: []}
            "sticker_packs": {},  # set_name -> {set_name, sample_file_id, sticker_count, last_used}
            "user_preferences": {},  # user_id -> {favorite_emojis: [], favorite_packs: []}
            "recent_sent_stickers": {},  # user_id -> [file_ids of last 20 sent stickers]
        }
        self.load()

    @staticmethod
    def _debug_mode(user_id: int) -> bool:
        return state_store.is_debug_mode(user_id)

    def load(self) -> None:
        if self.storage_file.exists():
            try:
                saved = load_json(self.storage_file, {})
                for k in ["custom_emojis", "stickers", "sticker_packs", "user_preferences", "recent_sent_stickers"]:
                    if k in saved:
                        self.data[k] = saved[k]
                self._storage_mtime_ns = self.storage_file.stat().st_mtime_ns
                logger.info(
                    f"AssetHarvester loaded {len(self.data['custom_emojis'])} custom emojis, "
                    f"{len(self.data['stickers'])} stickers, {len(self.data['sticker_packs'])} packs."
                )
            except Exception as e:
                logger.warning(f"Failed to load user assets: {e}")

    def save(self) -> None:
        try:
            atomic_write_json(self.storage_file, self.data)
            self._storage_mtime_ns = self.storage_file.stat().st_mtime_ns
        except Exception as e:
            logger.warning(f"Failed to save user assets: {e}")

    def _refresh_if_changed(self) -> None:
        """Reload JSON written by the background sticker scanner before reads."""
        try:
            mtime_ns = self.storage_file.stat().st_mtime_ns
        except OSError:
            return
        if mtime_ns != self._storage_mtime_ns:
            self.load()

    def register_custom_emoji(
        self,
        user_id: int,
        custom_emoji_id: str,
        emoji_char: str = "✨",
        set_name: Optional[str] = None,
    ) -> None:
        """Records a custom emoji sent by user."""
        self._refresh_if_changed()
        if self._debug_mode(user_id):
            return
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
        file_unique_id: Optional[str] = None,
        emoji: str = "🌸",
        emoji_char: Optional[str] = None,
        set_name: Optional[str] = None,
        is_animated: bool = False,
        is_video: bool = False,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Records a sticker sent by user, preserving unique IDs and rich descriptions."""
        self._refresh_if_changed()
        if self._debug_mode(user_id):
            return
        emoji_val = emoji_char or emoji or "🌸"
        fid = str(file_id).strip()
        if not fid:
            return

        now = time.time()
        uid_str = str(user_id)
        pack_name = set_name or "unknown_pack"

        # Check if already in stickers by fid or file_unique_id
        target_item = None
        if fid in self.data["stickers"]:
            target_item = self.data["stickers"][fid]
        elif file_unique_id:
            for item in self.data["stickers"].values():
                if item.get("file_unique_id") == file_unique_id:
                    target_item = item
                    break

        if target_item is None:
            new_item = {
                "file_id": fid,
                "file_unique_id": file_unique_id,
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
            if meta:
                if meta.get("description"):
                    new_item["description"] = meta["description"]
                if meta.get("tags"):
                    for t in meta["tags"]:
                        if t not in new_item["tags"]:
                            new_item["tags"].append(t)
            self.data["stickers"][fid] = new_item
        else:
            target_item["count"] = target_item.get("count", 0) + 1
            target_item["last_used"] = now
            if file_unique_id and not target_item.get("file_unique_id"):
                target_item["file_unique_id"] = file_unique_id
            if meta and meta.get("description") and not target_item.get("description"):
                target_item["description"] = meta["description"]
            if meta and meta.get("tags"):
                for t in meta["tags"]:
                    if t not in target_item.setdefault("tags", []):
                        target_item["tags"].append(t)
            if emoji_val and emoji_val not in target_item.get("tags", []):
                target_item.setdefault("tags", []).append(emoji_val)
            if uid_str not in target_item.get("users", []):
                target_item.setdefault("users", []).append(uid_str)
            if fid not in self.data["stickers"]:
                self.data["stickers"][fid] = target_item

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
        self._refresh_if_changed()
        uid_str = str(user_id)
        user_emojis = []
        for item in self.data["custom_emojis"].values():
            if uid_str in item.get("users", []):
                user_emojis.append(item)

        # Sort by usage count descending
        user_emojis.sort(key=lambda x: (x.get("count", 0), x.get("last_used", 0)), reverse=True)
        return user_emojis[:limit]

    def get_user_stickers(self, user_id: int) -> List[Dict[str, Any]]:
        """Returns the user's stickers plus the built-in Columbina catalog."""
        self._refresh_if_changed()
        uid_str = str(user_id)
        u_stickers = []
        for item in self.data["stickers"].values():
            if uid_str in item.get("users", []):
                u_stickers.append(item)

        default_stickers = load_json(config.DEFAULT_STICKERS_FILE, [])
        if not isinstance(default_stickers, list):
            default_stickers = []

        merged: Dict[str, Dict[str, Any]] = {}
        for item in [*default_stickers, *u_stickers]:
            if not isinstance(item, dict):
                continue
            key = str(item.get("file_unique_id") or item.get("file_id") or "")
            if not key:
                continue
            if key not in merged:
                merged[key] = dict(item)
                continue
            existing = merged[key]
            for field, value in item.items():
                if field == "tags" and value:
                    existing["tags"] = sorted(set(existing.get("tags", [])) | set(value))
                elif value not in (None, "", []):
                    existing[field] = value

        u_stickers = list(merged.values())
        u_stickers.sort(key=lambda x: (x.get("count", 0), x.get("last_used", 0)), reverse=True)
        return u_stickers

    def get_sticker_metadata(
        self,
        file_id: str,
        file_unique_id: Optional[str] = None,
        set_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieves stored metadata (description, tags, etc.) for a sticker by file_id, file_unique_id, and/or set_name."""
        self._refresh_if_changed()
        # 1. First priority: search by permanent file_unique_id with description in user_assets
        if file_unique_id:
            for item in self.data.get("stickers", {}).values():
                if item.get("file_unique_id") == file_unique_id and item.get("description"):
                    return item

        # 2. Search by file_id with description in user_assets
        if file_id in self.data.get("stickers", {}):
            item = self.data["stickers"][file_id]
            if item.get("description"):
                return item

        # 3. Search the tracked built-in Columbina catalog.
        default_stickers = load_json(config.DEFAULT_STICKERS_FILE, [])
        if isinstance(default_stickers, list):
            for default in default_stickers:
                if (file_unique_id and default.get("file_unique_id") == file_unique_id) or default.get("file_id") == file_id:
                    if default.get("description"):
                        return default

        # 4. Fallback to bot_stickers.json catalog (curated stickers)
        bot_stickers_file = config.DATA_DIR / "bot_stickers.json"
        if bot_stickers_file.exists():
            try:
                bot_stickers = load_json(bot_stickers_file, [])
                if isinstance(bot_stickers, list):
                    for bs in bot_stickers:
                        if (file_unique_id and bs.get("file_unique_id") == file_unique_id) or bs.get("file_id") == file_id:
                            if bs.get("description"):
                                return bs
            except Exception:
                pass

        # 5. Fallback to any item by file_unique_id even without description
        if file_unique_id:
            for item in self.data.get("stickers", {}).values():
                if item.get("file_unique_id") == file_unique_id:
                    return item

        # 6. Direct file_id lookup
        if file_id in self.data.get("stickers", {}):
            return self.data["stickers"][file_id]

        return None

    def is_pack_in_db(self, set_name: str) -> bool:
        """Checks if a sticker pack is already registered in database."""
        if not set_name or set_name == "unknown":
            return False
        return set_name in self.data.get("sticker_packs", {})

    def is_pack_fully_scanned(self, set_name: str) -> bool:
        """Checks if a sticker pack is already ingested and has vision scan descriptions."""
        self._refresh_if_changed()
        if not set_name or set_name == "unknown":
            return False
        p_info = self.data.get("sticker_packs", {}).get(set_name, {})
        return bool(p_info.get("fully_synced") and (p_info.get("scanned") or p_info.get("summary")))

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
            by_pack: Dict[str, List[Dict[str, Any]]] = {}
            for s in stickers:
                p = s.get("set_name", "general")
                by_pack.setdefault(p, []).append(s)

            st_lines = [
                "[База знаний стикеров из JSON-каталога (анализируй описания каждого стикера и выбирай наиболее подходящий по смыслу и контексту)]:",
                "• ПРАВИЛО ВЫБОРА: при каждой отправке стикера сопоставляй контекст диалога с описанием каждого стикера (description) и тегами (tags), выбирая самый остроумный и точный стикер из всех доступных паков, указывая тег `<tg-sticker pack=\"...\" tag=\"...\"/>` или `<tg-sticker tag=\"...\"/>`!",
                "• СТРОГО НЕ отправляй в точности тот же стикер, что прислал пользователь.",
            ]

            # Sort packs: packs with descriptions first, then by count
            sorted_packs = sorted(
                by_pack.items(),
                key=lambda item: (sum(1 for s in item[1] if s.get("description")), len(item[1])),
                reverse=True,
            )

            for p_name, s_list in sorted_packs:
                p_info = self.data.get("sticker_packs", {}).get(p_name, {})
                p_title = p_info.get("title", p_name)
                p_summary = p_info.get("summary") or p_info.get("description", "")
                summary_short = f" — {p_summary[:85]}..." if p_summary else ""

                with_desc = [s for s in s_list if s.get("description")]
                if with_desc:
                    st_lines.append(f"\n📦 **Пак «{p_title}» (`{p_name}`)** ({len(with_desc)} описанных стикеров){summary_short}:")
                    for s in with_desc:
                        em = s.get("emoji", "✨")
                        desc = s.get("description", "")
                        tags = s.get("tags") or [em]
                        first_tag = tags[0]
                        st_lines.append(f"  • [{em}] {desc} -> `<tg-sticker pack=\"{p_name}\" tag=\"{first_tag}\"/>`")
                else:
                    em_list = list(dict.fromkeys([s.get("emoji", "✨") for s in s_list]))[:15]
                    em_str = " ".join(em_list)
                    st_lines.append(f"\n📦 **Пак «{p_title}» (`{p_name}`)** ({len(s_list)} стикеров): эмодзи [{em_str}] -> `<tg-sticker pack=\"{p_name}\" emoji=\"...\"/>`")

            blocks.append("\n".join(st_lines))

        return "\n\n".join(blocks)

    def record_sent_sticker(self, user_id: int, file_id: str) -> None:
        """Records a sticker sent by the bot for recency and frequency penalty tracking."""
        self._refresh_if_changed()
        if self._debug_mode(user_id):
            return
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
        if self._debug_mode(user_id):
            return
        if not set_name or set_name == "unknown":
            return

        p_info = self.data.get("sticker_packs", {}).get(set_name, {})
        if p_info.get("fully_synced") and (time.time() - p_info.get("last_sync", 0) < 86400):
            return

        try:
            sticker_set = await bot.get_sticker_set(set_name)
            if self._debug_mode(user_id):
                return
            uid_str = str(user_id)
            now = time.time()
            for s in sticker_set.stickers:
                fid = s.file_id
                s_uid = getattr(s, "file_unique_id", None)
                emoji_val = s.emoji or "✨"
                if fid not in self.data["stickers"]:
                    self.data["stickers"][fid] = {
                        "file_id": fid,
                        "file_unique_id": s_uid,
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
                    if s_uid and not item.get("file_unique_id"):
                        item["file_unique_id"] = s_uid
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

    def calculate_sticker_score(
        self,
        s: Dict[str, Any],
        tag: Optional[str],
        emoji: Optional[str],
        pack: Optional[str],
    ) -> float:
        """Calculates semantic match score between search criteria and a sticker in JSON."""
        score = 0.0
        s_pack = s.get("set_name", "").lower()
        s_emoji = s.get("emoji", "")
        desc = s.get("description", "").lower()
        tags = [str(t).lower() for t in s.get("tags", [])]

        pack_match = bool(pack and pack.lower() in s_pack)

        # 1. Emoji match
        if emoji and s_emoji == emoji:
            score += 80.0

        # 2. Tag / Description / Semantic match
        if tag:
            t_low = tag.lower().strip()
            # Exact tag match
            if t_low in tags:
                score += 120.0
            # Substring in any tag
            elif any(t_low in t for t in tags):
                score += 80.0

            # Substring in description
            if t_low in desc:
                score += 70.0

            # Stems and synonyms match
            stems = extract_stems(t_low)
            for st in stems:
                if any(st in t for t in tags):
                    score += 50.0
                if st in desc:
                    score += 45.0

        # Pack bonus: boosts score if sticker is in the requested pack
        if pack_match:
            if score > 0:
                score += 40.0
            elif not tag and not emoji:
                score += 20.0

        return score

    def find_best_matching_sticker(
        self,
        user_id: int,
        tag: Optional[str] = None,
        emoji: Optional[str] = None,
        pack: Optional[str] = None,
    ) -> Optional[str]:
        """Finds the best matching sticker file_id from user's stickers or global database using semantic scoring and recency penalty."""
        stickers = self.get_user_stickers(user_id)
        if not stickers:
            stickers = list(self.data.get("stickers", {}).values())
        if not stickers:
            return None

        # 1. If pack specified, score within requested pack first
        scored_stickers: List[Tuple[float, Dict[str, Any]]] = []
        if pack:
            pack_stickers = [s for s in stickers if pack.lower() in s.get("set_name", "").lower()]
            for s in pack_stickers:
                sc = self.calculate_sticker_score(s, tag=tag, emoji=emoji, pack=pack)
                if sc > 0:
                    scored_stickers.append((sc, s))

        # 2. If no pack specified or no match in requested pack, search across all user stickers
        if not scored_stickers:
            for s in stickers:
                sc = self.calculate_sticker_score(s, tag=tag, emoji=emoji, pack=pack)
                if sc > 0:
                    scored_stickers.append((sc, s))

        # 3. Fallback to all database stickers if criteria requested but not found in user stickers
        if not scored_stickers and (tag or emoji or pack):
            all_stickers = list(self.data.get("stickers", {}).values())
            for s in all_stickers:
                sc = self.calculate_sticker_score(s, tag=tag, emoji=emoji, pack=pack)
                if sc > 0:
                    scored_stickers.append((sc, s))

        if scored_stickers:
            scored_stickers.sort(key=lambda x: x[0], reverse=True)
            top_score = scored_stickers[0][0]
            # Take top candidates (score >= 60% of top score)
            threshold = max(30.0, top_score * 0.6)
            candidates = [s for sc, s in scored_stickers if sc >= threshold]
        else:
            if tag or emoji:
                return None
            if pack:
                candidates = [s for s in stickers if pack.lower() in s.get("set_name", "").lower()]
            else:
                candidates = stickers

        if not candidates:
            return None

        recent_history = self.get_recent_sent_stickers(user_id, limit=20)

        # Calculate weights: candidate match score combined with 50% penalty per usage in last 20 messages
        weights = []
        for s in candidates:
            fid = s["file_id"]
            recent_count = recent_history.count(fid)
            sc = self.calculate_sticker_score(s, tag=tag, emoji=emoji, pack=pack)
            base_w = max(1.0, sc)
            w = base_w * (0.5 ** recent_count)
            weights.append(w)

        chosen = random.choices(candidates, weights=weights, k=1)[0]
        return chosen["file_id"]


asset_harvester = AssetHarvester()

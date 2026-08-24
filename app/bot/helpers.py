"""Telegram context extraction and file inspection helpers for Geminka."""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiogram import Bot, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from app.core import config
from app.services.harvester import asset_harvester
from app.services.streamer import md_to_telegram_html

logger = logging.getLogger("geminka-helpers")

CUSTOM_EMOJIS_FILE = config.CUSTOM_EMOJIS_FILE

TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss", ".json",
    ".md", ".txt", ".yaml", ".yml", ".sh", ".bash", ".zsh", ".sql", ".env",
    ".csv", ".xml", ".rs", ".go", ".c", ".cpp", ".h", ".hpp", ".java", ".kt",
    ".php", ".rb", ".lua", ".ini", ".conf", ".toml", ".dockerfile", ".log",
}

# Global in-memory sessions dictionary
sessions: Dict[str, str] = {}


def load_sessions() -> None:
    global sessions
    if config.SESSIONS_FILE.exists():
        try:
            with open(config.SESSIONS_FILE, "r", encoding="utf-8") as f:
                sessions.update(json.load(f))
                logger.info(f"Loaded {len(sessions)} active sessions.")
        except Exception as e:
            logger.warning(f"Failed to load sessions: {e}")


def save_sessions() -> None:
    try:
        with open(config.SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Failed to save sessions: {e}")


async def send_response(message: types.Message, text: str) -> None:
    """Sends text in chunks <= 4000 characters, rendered as clean Telegram HTML."""
    max_len = 4000
    chunks = [text[i : i + max_len] for i in range(0, len(text), max_len)] or [""]
    for chunk in chunks:
        html_chunk = md_to_telegram_html(chunk)
        try:
            await message.answer(html_chunk, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            await message.answer(chunk, parse_mode=None)


async def download_telegram_file(bot: Bot, file_id: str, filename: str) -> Path:
    """Downloads a file from Telegram into the downloads directory."""
    file_obj = await bot.get_file(file_id)
    safe_name = filename.replace("/", "_").replace("\\", "_")
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest_path = config.DOWNLOADS_DIR / f"{ts}_{safe_name}"
    await bot.download_file(file_obj.file_path, dest_path)
    return dest_path


def read_text_file_preview(path: Path, max_bytes: int = 65536) -> Optional[str]:
    """Reads a text/code file for prompt embedding."""
    try:
        if path.stat().st_size > max_bytes:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read(max_bytes) + "\n... [файл обрезан из-за размера]"
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None


async def inspect_custom_emojis(message: types.Message, bot: Bot) -> List[Dict]:
    """Inspects message entities for custom_emoji, resolves stickers/packs, and stores them."""
    if not message.entities:
        return []

    discovered = []
    custom_ids = [
        ent.custom_emoji_id
        for ent in message.entities
        if ent.type == "custom_emoji" and ent.custom_emoji_id
    ]

    if not custom_ids:
        return []

    try:
        stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=custom_ids)
        for st in stickers:
            info = {
                "custom_emoji_id": st.custom_emoji_id,
                "emoji": st.emoji,
                "set_name": st.set_name,
                "file_id": st.file_id,
                "is_animated": st.is_animated,
                "is_video": st.is_video,
            }
            discovered.append(info)
            asset_harvester.register_custom_emoji(
                user_id=message.from_user.id,
                custom_emoji_id=st.custom_emoji_id,
                emoji_char=st.emoji or "✨",
                set_name=st.set_name,
            )
    except Exception as e:
        logger.debug(f"Could not fetch custom emoji stickers: {e}")

    if discovered and CUSTOM_EMOJIS_FILE.exists():
        try:
            with open(CUSTOM_EMOJIS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
        except Exception:
            saved = []

        existing_ids = {item["custom_emoji_id"] for item in saved if "custom_emoji_id" in item}
        new_items = [d for d in discovered if d["custom_emoji_id"] not in existing_ids]
        if new_items:
            saved.extend(new_items)
            with open(CUSTOM_EMOJIS_FILE, "w", encoding="utf-8") as f:
                json.dump(saved, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved {len(new_items)} new custom emojis.")

    return discovered


async def extract_message_context(message: types.Message, bot: Bot) -> str:
    """Deep inspection of message context: text, custom emojis, replies, documents, code, photos, stickers."""
    parts = []

    # 1. Custom Emoji inspection
    custom_emojis = await inspect_custom_emojis(message, bot)
    emoji_hint = ""
    if custom_emojis:
        em_list = [f"custom_emoji(id={ce['custom_emoji_id']}, emoji={ce.get('emoji')}, pack={ce.get('set_name')})" for ce in custom_emojis]
        emoji_hint = f"\n[Использованы кастомные эмодзи: {', '.join(em_list)}]"

    # 2. Reply Context Inspection
    reply_context = ""
    if message.reply_to_message:
        orig = message.reply_to_message
        sender_name = orig.from_user.full_name if orig.from_user else "Собеседник"
        orig_text = orig.text or orig.caption or ""

        orig_media_info = ""
        if orig.photo:
            try:
                dest = await download_telegram_file(bot, orig.photo[-1].file_id, "reply_photo.jpg")
                orig_media_info = f"\n[Прикрепленное фото: путь {dest}]"
            except Exception as e:
                orig_media_info = f"\n[Фото (ошибка скачивания: {e})]"
        elif orig.document:
            try:
                dest = await download_telegram_file(bot, orig.document.file_id, orig.document.file_name or "reply_doc")
                preview = ""
                if Path(orig.document.file_name or "").suffix.lower() in TEXT_EXTENSIONS:
                    content = read_text_file_preview(dest)
                    if content:
                        preview = f"\nСодержимое документа:\n```\n{content}\n```"
                orig_media_info = f"\n[Прикрепленный документ: {orig.document.file_name}, путь {dest}]{preview}"
            except Exception as e:
                orig_media_info = f"\n[Документ (ошибка скачивания: {e})]"
        elif orig.sticker:
            pack_name = orig.sticker.set_name or "unknown"
            st_type = "видео-стикер" if orig.sticker.is_video else ("анимированный стикер" if orig.sticker.is_animated else "статичный стикер")
            orig_media_info = f"\n[{st_type.capitalize()}: эмодзи '{orig.sticker.emoji}', стикерпак '{pack_name}']"

        reply_context = f"\n[В ответ на сообщение от {sender_name}]:\n\"\"\"\n{orig_text}{orig_media_info}\n\"\"\""

    # 3. Direct Message Content (Text / Photo / Document / Sticker)
    user_text = message.text or message.caption or ""

    if message.photo:
        try:
            dest = await download_telegram_file(bot, message.photo[-1].file_id, "incoming_photo.jpg")
            parts.append(f"[Пользователь прислал фото, сохранено локально: {dest}]")
        except Exception as e:
            parts.append(f"[Пользователь прислал фото (ошибка сохранения: {e})]")

    elif message.document:
        doc = message.document
        try:
            dest = await download_telegram_file(bot, doc.file_id, doc.file_name or "doc")
            preview = ""
            if Path(doc.file_name or "").suffix.lower() in TEXT_EXTENSIONS:
                content = read_text_file_preview(dest)
                if content:
                    preview = f"\nСодержимое файла {doc.file_name}:\n```\n{content}\n```"
            parts.append(f"[Пользователь прикрепил документ: {doc.file_name}, сохранено: {dest}]{preview}")
        except Exception as e:
            parts.append(f"[Пользователь прикрепил документ: {doc.file_name} (ошибка скачивания: {e})]")

    elif message.sticker:
        asset_harvester.register_sticker(
            user_id=message.from_user.id,
            file_id=message.sticker.file_id,
            emoji_char=message.sticker.emoji or "✨",
            set_name=message.sticker.set_name or "unknown",
            is_animated=message.sticker.is_animated,
            is_video=message.sticker.is_video,
        )
        st_type = "видео-стикер" if message.sticker.is_video else ("анимированный стикер" if message.sticker.is_animated else "стикер")
        parts.append(
            f"[Пользователь прислал {st_type}: эмодзи '{message.sticker.emoji}', "
            f"стикерпак '{message.sticker.set_name}']"
        )

    if user_text:
        parts.append(user_text)

    full_content = "\n".join(parts).strip()
    return f"{emoji_hint}{reply_context}\n{full_content}".strip()

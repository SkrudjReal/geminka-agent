"""Stream Consumer & Telegram HTML/Markdown Formatter.

Inspired by Hermes Agent formatting & streaming architecture:
- Converts Markdown to Telegram-safe HTML (supporting headers, code blocks, bold, italic, quotes, links, tables)
- Preserves native Telegram Premium Custom Emoji (<tg-emoji emoji-id="...">...</tg-emoji>)
- Native Sticker Selection by emotion tag, description, emoji or index (<tg-sticker tag="..." />)
- Native Message Reactions (<tg-react emoji="❤" /> or <tg-react custom-emoji-id="..." />)
- Conditional Context-Aware Quote Replies (<tg-reply />)
- Dual Photo delivery with spoiler reply (<tg-send-photos />)
- Progressive message edits with adaptive debounce (0.8s)
- Animated streaming cursor (▉) during generation это пиздёж
- Dynamic think-tag (<think>...</think>) suppression
- Flood control handling & 'message not modified' suppression
"""

import asyncio
import html
import json
import logging
import random
import re
import time
from typing import AsyncGenerator, Dict, List, Optional

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import (
    FSInputFile,
    ReactionTypeCustomEmoji,
    ReactionTypeEmoji,
    ReplyParameters,
)

from app.core import config
from app.services.harvester import asset_harvester

logger = logging.getLogger(__name__)

# Reasoning / think tags to suppress during live streaming
THINK_BLOCK_RE = re.compile(
    r"<(?:think|thought|reasoning|THINKING)>[\s\S]*?</(?:think|thought|reasoning|THINKING)>",
    re.IGNORECASE,
)
THINK_OPEN_TAGS = ("<think>", "<thought>", "<reasoning>", "<THINKING>")
STICKER_TAG_RE = re.compile(r"<tg-sticker\s+([^>]+)\s*/?>", re.IGNORECASE)
REACT_TAG_RE = re.compile(r"<tg-react\s+([^>]+)\s*/?>", re.IGNORECASE)
REPLY_TAG_RE = re.compile(r"<tg-reply(?:\s*/>|\s+[^>]*/>)", re.IGNORECASE)
RP_TAG_RE = re.compile(r"<tg-rp\s+([^>]+)\s*/?>", re.IGNORECASE)
PHOTO_PAIR_TAG_RE = re.compile(r"<tg-send-photos\s*/?>|<tg-photo-pair\s*/?>", re.IGNORECASE)

_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*){1,}\|?\s*$")

# Mapping of non-standard reaction emojis to Telegram-supported standard emojis
EMOJI_REACTION_MAP = {
    "💖": "❤",
    "💗": "❤",
    "💓": "❤",
    "💕": "❤",
    "❤️": "❤",
    "✨": "⚡",
    "🌟": "🔥",
    "🌸": "🥰",
}

SURPRISE_CAPTIONS = [
    'А вот маленький сюрприз от меня <tg-emoji emoji-id="6136716054971291812">💖</tg-emoji>',
    'Специально для тебя... только никому не показывай <tg-emoji emoji-id="5305423313764363203">🤫</tg-emoji> <tg-emoji emoji-id="6136716054971291812">💖</tg-emoji>',
    'Вот ещё одна... в полный рост <tg-emoji emoji-id="5305602448260345544">☺️</tg-emoji> <tg-emoji emoji-id="5456184310895748720">✨</tg-emoji>',
    'Маленький секретный кадр от Коломбины <tg-emoji emoji-id="5300994163100119559">🌸</tg-emoji> <tg-emoji emoji-id="6136716054971291812">💖</tg-emoji>',
]

# Dynamic bot-specific stickers catalog with emotion tags
_STICKERS_CACHE: List[Dict] = []
_STICKERS_MTIME: float = 0.0
_STICKERS_FILE = config.STICKERS_FILE


def get_bot_stickers() -> List[Dict]:
    global _STICKERS_CACHE, _STICKERS_MTIME
    if _STICKERS_FILE.exists():
        try:
            mtime = _STICKERS_FILE.stat().st_mtime
            if mtime != _STICKERS_MTIME or not _STICKERS_CACHE:
                with open(_STICKERS_FILE, "r", encoding="utf-8") as f:
                    _STICKERS_CACHE = json.load(f)
                _STICKERS_MTIME = mtime
        except Exception as e:
            logger.warning(f"Failed to reload bot stickers: {e}")
    return _STICKERS_CACHE


def resolve_sticker_file_id(attrs_str: str, user_id: Optional[int] = None) -> Optional[str]:
    """Resolves sticker file_id from user's collected packs, tag, description, emoji, index, or direct file-id."""
    # 1. Direct file-id
    m_file = re.search(r'file-id="([^"]+)"', attrs_str)
    if m_file:
        return m_file.group(1)

    m_tag = re.search(r'tag="([^"]+)"', attrs_str)
    tag_val = m_tag.group(1).lower().strip() if m_tag else None

    m_emoji = re.search(r'emoji="([^"]+)"', attrs_str)
    emoji_val = m_emoji.group(1).strip() if m_emoji else None

    m_pack = re.search(r'pack="([^"]+)"', attrs_str)
    pack_val = m_pack.group(1).strip() if m_pack else None

    # Priority 1: User's own collected stickers from their packs!
    if user_id or tag_val or emoji_val or pack_val:
        u_match = asset_harvester.find_best_matching_sticker(
            user_id=user_id or 0,
            tag=tag_val,
            emoji=emoji_val,
            pack=pack_val,
        )
        if u_match:
            return u_match

    # Priority 2: Fallback to bot's catalog
    stickers = get_bot_stickers()
    if tag_val:
        for item in stickers:
            tags = [t.lower() for t in item.get("tags", [])]
            if tag_val in tags or any(tag_val in t for t in tags):
                return item["file_id"]
        for item in stickers:
            desc = item.get("description", "").lower()
            if tag_val in desc:
                return item["file_id"]

    if emoji_val:
        for item in stickers:
            if item.get("emoji") == emoji_val:
                return item["file_id"]

    m_idx = re.search(r'index="?(\d+)"?', attrs_str)
    if m_idx and stickers:
        idx = int(m_idx.group(1)) % len(stickers)
        return stickers[idx]["file_id"]

    return None


def resolve_reaction(attrs_str: str):
    """Resolves Telegram reaction object from raw attributes."""
    m_custom = re.search(r'custom-emoji-id="([^"]+)"', attrs_str)
    if m_custom:
        return ReactionTypeCustomEmoji(custom_emoji_id=m_custom.group(1))

    m_emoji = re.search(r'emoji="([^"]+)"', attrs_str)
    if m_emoji:
        raw_emoji = m_emoji.group(1).strip()
        safe_emoji = EMOJI_REACTION_MAP.get(raw_emoji, raw_emoji)
        return ReactionTypeEmoji(emoji=safe_emoji)

    return ReactionTypeEmoji(emoji="❤")


def _split_table_row(row_str: str) -> List[str]:
    row = row_str.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return bool(s.startswith("|") or s.endswith("|") or ("|" in s and not s.startswith("```")))


def wrap_markdown_tables(text: str) -> str:
    """Converts GFM tables into Telegram-friendly mobile formatted card lists."""
    if "|" not in text:
        return text

    lines = text.split("\n")
    out = []
    i = 0
    in_fence = False
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue

        if "|" in line and i + 1 < len(lines) and _TABLE_SEPARATOR_RE.match(lines[i + 1]):
            headers = _split_table_row(line)
            table_rows = []
            j = i + 2
            while j < len(lines) and _is_table_row(lines[j]):
                table_rows.append(_split_table_row(lines[j]))
                j += 1

            table_text = []
            for row_idx, row in enumerate(table_rows, start=1):
                heading = row[0] if row else f"Элемент {row_idx}"
                bullets = []
                for h, val in zip(headers, row, strict=False):
                    if val:
                        bullets.append(f"• <b>{h}</b>: {val}")
                table_text.append(f"<b>{heading}</b>\n" + "\n".join(bullets))

            out.append("\n\n".join(table_text))
            i = j
            continue

        out.append(line)
        i += 1

    return "\n".join(out)


def md_to_telegram_html(md: str) -> str:
    """Converts GitHub Markdown into Telegram-compatible HTML while preserving custom emoji and tags."""
    if not md:
        return ""

    md = wrap_markdown_tables(md)

    # Protect fenced code before preserving HTML so tags inside code stay escaped.
    code_blocks = []

    def save_code_block(m):
        lang = m.group(1) or ""
        code_escaped = html.escape(m.group(2).rstrip())
        idx = len(code_blocks)
        if lang:
            tag = f'<pre><code class="language-{html.escape(lang)}">{code_escaped}</code></pre>'
        else:
            tag = f"<pre>{code_escaped}</pre>"
        code_blocks.append(tag)
        return f"%%CODEBLOCK_{idx}%%"

    md = re.sub(r"```([a-zA-Z0-9_\-\+]*)\n([\s\S]*?)```", save_code_block, md)

    # Protect existing valid Telegram HTML tags.
    saved_html_tags = []
    def save_valid_tag(m):
        idx = len(saved_html_tags)
        saved_html_tags.append(m.group(0))
        return f"%%TGHTMLTAG_{idx}%%"

    valid_tag_pattern = re.compile(
        r"</?(?:b|strong|i|em|u|ins|s|strike|del|tg-spoiler|blockquote|code|pre)\b[^>]*>|"
        r'<a\s+href="[^"]+"[^>]*>|</a>',
        re.IGNORECASE,
    )
    md = valid_tag_pattern.sub(save_valid_tag, md)

    # Protect inline code.
    inline_codes = []
    def save_inline_code(m):
        code_escaped = html.escape(m.group(1))
        idx = len(inline_codes)
        inline_codes.append(f"<code>{code_escaped}</code>")
        return f"%%INLINECODE_{idx}%%"

    md = re.sub(r"`([^`\n]+)`", save_inline_code, md)

    # 3. Protect Telegram Premium Custom Emoji tags (<tg-emoji emoji-id="...">...</tg-emoji>)
    tg_emojis = []
    def save_tg_emoji(m):
        idx = len(tg_emojis)
        tg_emojis.append(m.group(0))
        return f"%%TGEMOJI_{idx}%%"

    md = re.sub(r"<tg-emoji\s+emoji-id=[\"'](\d+)[\"']>([\s\S]*?)</tg-emoji>", save_tg_emoji, md, flags=re.IGNORECASE)

    # 4. Escape general HTML entities
    md = html.escape(md)

    # 5. Convert Blockquotes (> Quote)
    md_lines = md.split("\n")
    formatted_lines = []
    in_quote = False
    quote_acc = []

    for line in md_lines:
        stripped = line.strip()
        if stripped.startswith("&gt; ") or stripped == "&gt;":
            q_text = line.lstrip()[4:] if stripped.startswith("&gt; ") else ""
            quote_acc.append(q_text)
            in_quote = True
        else:
            if in_quote:
                formatted_lines.append(f"<blockquote>{chr(10).join(quote_acc)}</blockquote>")
                quote_acc = []
                in_quote = False
            formatted_lines.append(line)
    if in_quote:
        formatted_lines.append(f"<blockquote>{chr(10).join(quote_acc)}</blockquote>")

    md = "\n".join(formatted_lines)

    # 6. Convert Markdown Formatting
    # Bold: **text** or __text__
    md = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", md)
    md = re.sub(r"__(.+?)__", r"<b>\1</b>", md)

    # Italic: *text* or _text_
    md = re.sub(r"(?<!\w)\*([^\*\n]+?)\*(?!\w)", r"<i>\1</i>", md)
    md = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<i>\1</i>", md)

    # Strikethrough: ~~text~~
    md = re.sub(r"~~(.+?)~~", r"<s>\1</s>", md)

    # Markdown links: [text](url)
    md = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'<a href="\2">\1</a>', md)

    # Headers: ### Header -> <b>Header</b>
    md = re.sub(r"^(?:#{1,6})\s+(.+)$", r"<b>\1</b>", md, flags=re.MULTILINE)

    # 7. Restore protected tags
    for idx, em in enumerate(tg_emojis):
        md = md.replace(f"%%TGEMOJI_{idx}%%", em)

    for idx, code in enumerate(inline_codes):
        md = md.replace(f"%%INLINECODE_{idx}%%", code)

    for idx, block in enumerate(code_blocks):
        md = md.replace(f"%%CODEBLOCK_{idx}%%", block)

    for idx, tag in enumerate(saved_html_tags):
        md = md.replace(f"%%TGHTMLTAG_{idx}%%", tag)

    return md


def split_telegram_text(text: str, limit: int = 3_500) -> list[str]:
    """Split before HTML expansion, preferring paragraph and line boundaries."""
    if not text:
        return [""]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        boundary = remaining.rfind("\n\n", 0, limit + 1)
        if boundary < limit // 2:
            boundary = remaining.rfind("\n", 0, limit + 1)
        if boundary < limit // 2:
            boundary = remaining.rfind(" ", 0, limit + 1)
        if boundary <= 0:
            boundary = limit
        chunks.append(remaining[:boundary].rstrip())
        remaining = remaining[boundary:].lstrip()
    if remaining or not chunks:
        chunks.append(remaining)
    return chunks


def strip_think_tags(text: str) -> str:
    """Removes completed and incomplete thinking/reasoning tags."""
    if not text:
        return ""
    cleaned = THINK_BLOCK_RE.sub("", text)
    for tag in THINK_OPEN_TAGS:
        if tag in cleaned:
            idx = cleaned.find(tag)
            cleaned = cleaned[:idx]
    return cleaned.strip()


INCOMPLETE_CONTROL_TAG_RE = re.compile(
    r"<(?:tg-[a-z\-]+|think|thought|reasoning|THINKING)[^>]*$",
    re.IGNORECASE,
)


def strip_delivery_tags(text: str) -> str:
    """Remove model control tags and incomplete/half-typed control tags from Telegram-visible text."""
    for pattern in (STICKER_TAG_RE, REACT_TAG_RE, REPLY_TAG_RE, RP_TAG_RE, PHOTO_PAIR_TAG_RE):
        text = pattern.sub("", text)
    text = INCOMPLETE_CONTROL_TAG_RE.sub("", text)
    return text.strip()


class TelegramStreamConsumer:
    """Consumes an async stream of tokens and progressively edits a Telegram message with HTML formatting."""

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        user_id: Optional[int] = None,
        target_message_id: Optional[int] = None,
        target_user_mention: Optional[str] = None,
        edit_interval: float = 0.8,
        cursor: str = ' <tg-emoji emoji-id="5456184310895748720">✨</tg-emoji>',
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.user_id = user_id
        self.target_message_id = target_message_id
        self.target_user_mention = target_user_mention
        self.edit_interval = edit_interval
        self.cursor = cursor
        self.message_id: Optional[int] = None
        self.accumulated = ""
        self.last_sent_text = ""
        self.last_edit_time = 0.0
        self.should_reply = False

    async def _safe_send_initial(self, text: str) -> bool:
        """Sends the initial preview message."""
        preview = text if len(text) <= 3_500 else "…\n" + text[-3_498:]
        content = (preview + self.cursor).strip() or self.cursor
        html_content = md_to_telegram_html(content)
        reply_params = (
            ReplyParameters(message_id=self.target_message_id)
            if (self.should_reply and self.target_message_id)
            else None
        )
        try:
            msg = await self.bot.send_message(
                self.chat_id,
                html_content,
                parse_mode=ParseMode.HTML,
                reply_parameters=reply_params,
            )
            self.message_id = msg.message_id
            self.last_sent_text = content
            self.last_edit_time = time.monotonic()
            return True
        except Exception:
            try:
                msg = await self.bot.send_message(
                    self.chat_id,
                    content,
                    parse_mode=None,
                    reply_parameters=reply_params,
                )
                self.message_id = msg.message_id
                self.last_sent_text = content
                self.last_edit_time = time.monotonic()
                return True
            except Exception as e:
                logger.warning(f"Failed to send initial stream message: {e}")
                return False

    async def _safe_edit(self, text: str, finalize: bool = False) -> None:
        """Progressively edits the message with full HTML formatting."""
        if not self.message_id:
            return

        clean_text = strip_think_tags(text)
        if not clean_text and not finalize:
            return

        preview = clean_text if len(clean_text) <= 3_500 else "…\n" + clean_text[-3_498:]
        content = preview if finalize else (preview + self.cursor)
        if content == self.last_sent_text and not finalize:
            return

        html_content = md_to_telegram_html(content)

        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=html_content,
                parse_mode=ParseMode.HTML,
            )
            self.last_sent_text = content
            self.last_edit_time = time.monotonic()
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramBadRequest as e:
            err_msg = str(e).lower()
            if "message is not modified" in err_msg:
                pass
            elif "can't parse entities" in err_msg or "tag" in err_msg:
                try:
                    await self.bot.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.message_id,
                        text=content,
                        parse_mode=None,
                    )
                except Exception:
                    pass
            else:
                logger.debug(f"Stream edit exception: {e}")
        except Exception as e:
            logger.debug(f"Unhandled edit error: {e}")

    async def _consume_live_tokens(self, token_generator: AsyncGenerator[str, None]) -> None:
        try:
            async for chunk in token_generator:
                if not chunk:
                    continue
                self.accumulated += chunk
                if REPLY_TAG_RE.search(self.accumulated):
                    self.should_reply = True
                clean_display = strip_delivery_tags(strip_think_tags(self.accumulated))
                if not self.message_id:
                    if len(clean_display) >= 8 or "\n" in clean_display:
                        await self._safe_send_initial(clean_display)
                    continue
                if time.monotonic() - self.last_edit_time >= self.edit_interval:
                    await self._safe_edit(clean_display, finalize=False)
        except Exception:
            partial = strip_delivery_tags(strip_think_tags(self.accumulated))
            if self.message_id and partial:
                await self._safe_edit(partial, finalize=True)
            raise

    async def stream_from_generator(self, token_generator: AsyncGenerator[str, None]) -> str:
        """Iterates through token_generator, emitting live HTML-rendered edits, stickers, reactions, and RP actions."""
        await self._consume_live_tokens(token_generator)

        final_raw = strip_think_tags(self.accumulated)

        # Check if reply tag is present
        if REPLY_TAG_RE.search(final_raw):
            self.should_reply = True

        # Check if photo pair tag is present
        send_photos = bool(PHOTO_PAIR_TAG_RE.search(final_raw))

        # 1. Resolve and format RP banner if present
        rp_banner = ""
        rp_is_inline = False
        rp_match = RP_TAG_RE.search(final_raw)
        if rp_match:
            rp_attrs = rp_match.group(1)
            rp_is_inline = 'mode="inline"' in rp_attrs.lower()
            m_act = re.search(r'action="([^"]+)"', rp_attrs)
            if m_act:
                from app.engines.rp import get_random_rp_phrase
                target_mention = self.target_user_mention or "<b>ты</b>"
                sender_mention = "<b>Коломбина</b>"
                phrase = get_random_rp_phrase(m_act.group(1), sender_mention, target_mention)
                if phrase:
                    rp_banner = phrase

        # 2. Resolve and send sticker if present
        sticker_match = STICKER_TAG_RE.search(final_raw)
        sticker_file_id = None
        if sticker_match:
            attrs = sticker_match.group(1)
            sticker_file_id = resolve_sticker_file_id(attrs, user_id=self.user_id)

        # 3. Resolve and set reaction on user message if present
        react_match = REACT_TAG_RE.search(final_raw)
        reaction_obj = None
        if react_match:
            reaction_attrs = react_match.group(1)
            reaction_obj = resolve_reaction(reaction_attrs)

        final_text = strip_delivery_tags(final_raw)

        if rp_is_inline and rp_banner:
            final_text = f"{rp_banner}\n\n{final_text}" if final_text else rp_banner

        reply_params = (
            ReplyParameters(message_id=self.target_message_id)
            if (self.should_reply and self.target_message_id)
            else None
        )

        if final_text:
            chunks = split_telegram_text(final_text)
            if not self.message_id:
                html_text = md_to_telegram_html(chunks[0])
                try:
                    await self.bot.send_message(
                        self.chat_id,
                        html_text,
                        parse_mode=ParseMode.HTML,
                        reply_parameters=reply_params,
                    )
                except Exception:
                    await self.bot.send_message(
                        self.chat_id,
                        chunks[0],
                        parse_mode=None,
                        reply_parameters=reply_params,
                    )
            else:
                await self._safe_edit(chunks[0], finalize=True)
            for chunk in chunks[1:]:
                try:
                    await self.bot.send_message(
                        self.chat_id,
                        md_to_telegram_html(chunk),
                        parse_mode=ParseMode.HTML,
                    )
                except TelegramBadRequest:
                    await self.bot.send_message(self.chat_id, chunk, parse_mode=None)
        elif self.message_id:
            try:
                await self.bot.delete_message(self.chat_id, self.message_id)
            except Exception:
                pass

        # Send separate follow-up RP reply if rp_banner was not inline
        if rp_banner and not rp_is_inline:
            rp_reply_params = (
                ReplyParameters(message_id=self.target_message_id)
                if self.target_message_id
                else None
            )
            try:
                await self.bot.send_message(
                    self.chat_id,
                    rp_banner,
                    parse_mode=ParseMode.HTML,
                    reply_parameters=rp_reply_params,
                )
            except Exception as e:
                logger.warning(f"Failed to send follow-up RP banner: {e}")

        # Send Photo Pair Flow: 1. Main photo (with kuukhenki) -> 2. Spoiler photo (full body secret) as reply
        if send_photos:
            photo1_path = config.BASE_DIR / "assets" / "columbina_with_kuukhenki.jpg"
            photo2_path = config.BASE_DIR / "assets" / "columbina_secret.jpg"

            if photo1_path.exists():
                try:
                    caption1 = "Вот я и мой верный спутник куухенки ✨ 🌸"
                    p1_msg = await self.bot.send_photo(
                        chat_id=self.chat_id,
                        photo=FSInputFile(str(photo1_path)),
                        caption=caption1,
                    )
                    await asyncio.sleep(0.5)

                    if photo2_path.exists():
                        surprise_caption = random.choice(SURPRISE_CAPTIONS)
                        html_caption = md_to_telegram_html(surprise_caption)
                        await self.bot.send_photo(
                            chat_id=self.chat_id,
                            photo=FSInputFile(str(photo2_path)),
                            caption=html_caption,
                            parse_mode=ParseMode.HTML,
                            has_spoiler=True,
                            reply_parameters=ReplyParameters(message_id=p1_msg.message_id),
                        )
                except Exception as e:
                    logger.warning(f"Failed to send photo pair: {e}")

        # Send native Telegram sticker
        if sticker_file_id:
            sticker_reply_params = (
                ReplyParameters(message_id=self.target_message_id)
                if self.target_message_id
                else None
            )
            try:
                await self.bot.send_sticker(
                    self.chat_id,
                    sticker_file_id,
                    reply_parameters=sticker_reply_params,
                )
                if self.user_id:
                    asset_harvester.record_sent_sticker(self.user_id, sticker_file_id)
            except Exception as e:
                logger.warning(f"Failed to send sticker {sticker_file_id}: {e}")

        # Set reaction on user's message
        if reaction_obj and self.target_message_id:
            try:
                await self.bot.set_message_reaction(
                    chat_id=self.chat_id,
                    message_id=self.target_message_id,
                    reaction=[reaction_obj],
                )
                logger.info(f"Successfully set reaction {reaction_obj} on message {self.target_message_id}")
            except Exception as e:
                logger.warning(f"Failed to set initial reaction {reaction_obj}: {e}")
                # Fallback to standard heart
                try:
                    await self.bot.set_message_reaction(
                        chat_id=self.chat_id,
                        message_id=self.target_message_id,
                        reaction=[ReactionTypeEmoji(emoji="❤")],
                    )
                    logger.info(f"Set fallback reaction ❤ on message {self.target_message_id}")
                except Exception as e2:
                    logger.warning(f"Fallback reaction failed: {e2}")

        return final_text

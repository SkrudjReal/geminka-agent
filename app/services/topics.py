"""Topic / Group Chat Management for Geminka.

Allows configuring active Telegram group topics (threads) where Geminka listens and replies.
Stores configured topics persistently in JSON.
"""

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Bot
from aiogram.enums import ChatMemberStatus

from app.core import config
from app.core.files import atomic_write_json, load_json

logger = logging.getLogger("geminka-topics")

TOPICS_FILE = config.DATA_DIR / "active_topics.json"


class TopicManager:
    def __init__(self, storage_file: Path = TOPICS_FILE):
        self.storage_file = storage_file
        self.data: Dict[str, Any] = {
            "topics": {}  # "chat_id:topic_id" -> {chat_id, topic_id, chat_title, added_by, created_at}
        }
        self.load()

    def load(self) -> None:
        if self.storage_file.exists():
            try:
                saved = load_json(self.storage_file, {})
                if "topics" in saved:
                    self.data["topics"] = saved["topics"]
                logger.info(f"TopicManager loaded {len(self.data['topics'])} active topics.")
            except Exception as e:
                logger.warning(f"Failed to load topics: {e}")

    def save(self) -> None:
        try:
            atomic_write_json(self.storage_file, self.data)
        except Exception as e:
            logger.warning(f"Failed to save topics: {e}")

    @staticmethod
    def parse_topic_link(raw_input: str) -> Optional[Tuple[int, int]]:
        """
        Parses links like:
        - https://t.me/c/4488980222/5
        - https://t.me/c/4488980222/5/123
        - t.me/c/4488980222/5
        - -1004488980222 5
        - -1004488980222:5
        """
        text = raw_input.strip()

        # 1. Regex for t.me/c/<chat_num>/<topic_id>
        c_match = re.search(r"t\.me/c/(\d+)/(\d+)", text)
        if c_match:
            chat_num = c_match.group(1)
            topic_id = int(c_match.group(2))
            if not chat_num.startswith("100"):
                chat_id = int(f"-100{chat_num}")
            else:
                chat_id = int(f"-{chat_num}")
            return chat_id, topic_id

        # 2. Regex for direct chat_id and topic_id (e.g. -1004488980222:5 or -1004488980222 5)
        direct_match = re.search(r"(-?\d{8,})[:\s/]+(\d+)", text)
        if direct_match:
            raw_cid = direct_match.group(1)
            topic_id = int(direct_match.group(2))
            cid_int = int(raw_cid)
            if cid_int > 0:
                if not raw_cid.startswith("100"):
                    cid_int = int(f"-100{raw_cid}")
                else:
                    cid_int = int(f"-{raw_cid}")
            return cid_int, topic_id

        return None

    async def check_bot_in_chat(self, bot: Bot, chat_id: int) -> Tuple[bool, str]:
        """Checks if the bot is present in the specified chat and returns (is_in_chat, title)."""
        try:
            chat = await bot.get_chat(chat_id)
            bot_user = await bot.get_me()
            bot_member = await bot.get_chat_member(chat_id, bot_user.id)
            if bot_member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                return True, chat.title or str(chat_id)
            if bot_member.status == ChatMemberStatus.RESTRICTED:
                return bool(getattr(bot_member, "can_send_messages", False)), chat.title or str(chat_id)
            return False, chat.title or str(chat_id)
        except Exception as e:
            logger.warning(f"Error checking bot in chat {chat_id}: {e}")
            return False, ""

    def add_topic(self, chat_id: int, topic_id: int, chat_title: str, added_by: int) -> bool:
        key = f"{chat_id}:{topic_id}"
        self.data["topics"][key] = {
            "chat_id": chat_id,
            "topic_id": topic_id,
            "chat_title": chat_title or f"Chat {chat_id}",
            "added_by": added_by,
            "created_at": time.time(),
        }
        self.save()
        logger.info(f"Added active topic {key} ('{chat_title}') by user {added_by}")
        return True

    def remove_topic(self, chat_id: int, topic_id: int) -> bool:
        key = f"{chat_id}:{topic_id}"
        if key in self.data["topics"]:
            del self.data["topics"][key]
            self.save()
            logger.info(f"Removed active topic {key}")
            return True
        return False

    def is_topic_active(self, chat_id: int, topic_id: Optional[int]) -> bool:
        """Returns True if messages in this chat and topic should be processed."""
        if topic_id is None:
            # A message without a thread ID is not a forum topic message.  Do
            # not treat it as General (or any other topic), otherwise a
            # configured forum can accidentally enable ordinary group traffic.
            return False
        return f"{chat_id}:{topic_id}" in self.data["topics"]

    def get_active_topics(self) -> List[Dict[str, Any]]:
        return list(self.data["topics"].values())


topic_manager = TopicManager()

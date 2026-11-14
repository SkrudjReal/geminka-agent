"""Persistent, non-overlapping 100-message batches for optional group replies."""

import asyncio
import json
import logging
import sqlite3
from html import escape

from aiogram.types import ReplyParameters

from app.core import config
from app.core.state import state_store
from app.services.agy_cli import AgyCliClient
from app.services.topics import topic_manager

logger = logging.getLogger(__name__)
PROMPT = config.BASE_DIR / "agent_skills/geminka/references/chat-observer.md"


class ChatObserver:
    def __init__(self, bot, client, *, path=None, topics=topic_manager, store=state_store):
        self.bot, self.client, self.topics, self.store = bot, client, topics, store
        path = path or config.DATA_DIR / "chat_observer.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS messages (
            chat_id INTEGER, thread_id INTEGER, message_id INTEGER, user_id INTEGER,
            text TEXT, username TEXT, reply_to INTEGER, eligible INTEGER, status TEXT DEFAULT 'pending',
            PRIMARY KEY(chat_id, message_id))""")
        self.db.execute("CREATE INDEX IF NOT EXISTS pending ON messages(status,chat_id,thread_id,message_id)")
        self.db.commit()
        self.task = None

    def enabled(self, chat_id, thread_id):
        return self.topics.is_chat_active(chat_id) or self.topics.is_topic_active(chat_id, thread_id)

    def observe(self, message, *, addressed=False):
        user = message.from_user
        if (not user or user.id == self.bot.id or message.sender_chat
                or not self.enabled(message.chat.id, message.message_thread_id)
                or self.store.is_debug_mode(user.id)):
            return
        text = message.text or message.caption or f"[{message.content_type}]"
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?,?, 'pending')", (
                message.chat.id, message.message_thread_id or 0, message.message_id, user.id,
                text, user.username, message.reply_to_message.message_id if message.reply_to_message else None,
                int(not addressed and not text.startswith('/')),
            ))

    def claim(self):
        with self.db:
            group = self.db.execute("""SELECT chat_id,thread_id FROM messages WHERE status='pending'
                GROUP BY chat_id,thread_id HAVING count(*)>=100 ORDER BY min(rowid) LIMIT 1""").fetchone()
            if group is None:
                return []
            rows = self.db.execute("""SELECT * FROM messages WHERE status='pending' AND chat_id=? AND thread_id=?
                ORDER BY message_id LIMIT 100""", tuple(group)).fetchall()
            # Claim before generation: a crash or uncertain send must not replay a batch.
            self.db.executemany("UPDATE messages SET status='attempted' WHERE chat_id=? AND message_id=?",
                                [(r['chat_id'], r['message_id']) for r in rows])
        return [dict(row) for row in rows]

    async def generate(self, rows):
        agy = AgyCliClient(command=self.client._agy.command, timeout_seconds=90, sandbox_enabled=True)
        try:
            chunks = [chunk async for chunk in agy.stream(
                -abs(rows[0]['chat_id']), model=self.client.get_user_model(config.settings.owner_user_id),
                effort="low", debug=True,
                messages=[{"role": "system", "content": config.get_system_prompt() + '\n' + PROMPT.read_text()},
                          {"role": "user", "content": json.dumps(rows, ensure_ascii=False)}],
                tool_context={"requester_id": "summary", "chat_id": str(rows[0]['chat_id'])},
            )]
            return ''.join(chunks).strip()
        finally:
            await agy.aclose()

    async def tick(self):
        rows = self.claim()
        if not rows:
            return False
        chat_id, thread_id = rows[0]['chat_id'], rows[0]['thread_id'] or None
        if not self.enabled(chat_id, thread_id):
            return True
        visible = [row for row in rows if not self.store.is_debug_mode(row['user_id'])]
        if not visible:
            return True
        try:
            raw = await asyncio.wait_for(self.generate(visible), timeout=100)
            if raw == 'NO_REPLY':
                return True
            if raw.startswith('```json') and raw.endswith('```'):
                raw = raw[7:-3].strip()
            result = json.loads(raw)
            target = next((r for r in visible if type(result.get('message_id')) is int
                           and r['message_id'] == result['message_id'] and r['eligible']), None)
            text = result.get('text')
            if target is None or not isinstance(text, str) or not 1 <= len(text.strip()) <= 400:
                raise ValueError('Invalid observer reply')
            if not self.enabled(chat_id, thread_id) or self.store.is_debug_mode(target['user_id']):
                return True
            sent = await self.bot.send_message(
                chat_id, escape(text.strip()), parse_mode='HTML', message_thread_id=thread_id,
                reply_parameters=ReplyParameters(message_id=target['message_id'], allow_sending_without_reply=False))
            logger.info('Observer reply chat=%s target=%s sent=%s', chat_id, target['message_id'], sent.message_id)
        except Exception as exc:
            logger.warning('Observer batch failed chat=%s type=%s', chat_id, type(exc).__name__)
        return True

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception as exc:
                logger.warning('Observer worker failed type=%s', type(exc).__name__)
            await asyncio.sleep(1)

    def start(self):
        self.task = asyncio.create_task(self.run())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.db.close()

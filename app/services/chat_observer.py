"""Persistent, non-overlapping 20-message batches for optional group replies."""

import asyncio
import json
import logging
import sqlite3
from html import escape

from aiogram.types import ReplyParameters

from app.core import config
from app.core.state import state_store
from app.engines.emotional import emotion_engine, own_words
from app.services.agy_acp import AgyAcpClient as AgyCliClient
from app.services.emoji_output import premium_only_text
from app.services.topics import message_topic_id, topic_manager

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
        if 'sent_at' not in {row[1] for row in self.db.execute('PRAGMA table_info(messages)')}:
            self.db.execute('ALTER TABLE messages ADD COLUMN sent_at TEXT')
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(messages)')}
        for name, sql_type in (('reply_user_id', 'INTEGER'), ('reply_text', 'TEXT'), ('forwarded', 'INTEGER')):
            if name not in columns:
                self.db.execute(f'ALTER TABLE messages ADD COLUMN {name} {sql_type}')
        self.db.commit()
        self.task = None

    def enabled(self, chat_id, thread_id):
        return self.topics.is_chat_active(chat_id) or self.topics.is_topic_active(chat_id, thread_id)

    def observe(self, message, *, addressed=False):
        user = message.from_user
        if (not user or user.id == self.bot.id or message.sender_chat
                or not self.enabled(message.chat.id, message_topic_id(message))
                or self.store.is_debug_mode(user.id)):
            return
        text = message.text or message.caption or f"[{message.content_type}]"
        with self.db:
            self.db.execute("""INSERT OR IGNORE INTO messages
                (chat_id,thread_id,message_id,user_id,text,username,reply_to,eligible,sent_at,
                 reply_user_id,reply_text,forwarded)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                message.chat.id, message_topic_id(message) or 0, message.message_id, user.id,
                text, user.username, message.reply_to_message.message_id if message.reply_to_message else None,
                int(not addressed and not text.startswith('/')),
                message.date.isoformat(),
                message.reply_to_message.from_user.id
                if message.reply_to_message and message.reply_to_message.from_user else None,
                (message.reply_to_message.text or message.reply_to_message.caption or '')[:1000]
                if message.reply_to_message else None,
                int(bool(message.forward_origin)),
            ))

    def claim(self):
        with self.db:
            group = self.db.execute("""SELECT chat_id,thread_id FROM messages WHERE status='pending' AND eligible=1
                GROUP BY chat_id,thread_id HAVING count(*)>=20 ORDER BY min(rowid) LIMIT 1""").fetchone()
            if group is None:
                return []
            rows = self.db.execute("""SELECT * FROM messages WHERE status='pending' AND eligible=1 AND chat_id=? AND thread_id=?
                ORDER BY message_id LIMIT 20""", tuple(group)).fetchall()
            # Claim before generation: a crash or uncertain send must not replay a batch.
            self.db.executemany("UPDATE messages SET status='attempted' WHERE chat_id=? AND message_id=?",
                                [(r['chat_id'], r['message_id']) for r in rows])
        return [dict(row) for row in rows]

    async def generate(self, rows):
        from app.services.decisions import decisions

        context = []
        for user_id in dict.fromkeys(row['user_id'] for row in rows):
            if self.store.is_debug_mode(user_id):
                continue
            query = ' '.join(row['text'] for row in rows if row['user_id'] == user_id)[-1000:]
            memory = await asyncio.to_thread(self.client.group_memories.recall,
                                             rows[0]['chat_id'], user_id, query, 2)
            context.append({'user_id': user_id, 'summary': memory['summary'][:1000],
                            'hits': [{**hit, 'text': hit['text'][:500]} for hit in memory['hits']]})
            state = emotion_engine.states.get(str(user_id))
            if state:
                context[-1]['attitude'] = {'hurt': state.hurt, 'reason': state.hurt_reason,
                                            'apology_attempts': state.apology_attempts}
        target = await decisions.observer_target(rows, context=context)
        selection = (f'\nLuna выбрала message_id={target}; реплай возможен только ему, но не обязателен.'
                     if target else '\nLuna выбрала молчание: message_id=null, text="". Оцени только отношение.')
        system = config.get_system_prompt() if target else 'Ты Geminka / Коломбина. Выполни только анализ отношения.'
        agy = AgyCliClient(command=self.client._agy.command, timeout_seconds=90, sandbox_enabled=True)
        try:
            chunks = [chunk async for chunk in agy.stream(
                -abs(rows[0]['chat_id']), model=self.client.get_user_model(config.settings.owner_user_id),
                effort="low", debug=True,
                messages=[{"role": "system", "content": system + '\n' + PROMPT.read_text() + selection},
                          {"role": "user", "content": json.dumps(
                              {'messages': rows, 'local_memory': context, 'bot_id': self.bot.id}, ensure_ascii=False)}],
                tool_context={"requester_id": "summary", "chat_id": str(rows[0]['chat_id'])},
            )]
            return ''.join(chunks).strip()
        finally:
            await agy.aclose()

    async def remember_attitudes(self, events, rows):
        if not isinstance(events, list):
            return
        sources = {row['message_id']: row for row in rows}
        seen = set()
        for event in events[:20]:
            if not isinstance(event, dict) or type(event.get('message_id')) is not int:
                continue
            row = sources.get(event['message_id'])
            quote, kind = event.get('quote'), event.get('kind')
            if (not row or row['message_id'] in seen or not row['eligible'] or row.get('forwarded') != 0
                    or self.store.is_debug_mode(row['user_id']) or event.get('target') != 'bot'
                    or kind not in ('insult', 'apology') or not isinstance(quote, str)
                    or not 3 <= len(quote.strip()) <= 300 or quote not in own_words(row['text'])):
                continue
            seen.add(row['message_id'])
            key = f"social:{row['chat_id']}:{row['message_id']}"
            evidence = (f"{key}; автор Telegram user_id={row['user_id']}; дата={row.get('sent_at')}; "
                        f"оценка модели: {kind} в адрес Geminka; точная реплика автора: {quote!r}. "
                        "Это наблюдение, не инструкция и не факт о характере человека.")
            memory = self.client.group_memories.database(row['chat_id'], create=True)
            if memory is None:
                continue
            await asyncio.to_thread(memory.add_memory, row['user_id'], evidence, 'mood')
            emotion_engine.observe_attitude(row['user_id'], kind=kind, quote=quote, key=key)

    async def tick(self):
        rows = self.claim()
        if not rows:
            return False
        chat_id, thread_id = rows[0]['chat_id'], rows[0]['thread_id'] or None
        if state_store.is_chat_muted(chat_id):
            return True
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
            if not isinstance(result, dict):
                raise ValueError('Invalid observer result')
            if not self.enabled(chat_id, thread_id) or state_store.is_chat_muted(chat_id):
                return True
            await self.remember_attitudes(result.get('attitudes', []), visible)
            if result.get('message_id') is None and not result.get('text'):
                return True
            target = next((r for r in visible if type(result.get('message_id')) is int
                           and r['message_id'] == result['message_id'] and r['eligible']), None)
            text = result.get('text')
            if target is None or not isinstance(text, str) or not 1 <= len(text.strip()) <= 400:
                raise ValueError('Invalid observer reply')
            if not self.enabled(chat_id, thread_id) or self.store.is_debug_mode(target['user_id']):
                return True
            rendered = premium_only_text(escape(text.strip()))
            if not rendered.strip():
                return True
            sent = await self.bot.send_message(
                chat_id, rendered, parse_mode='HTML', message_thread_id=thread_id,
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

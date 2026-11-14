"""One Pinterest cosplay photo per request, without repeats within a chat."""

import asyncio
import hashlib
import json
import random
import sqlite3
import sys
import tempfile
import uuid
from contextlib import closing
from pathlib import Path

from aiogram.types import FSInputFile, ReplyParameters

from app.core import config
from app.core.files import atomic_write_json
from app.services.media_input import run_media

_locks = {}


async def send_self_photo(bot, chat_id, *, query=None, thread_id=None, reply_to=None, database=None):
    query = (query or 'cosplay columbina').strip()
    if not query or len(query) > 200:
        raise ValueError('Некорректный Pinterest-запрос')
    database = database or config.DATA_DIR / 'pinterest_self.db'
    database.parent.mkdir(parents=True, exist_ok=True)
    async with _locks.setdefault(chat_id, asyncio.Lock()):
        with closing(sqlite3.connect(database)) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS used (
                chat_id INTEGER, url TEXT, pin_url TEXT, digest TEXT,
                PRIMARY KEY(chat_id, digest), UNIQUE(chat_id, url))''')
            used = db.execute('SELECT url,pin_url,digest FROM used WHERE chat_id=?', (chat_id,)).fetchall()
            with tempfile.TemporaryDirectory(prefix='geminka-pinterest-') as directory:
                root = Path(directory)
                registry = root / 'used.json'
                atomic_write_json(registry, {'used': [{'url': r[0], 'pin_url': r[1]} for r in used]})
                raw = await run_media(
                    sys.executable, str(config.BASE_DIR / 'scripts/select_post_image.py'),
                    '--query', query, '--job-id', uuid.uuid4().hex,
                    '--output-dir', str(root / 'images'), '--registry', str(registry), timeout=240)
                candidates = json.loads(raw)['candidates']
                random.shuffle(candidates)
                hashes = {r[2] for r in used}
                pins = {r[1] for r in used if r[1]}
                urls = {r[0] for r in used}
                for item in candidates:
                    path = Path(item['path']).resolve()
                    if not path.is_relative_to(root.resolve()) or not path.is_file():
                        continue
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    if digest in hashes or item['url'] in urls or item.get('pin_url') in pins:
                        continue
                    # Reserve before Telegram: uncertain deliveries must not resend the same image.
                    with db:
                        db.execute('INSERT INTO used VALUES (?,?,?,?)',
                                   (chat_id, item['url'], item.get('pin_url'), digest))
                    return await bot.send_photo(
                        chat_id=chat_id, photo=FSInputFile(path), message_thread_id=thread_id,
                        reply_parameters=ReplyParameters(message_id=reply_to, allow_sending_without_reply=False)
                        if reply_to else None)
                raise ValueError('Pinterest не вернул новых картинок для этого чата')

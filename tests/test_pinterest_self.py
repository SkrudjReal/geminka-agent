import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import pinterest_self as service
from app.services.streamer import PHOTO_PAIR_TAG_RE


@pytest.mark.asyncio
async def test_no_repeats_per_chat_even_with_changed_url(monkeypatch, tmp_path):
    async def search(*args, **kwargs):
        root = Path(args[args.index('--output-dir') + 1])
        root.mkdir()
        candidates = []
        for index in range(2):
            path = root / f'{index}.jpg'
            path.write_bytes(f'image-{index}'.encode())
            candidates.append({'path': str(path), 'url': str(root) + f'/{index}', 'pin_url': None})
        return json.dumps({'candidates': candidates})

    monkeypatch.setattr(service, 'run_media', search)
    monkeypatch.setattr(service.random, 'shuffle', lambda _: None)
    bot = SimpleNamespace(send_photo=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    database = tmp_path / 'history.db'
    for _ in range(2):
        await service.send_self_photo(bot, -1, database=database, thread_id=3, reply_to=10)
    with pytest.raises(ValueError, match='новых картинок'):
        await service.send_self_photo(bot, -1, database=database)
    assert bot.send_photo.await_count == 2
    assert bot.send_photo.call_args.kwargs['reply_parameters'].message_id == 10
    await service.send_self_photo(bot, -2, database=database)
    assert bot.send_photo.await_count == 3


@pytest.mark.parametrize('tag', ['<tg-send-photos/>', '<tg-photo-pair/>',
                               '<tg-send-photos query="cosplay columbina portrait"/>'])
def test_photo_tag_and_query_are_hidden(tag):
    assert PHOTO_PAIR_TAG_RE.sub('', tag + 'текст') == 'текст'


@pytest.mark.asyncio
async def test_search_failure_never_sends_local_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(service, 'run_media', AsyncMock(side_effect=TimeoutError))
    bot = SimpleNamespace(send_photo=AsyncMock())
    with pytest.raises(TimeoutError):
        await service.send_self_photo(bot, 1, database=tmp_path / 'history.db')
    bot.send_photo.assert_not_awaited()

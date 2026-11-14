import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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


@pytest.mark.asyncio
@pytest.mark.parametrize('cookies', ['', 'data/pinterest-cookies.json'])
async def test_search_failure_explains_cookie_configuration(monkeypatch, tmp_path, cookies):
    monkeypatch.setenv('PINTEREST_COOKIES_FILE', cookies)
    monkeypatch.setattr(service, 'run_media', AsyncMock(side_effect=ValueError('worker failed')))
    bot = SimpleNamespace(send_photo=AsyncMock())
    with pytest.raises(service.PinterestSearchUnavailable) as error:
        await service.send_self_photo(bot, 1, database=tmp_path / 'history.db')
    assert ('Проверь cookies' if cookies else 'PINTEREST_COOKIES_FILE') in str(error.value)
    bot.send_photo.assert_not_awaited()


def test_pinterest_worker_loads_configured_cookies(monkeypatch, tmp_path):
    from scripts import select_post_image as script

    scraper = SimpleNamespace(search_and_download=Mock(return_value=[]))
    scraper.with_cookies_path = Mock(return_value=scraper)
    monkeypatch.setattr(script.PinterestDL, 'with_api', lambda **kwargs: scraper)
    monkeypatch.setenv('PINTEREST_COOKIES_FILE', str(tmp_path / 'private.json'))
    with pytest.raises(RuntimeError, match='no unused'):
        script.select_images('cosplay columbina', 'a' * 32, tmp_path / 'images', tmp_path / 'used.json')
    scraper.with_cookies_path.assert_called_once_with(tmp_path / 'private.json')

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import pinterest_self as service
from app.services.streamer import PHOTO_PAIR_TAG_RE


@pytest.mark.asyncio
async def test_no_repeats_per_chat_by_url(monkeypatch, tmp_path):
    async def search(*args, **kwargs):
        root = Path(args[args.index('--output-dir') + 1])
        root.mkdir()
        candidates = []
        for index in range(2):
            path = root / f'{index}.jpg'
            path.write_bytes(f'image-{index}'.encode())
            candidates.append({'path': str(path), 'url': f'https://image/{index}', 'pin_url': None})
        return json.dumps({'candidates': candidates})

    monkeypatch.setattr(service, 'run_media', search)
    monkeypatch.setattr(service, '_approved_candidates', AsyncMock(side_effect=lambda items, root, count: items[:count]))
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

    scraper = SimpleNamespace(iter_search=Mock(return_value=iter([])),
                              _download_and_save=Mock(return_value=[]))
    scraper.with_cookies_path = Mock(return_value=scraper)
    monkeypatch.setattr(script.PinterestDL, 'with_api', lambda **kwargs: scraper)
    monkeypatch.setenv('PINTEREST_COOKIES_FILE', str(tmp_path / 'private.json'))
    with pytest.raises(RuntimeError, match='no unused'):
        script.select_images('cosplay columbina', 'a' * 32, tmp_path / 'images', tmp_path / 'used.json')
    scraper.with_cookies_path.assert_called_once_with(tmp_path / 'private.json')


def test_worker_paginates_filters_then_shuffles_before_downloading(monkeypatch, tmp_path):
    from scripts import select_post_image as script

    media = [SimpleNamespace(src=f'https://image/{i}', origin=f'https://pin/{i}',
                             video_stream=None) for i in range(250)]
    scraper = SimpleNamespace(iter_search=Mock(side_effect=lambda *a, **kw: iter(media)),
                              _download_and_save=Mock(return_value=[]))
    monkeypatch.setattr(script.PinterestDL, 'with_api', lambda **kw: scraper)
    monkeypatch.delenv('PINTEREST_COOKIES_FILE', raising=False)
    monkeypatch.setattr(script.random, 'shuffle', lambda values: values.reverse())
    registry = tmp_path / 'used.json'
    registry.write_text(json.dumps({'used': [{'url': f'https://image/{i}'} for i in range(100)]}))
    with pytest.raises(RuntimeError, match='no unused'):
        script.select_images('cosplay columbina', 'a' * 32, tmp_path / 'images', registry)
    downloaded = scraper._download_and_save.call_args.args[0]
    assert len(downloaded) == 80
    assert downloaded[0].src == 'https://image/249'
    assert all(int(item.src.rsplit('/', 1)[1]) >= 100 for item in downloaded)


@pytest.mark.asyncio
@pytest.mark.parametrize('count,expected', [(10, 10), (99, 10), (3, 3)])
async def test_album_limit_and_no_repeats(monkeypatch, tmp_path, count, expected):
    async def search(*args, **kwargs):
        root = Path(args[args.index('--output-dir') + 1])
        root.mkdir()
        candidates = []
        for i in range(10):
            path = root / f'{i}.jpg'
            path.write_bytes(f'image-{i}'.encode())
            candidates.append({'path': str(path), 'url': f'https://image/{i}', 'pin_url': None})
        return json.dumps({'candidates': candidates + candidates})

    monkeypatch.setattr(service, 'run_media', search)
    monkeypatch.setattr(service, '_approved_candidates', AsyncMock(side_effect=lambda items, root, count: items[:count]))
    bot = SimpleNamespace(send_photo=AsyncMock(), send_media_group=AsyncMock())
    database = tmp_path / 'history.db'
    await service.send_self_photo(bot, -1, count=count, database=database, thread_id=3, reply_to=10)
    kwargs = bot.send_media_group.call_args.kwargs
    assert len(kwargs['media']) == expected
    assert kwargs['message_thread_id'] == 3
    assert kwargs['reply_parameters'].message_id == 10
    bot.send_photo.assert_not_awaited()
    if expected == 10:
        with pytest.raises(ValueError, match='новых картинок'):
            await service.send_self_photo(bot, -1, count=count, database=database)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['photo', 'video'])
async def test_vision_rejection_and_video_delivery(monkeypatch, tmp_path, kind):
    async def search(*args, **kwargs):
        root = Path(args[args.index('--output-dir') + 1])
        root.mkdir()
        path = root / ('image.jpg' if kind == 'photo' else 'video.mp4')
        path.write_bytes(b'fixture')
        return json.dumps({'candidates': [{'path': str(path), 'kind': kind,
                                          'url': 'https://image/new', 'pin_url': 'https://pin/new'}]})
    monkeypatch.setattr(service, 'run_media', search)
    vision = AsyncMock(return_value=[])
    monkeypatch.setattr(service, '_approved_candidates', vision)
    bot = SimpleNamespace(send_photo=AsyncMock(), send_video=AsyncMock(), send_media_group=AsyncMock())
    database = tmp_path / 'history.db'
    with pytest.raises(ValueError):
        await service.send_self_photo(bot, 1, database=database)
    bot.send_photo.assert_not_awaited()
    bot.send_video.assert_not_awaited()
    vision.side_effect = lambda items, root, count: items[:count]
    await service.send_self_photo(bot, 1, database=database)
    assert (bot.send_video if kind == 'video' else bot.send_photo).await_count == 1
    # Same URL is filtered before another vision call.
    with pytest.raises(ValueError):
        await service.send_self_photo(bot, 1, database=database)
    assert vision.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('response', ['{"approved": [2]}', '{"approved": [99]}', 'not json'])
async def test_vision_strict_indexes_and_cleanup(monkeypatch, tmp_path, response):
    from PIL import Image

    from app.services import agy_acp

    path = tmp_path / 'photo.jpg'
    Image.new('RGB', (40, 40), 'blue').save(path)
    client = SimpleNamespace(list_models=AsyncMock(return_value=['claude', 'gemini-test']), aclose=AsyncMock())
    prompts = []
    async def stream(**kwargs):
        prompts.append(kwargs)
        yield response
    client.stream_image = stream
    monkeypatch.setattr(agy_acp, 'AgyAcpClient', lambda **kwargs: client)
    items = [{'path': str(path), 'url': f'https://image/{i}'} for i in range(3)]
    if response == '{"approved": [2]}':
        assert await service._approved_candidates(items, tmp_path, 3) == [items[1]]
        assert (tmp_path / 'vision-0.jpg').is_file()
        assert prompts[0]['model'] == 'gemini-test'
    else:
        with pytest.raises(ValueError):
            await service._approved_candidates(items, tmp_path, 3)
    client.aclose.assert_awaited_once()


def test_video_preview_and_preservation(monkeypatch, tmp_path):
    import shutil
    import subprocess

    from scripts import select_post_image as script

    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg/ffprobe required')
    output = tmp_path / 'images'
    output.mkdir()
    video = output / 'clip.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=512x512:d=1',
                    '-threads', '1', str(video)], check=True)
    media = SimpleNamespace(src='https://image/1', origin='https://pin/1', video_stream=True,
                            local_path=video, resolution=(512, 512), alt=None)
    scraper = SimpleNamespace(iter_search=Mock(return_value=iter([media])),
                              _download_and_save=Mock(return_value=[media]))
    monkeypatch.setattr(script.PinterestDL, 'with_api', lambda **kw: scraper)
    monkeypatch.delenv('PINTEREST_COOKIES_FILE', raising=False)
    result = script.select_images('columbina', 'a' * 32, output, tmp_path / 'used.json', True)
    candidate = result['candidates'][0]
    assert candidate['kind'] == 'video'
    assert Path(candidate['path']).is_file()
    assert Path(candidate['preview_path']).is_file()
    assert scraper._download_and_save.call_args.args[2] is True


@pytest.mark.asyncio
async def test_multimodal_acp_contains_pixels_and_no_mcp(monkeypatch, tmp_path):
    import base64

    from app.services.agy_acp import AgyAcpClient

    path = tmp_path / 'image.jpg'
    path.write_bytes(b'jpeg-pixels')
    client = AgyAcpClient()
    calls = []
    async def once(model, effort, content, context):
        calls.append((content, context))
        yield '{"approved": [1]}'
    monkeypatch.setattr(client, '_stream_once', once)
    try:
        assert [chunk async for chunk in client.stream_image(model='gemini', prompt='check', path=path)]
        content, context = calls[0]
        assert content[1]['type'] == 'image'
        assert content[1]['mimeType'] == 'image/jpeg'
        assert base64.b64decode(content[1]['data']) == b'jpeg-pixels'
        assert context == {'requester_id': 'summary'}
    finally:
        await client.aclose()

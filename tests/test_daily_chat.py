import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChat
from aiogram.types import ReplyParameters

from app.services import daily_chat
from app.services.daily_chat import DailyChat, choose_participant, render_quote, unpack
from app.services.topics import TopicManager


def make_job(tmp_path):
    topics = TopicManager(tmp_path / "topics.json")
    topics.add_chat(-1001, "group", 1)
    bot = SimpleNamespace(id=999, send_message=AsyncMock(return_value=SimpleNamespace(message_id=123)),
                          get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))
    job = DailyChat(bot, None, path=tmp_path / "daily.json", topics=topics)
    return job


@pytest.mark.asyncio
async def test_daily_random_schedule_and_restart_dedup(tmp_path):
    job = make_job(tmp_path)
    now = datetime.now().astimezone()
    assert not job.due(-1001, now)
    entry = job.state["-1001"]
    assert entry["at"] >= now.timestamp()
    assert datetime.fromtimestamp(entry["at"]).date() == now.date()
    entry["at"] = now.timestamp() - 1
    job.compose = AsyncMock(return_value={"text": "@tester, ну и мем))", "reply_parameters": ReplyParameters(message_id=7, chat_id=-2002)})
    await job.tick(now)
    job.bot.send_message.assert_awaited_once()
    assert job.bot.send_message.call_args.kwargs["reply_parameters"].chat_id == -2002
    assert entry["status"] == "sent"
    restored = DailyChat(job.bot, None, path=job.path, topics=job.topics)
    restored.compose = AsyncMock()
    await restored.tick(now)
    restored.compose.assert_not_awaited()
    assert not restored.due(-1001, now + timedelta(days=1))
    assert restored.state["-1001"]["status"] == "scheduled"


@pytest.mark.asyncio
async def test_failed_delivery_is_not_repeated_and_removed_group_is_not_sent(tmp_path):
    job = make_job(tmp_path)
    now = datetime.now().astimezone()
    job.due(-1001, now)
    job.state["-1001"]["at"] = 0
    job.compose = AsyncMock(return_value={"text": "test))", "reply_parameters": ReplyParameters(message_id=7)})
    job.bot.send_message.side_effect = TimeoutError
    await job.tick(now)
    await job.tick(now)
    assert job.state["-1001"]["status"] == "failed"
    assert job.bot.send_message.await_count == 1
    job.topics.remove_chat(-1001)
    assert job.targets() == {}


def test_topics_are_deduplicated_per_group(tmp_path):
    job = make_job(tmp_path)
    job.topics.add_topic(-1001, 7, "group", 1)
    job.topics.add_topic(-1002, 8, "other", 1)
    assert job.targets() == {-1001: None, -1002: 8}


def test_unpack_fastmcp_and_error():
    assert unpack({"ok": True, "result": {"content": [{"type": "text", "text": '{"authors": []}'}]}}) == {"authors": []}
    with pytest.raises(RuntimeError):
        unpack({"ok": False})


@pytest.mark.parametrize("roll,is_bot", [(0.0, True), (0.29999, True), (0.3, False), (0.99, False)])
def test_participant_bot_probability_boundary(roll, is_bot):
    bot = {"profile": {"is_bot": True}}
    human = {"profile": {"is_bot": False}}
    rng = SimpleNamespace(random=lambda: roll, choice=lambda pool: pool[0])
    assert choose_participant([bot, human], rng)["profile"]["is_bot"] is is_bot


@pytest.mark.parametrize("flag", [True, False])
def test_participant_single_category_fallback(flag):
    candidate = {"profile": {"is_bot": flag}}
    rng = SimpleNamespace(random=lambda: 0.9, choice=lambda pool: pool[0])
    assert choose_participant([candidate], rng) is candidate


def test_missing_bot_flag_is_not_guessed_from_username():
    rng = SimpleNamespace(random=lambda: 0.0, choice=lambda pool: pool[0])
    with pytest.raises(ValueError):
        choose_participant([{"profile": {"username": "human_named_bot"}}], rng)


def quote_fixture():
    source = {"chat_id": -2002, "message_id": 7, "sender_user_id": 42,
              "text": "Я написал десять микросервисов, чтобы включать чайник."}
    response = {"source_chat_id": -2002, "source_message_id": 7, "quote": source["text"],
                "intro": "чайник-то уже прошёл архитектурный комитет?",
                "comment": "Смотрю, простое кипячение воды у нас теперь требует архитектурного комитета и собственного дата-центра. Чай-то в итоге успел завариться или всё ещё деплоится?))"}
    return source, response


def test_quote_is_exact_and_presentation_is_natural():
    source, response = quote_fixture()
    draft = render_quote(json.dumps(response), "tester", 42, [source])
    result = draft["text"]
    assert source["text"] not in result and response["intro"] in result
    assert draft["reply_parameters"].quote == source["text"]
    assert draft["reply_parameters"].chat_id == source["chat_id"]
    assert draft["reply_parameters"].message_id == source["message_id"]
    assert "Театральный перевод" not in result and "в архиве сохранилось" not in result
    assert "@tester" in result and result.endswith("))")
    assert '<tg-emoji emoji-id="5456184310895748720">' in result
    assert "<blockquote>" not in result


def test_quote_html_escapes_archive_and_model_text():
    source, response = quote_fixture()
    source["text"] = response["quote"] = "<script>Чайник & микросервисы</script>"
    response["intro"] = "<script>пример & текст</script>"
    draft = render_quote(json.dumps(response), "tester", 42, [source])
    result = draft["text"]
    assert "<script>" not in result
    assert "&lt;script&gt;пример &amp; текст&lt;/script&gt;" in result
    assert draft["reply_parameters"].quote == source["text"]


def test_native_quote_utf16_offset_and_formatting():
    source, response = quote_fixture()
    original = source["text"]
    source["text"] = "✨😀 " + original
    source["entities"] = [{"type": "bold", "offset": 4, "length": len(original)}]
    reply = render_quote(json.dumps(response), "tester", 42, [source])["reply_parameters"]
    assert reply.quote_position == 4
    assert reply.quote_entities[0].offset == 0
    assert reply.quote_entities[0].length == len(original)
    assert reply.allow_sending_without_reply is False


def test_native_quote_respects_telegram_limit_without_splitting_surrogates():
    source, response = quote_fixture()
    source["text"] = response["quote"] = "а" + "😀" * 900
    draft = render_quote(json.dumps(response), "tester", 42, [source])
    quote = draft["reply_parameters"].quote
    assert len(quote.encode("utf-16-le")) <= 2048
    assert source["text"].startswith(quote)
    assert quote not in draft["text"]


@pytest.mark.asyncio
async def test_external_reply_uses_member_source(tmp_path):
    job = make_job(tmp_path)
    assert await job.reply_chat(-2002, -1001) == -2002
    job.bot.get_chat_member.assert_awaited_once_with(-2002, 999)


@pytest.mark.asyncio
async def test_external_reply_uses_quote_for_inaccessible_group(tmp_path):
    job = make_job(tmp_path)
    job.bot.get_chat_member.side_effect = TelegramBadRequest(method=GetChat(chat_id=-2002), message="chat not found")
    assert await job.reply_chat(-2002, -1001) is None
    job.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_readable_chat_without_membership_uses_quote(tmp_path):
    job = make_job(tmp_path)
    job.bot.get_chat_member.return_value = SimpleNamespace(status="left")
    assert await job.reply_chat(-2002, -1001) is None


def test_blockquote_fallback_keeps_exact_source_without_reply():
    source, response = quote_fixture()
    draft = render_quote(json.dumps(response), "tester", 42, [source], native_reply=False)
    assert draft["reply_parameters"] is None
    assert f'<blockquote>{source["text"]}</blockquote>' in draft["text"]


@pytest.mark.parametrize("change", ["text", "sender", "private", "forward", "id"])
def test_quote_rejects_wrong_source(change):
    source, response = quote_fixture()
    if change == "text":
        response["quote"] = "Я такого никогда не говорил."
    elif change == "sender":
        source["sender_user_id"] = 43
    elif change == "private":
        source["chat_id"] = response["source_chat_id"] = 42
    elif change == "forward":
        source["forward_info"] = {"from_user_id": 99}
    else:
        response["source_message_id"] = 8
    with pytest.raises(ValueError):
        render_quote(json.dumps(response), "tester", 42, [source])


def test_truncated_model_response_and_skip():
    assert render_quote("SKIP", "tester", 42, []) is None
    with pytest.raises(ValueError):
        render_quote('@tester Смотрю, ты в архиве так яростно вое))', "tester", 42, [])


@pytest.mark.asyncio
async def test_disabled_scheduler_never_starts(tmp_path, monkeypatch):
    job = make_job(tmp_path)
    monkeypatch.setenv("DAILY_CHAT_ENABLED", "false")
    job.start()
    assert job.task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("member", [False, True])
async def test_compose_queries_other_groups_by_exact_sender_and_allows_bots(tmp_path, monkeypatch, fallback, automatic, member):
    job = make_job(tmp_path)
    job.bot.id = 999
    job.bot.get_chat_member.return_value = SimpleNamespace(status="member" if member else "left")
    job.random = SimpleNamespace(random=lambda: 0.0, choice=lambda pool: pool[0])
    job.client = SimpleNamespace(_agy=SimpleNamespace(command="agy"), get_user_model=lambda _: "test")
    source, response = quote_fixture()
    if fallback:
        source["chat_id"] = response["source_chat_id"] = -1001
    calls = []

    async def request(cfg, tool, arguments):
        calls.append((tool, arguments))
        if tool == "get_top_authors":
            data = {"authors": [{"sender_user_id": 43, "profile": {"username": "local_user", "is_bot": False}},
                                {"sender_user_id": 42, "profile": {"username": "tester", "is_bot": True}}]}
            start = datetime.fromisoformat(arguments["scope"]["since"])
            end = datetime.fromisoformat(arguments["scope"]["until"])
            assert end - start == timedelta(days=1)
        elif tool == "search_messages" and arguments.get("limit") == 1:
            assert arguments["exclude_chat_id"] == -1001 and "chat_id" not in arguments
            data = {"messages": [source] if arguments["sender_user_id"] == 42 and not fallback else []}
        elif tool in {"search", "search_messages"}:
            assert arguments["sender_user_id"] == 42 and arguments["public_only"] is True
            assert "topic_id" not in arguments
            if "exclude_chat_id" in arguments:
                assert arguments["exclude_chat_id"] == -1001 and "chat_id" not in arguments
                data = {"messages": [] if fallback else [source]}
            else:
                assert fallback and arguments["chat_id"] == -1001
                data = {"messages": [source]}
        else:
            data = {}
        return {"ok": True, "result": {"structuredContent": data}}

    class FakeAgy:
        def __init__(self, **kwargs):
            pass

        async def stream(self, *args, **kwargs):
            assert kwargs["tool_context"]["requester_id"] == "summary"
            yield json.dumps(response)

        async def aclose(self):
            pass

    monkeypatch.setattr(daily_chat, "collector_config", lambda: object())
    monkeypatch.setattr(daily_chat, "_request", request)
    monkeypatch.setattr(daily_chat, "AgyCliClient", FakeAgy)
    text = await job.compose(-1001, None, user_id=None if automatic else 42)
    if member or fallback:
        assert text["reply_parameters"].quote == source["text"]
    else:
        assert text["reply_parameters"] is None
        assert f'<blockquote>{source["text"]}</blockquote>' in text["text"]
    assert [name for name, _ in calls] == ["get_top_authors", *(["search_messages"] * 2 if automatic else []),
                                          "search_messages", *(["search_messages"] if fallback else []),
                                          "get_top_phrases", "search", "get_reply_partners"]
    job.bot.send_message.assert_not_awaited()

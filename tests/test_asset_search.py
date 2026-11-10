import json
from pathlib import Path

import pytest

from app.core import config
from app.services.asset_search import AssetSearch
from app.services.harvester import AssetHarvester


class DictionaryFixture:
    """Fixed external-provider results, not a production synonym dictionary."""
    path = Path(__file__)

    def expand(self, query):
        return {"обнимашки": ["объятия"], "cuddle": ["объятия"],
                "застенчивость": ["румянец"]}.get(query, [])


@pytest.fixture
def index(tmp_path, monkeypatch):
    stickers = tmp_path / "defaults.json"
    emojis = tmp_path / "emojis.json"
    source = tmp_path / "user.json"
    stickers.write_text(json.dumps([
        {"file_id": "hug", "file_unique_id": "unique-hug", "set_name": "Pack", "description": "Прижимается к подруге", "tags": ["объятия"]},
        {"file_id": "sad", "file_unique_id": "unique-sad", "description": "Грустная девушка", "tags": ["печаль"]},
    ]))
    emojis.write_text(json.dumps({"emojis": [{"custom_emoji_id": "123", "emoji": "😳", "description": "Смущённая девушка", "tags": ["румянец"]}]}))
    source.write_text(json.dumps({"stickers": {
        "private": {"file_id": "private", "file_unique_id": "unique-private", "description": "Секретный хохот", "tags": ["смех"], "users": ["1"]},
    }, "custom_emojis": {}, "sticker_packs": {}}))
    monkeypatch.setattr(config, "DEFAULT_STICKERS_FILE", stickers)
    monkeypatch.setattr(config, "DEFAULT_EMOJIS_FILE", emojis)
    return AssetSearch(AssetHarvester(source), dictionary=DictionaryFixture())


def test_fts_synonyms_morphology_type_and_markup(index):
    result = index.search(1, "обнимашки", "sticker")
    assert result["results"][0]["file_id"] == "hug"
    assert result["results"][0]["markup"] == '<tg-sticker file-id="hug"/>'
    assert "объятия" in result["synonyms"]
    assert index.search(1, "cuddle", "sticker")["results"]
    assert index.search(1, "грустными", "sticker")["results"][0]["file_id"] == "sad"
    assert index.search(1, "застенчивость", "emoji")["results"][0]["custom_emoji_id"] == "123"
    assert index.search(1, "😳", "emoji")["results"]
    assert index.search(1, "невстречающеесяслово")["results"] == []
    assert index.search(1, '" OR ) NOT *')["results"] == []


def test_index_reuse_reload_limits_and_user_isolation(index):
    assert index.search(1, "хохот")["results"][0]["file_id"] == "private"
    builds = index.rebuilds
    index.search(1, "хохот")
    assert index.rebuilds == builds
    assert not index.search(2, "хохот")["results"]
    source = index.harvester.storage_file
    data = json.loads(source.read_text())
    data["stickers"]["private"]["users"].append("2")
    source.write_text(json.dumps(data))
    assert index.search(2, "хохот")["results"]
    assert len(index.search(1, "объятия", limit=100)["results"]) <= 10
    with pytest.raises(ValueError):
        index.search(1, "", "all")
    with pytest.raises(ValueError):
        index.search(1, "объятия", "invalid")


def test_count_only_changes_reuse_index_and_observed_filter_is_strict(index):
    index.search(1, "хохот")
    builds = index.rebuilds
    source = index.harvester.storage_file
    data = json.loads(source.read_text())
    data["stickers"]["private"]["count"] = 999
    source.write_text(json.dumps(data))
    index.search(1, "хохот")
    assert index.rebuilds == builds
    assert not index.search(1, "хохот", source="user", preferences=[])["results"]
    result = index.search(1, "хохот", source="user", preferences=[{
        "type": "sticker", "key": "unique-private", "file_id": "private", "file_unique_id": "unique-private",
        "description": "Секретный хохот", "count": 10,
    }])
    assert result["results"][0]["count"] == 10


def test_compact_bootstrap_does_not_embed_personal_catalog(index):
    text = index.harvester.format_emojis_prompt_context(1)
    assert "asset_search" in text
    assert text.count("<tg-emoji ") <= 6
    assert "Секретный" not in text
    assert 'file-id="hug"' not in text
    assert len(text) < 2000


def test_ready_ruwordnet_corpus_is_read_only_and_cached():
    from app.services.thesaurus import DATABASE, Thesaurus

    if not DATABASE.is_file():
        pytest.skip("Run python -m scripts.setup_thesaurus for corpus integration test")
    dictionary = Thesaurus()
    assert "прийтись по душе" in dictionary.expand("нравится")
    hits = dictionary.synonyms.cache_info().hits
    dictionary.expand("нравится")
    assert dictionary.synonyms.cache_info().hits > hits
    with dictionary.engine.connect() as db:
        from sqlalchemy.exc import OperationalError

        with pytest.raises(OperationalError, match="readonly"):
            db.exec_driver_sql("CREATE TABLE must_not_be_created (id INTEGER)")


def test_absent_corpus_has_literal_search_fallback(index, tmp_path):
    from app.services.thesaurus import Thesaurus

    index.dictionary = Thesaurus(tmp_path / "missing.db")
    result = index.search(1, "грустный")
    assert result["results"] and not result["synonyms_available"]
    assert result["synonyms"] == []

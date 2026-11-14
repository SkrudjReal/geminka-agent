"""Scoped, derived SQLite FTS5/BM25 index; no personal-memory DB or disk writes."""

import json
import re
import sqlite3
import threading
from functools import lru_cache

import snowballstemmer

from app.core import config
from app.core.files import load_json
from app.services.harvester import AssetHarvester, asset_harvester, load_default_emojis
from app.services.sticker_policy import allowed
from app.services.thesaurus import thesaurus

_RUSSIAN = snowballstemmer.stemmer("russian")
_ENGLISH = snowballstemmer.stemmer("english")
_STEM_LOCK = threading.Lock()
_STOP = set("и или не без с со на в во по для от к ко из это что как где мне мне бы очень пожалуйста пришли отправь покажи стикер эмодзи коломбина".split())


@lru_cache(maxsize=50000)
def stem(word: str) -> str:
    with _STEM_LOCK:
        return (_RUSSIAN if re.search("[а-я]", word) else _ENGLISH).stemWord(word)


def tokens(text: str) -> list[str]:
    words = re.findall(r"[^\W_]+", text.lower().replace("ё", "е"), re.UNICODE)
    return list(dict.fromkeys(stem(word) for word in words if len(word) > 1 and word not in _STOP))


class AssetSearch:
    def __init__(self, harvester: AssetHarvester = asset_harvester, dictionary=thesaurus):
        self.harvester = harvester
        self.dictionary = dictionary
        self.lock = threading.RLock()
        self.db = None
        self.signature = None
        self.content_signature = None
        self.rebuilds = 0

    def _signature(self, user_id):
        paths = (config.DEFAULT_STICKERS_FILE, config.DEFAULT_EMOJIS_FILE,
                 self.harvester.storage_file)
        return (user_id, tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size)
                               if path.exists() else (str(path), None, None) for path in paths))

    def _refresh(self, user_id, preferences, observed_only):
        signature = (self._signature(user_id), observed_only, tuple(sorted((row.get("type", ""), row.get("key", ""),
                                                           row.get("file_id", ""), row.get("description", ""))
                                                          for row in preferences)))
        if signature == self.signature:
            return
        records = [("emoji", item, "default") for item in load_default_emojis()]
        for item in ([] if observed_only else self.harvester.get_user_custom_emojis(user_id, limit=10000)):
            key = str(item.get("custom_emoji_id", ""))
            if key:
                records.append(("emoji", item, "user"))
        defaults = load_json(config.DEFAULT_STICKERS_FILE, [])
        records.extend(("sticker", item, "default") for item in defaults)
        default_keys = {item.get("file_unique_id") or item.get("file_id") for item in defaults}
        for item in ([] if observed_only else self.harvester.get_user_stickers(user_id)):
            if ((item.get("file_unique_id") or item.get("file_id")) not in default_keys
                    or str(user_id) in item.get("users", [])):
                records.append(("sticker", item, "user"))
        for item in preferences:
            kind = item["type"]
            if kind in {"gif", "emoji", "sticker"}:
                if kind == "sticker":
                    meta = self.harvester.get_sticker_metadata(item["file_id"], item.get("file_unique_id")) or {}
                    item = {**meta, **{key: value for key, value in item.items() if value}}
                elif kind == "emoji":
                    self.harvester._refresh_if_changed()
                    meta = self.harvester.data["custom_emojis"].get(item["custom_emoji_id"], {})
                    item = {**meta, **{key: value for key, value in item.items() if value}}
                records.append((kind, item, "user"))
        # Usage counts change frequently, but the FTS text often stays identical.
        fields = ("file_id", "file_unique_id", "custom_emoji_id", "emoji", "set_name", "description", "tags")
        content_signature = (user_id, tuple(sorted(set(
            json.dumps([kind, source, {key: item[key] for key in fields if key in item}],
                       ensure_ascii=False, sort_keys=True) for kind, item, source in records))))
        if content_signature == self.content_signature:
            self.signature = signature
            return
        db = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            db.execute("CREATE VIRTUAL TABLE assets USING fts5(kind UNINDEXED, payload UNINDEXED, tags, description, terms)")
            seen = set()
            for kind, item, source in records:
                if kind == "sticker" and not allowed(item):
                    continue
                identity = (kind, source, item.get("custom_emoji_id") or item.get("file_unique_id") or item.get("file_id"))
                if identity in seen:
                    continue
                seen.add(identity)
                description = str(item.get("description") or "")
                tags = [str(tag) for tag in item.get("tags", [])]
                payload = {key: item[key] for key in ("file_id", "file_unique_id", "custom_emoji_id", "emoji", "set_name") if item.get(key)}
                payload.update(type=kind, source=source, description=description[:1200], tags=tags[:20])
                if kind == "emoji":
                    payload["markup"] = f'<tg-emoji emoji-id="{item["custom_emoji_id"]}">{item.get("emoji", "✨")}</tg-emoji>'
                else:
                    tag = "tg-gif" if kind == "gif" else "tg-sticker"
                    payload["markup"] = f'<{tag} file-id="{item["file_id"]}"/>'
                db.execute("INSERT INTO assets VALUES(?,?,?,?,?)", (
                    kind, json.dumps(payload, ensure_ascii=False), " ".join(tokens(" ".join(tags))),
                    " ".join(tokens(description)), " ".join(tokens(str(item.get("set_name", "")))),
                ))
            db.commit()
        except BaseException:
            db.close()
            raise
        if self.db is not None:
            self.db.close()
        self.db, self.signature = db, signature
        self.content_signature = content_signature
        self.rebuilds += 1

    def search(self, user_id: int, query: str, kind="all", limit=5, source="all", preferences=None) -> dict:
        if kind not in {"all", "emoji", "sticker", "gif"} or source not in {"all", "default", "user"}:
            raise ValueError("Invalid asset type or source")
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("Запрос должен содержать от 1 до 1000 символов.")
        limit = max(1, min(int(limit), 10))
        with self.lock:
            observed_only = preferences is not None
            preferences = preferences or []
            self._refresh(user_id, preferences, observed_only)
            original = tokens(query)[:12]
            expanded = list(original)
            synonyms = self.dictionary.expand(query)
            expanded.extend(tokens(" ".join(synonyms)))
            expanded = list(dict.fromkeys(expanded))[:64]
            if expanded:
                match = " OR ".join('"' + word + '"' for word in expanded)
                rows = self.db.execute(
                    "SELECT payload,bm25(assets,0,0,6,1,0.5) AS score FROM assets "
                    "WHERE assets MATCH ? AND (?='all' OR kind=?) "
                    "AND (?='all' OR json_extract(payload,'$.source')=?) ORDER BY score LIMIT ?",
                    (match, kind, kind, source, source, 40),
                ).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT payload,0 FROM assets WHERE json_extract(payload,'$.emoji')=? "
                    "AND (?='all' OR kind=?) AND (?='all' OR json_extract(payload,'$.source')=?) LIMIT ?",
                    (query.strip(), kind, kind, source, source, 40),
                ).fetchall()
            counts = {(item["type"], item["key"]): item["count"] for item in preferences}
            results, used = [], set()
            for row in rows:
                item = json.loads(row[0])
                key = item.get("custom_emoji_id") or item.get("file_unique_id") or item.get("file_id")
                if (item["type"], key) in used:
                    continue
                used.add((item["type"], key))
                item.update(count=counts.get((item["type"], key), 0), score=row[1])
                results.append(item)
            results.sort(key=lambda item: item["score"] * (1 + min(item["count"], 100) / 100))
            return {"ok": True, "source": source, "results": results[:limit],
                    "expanded_terms": expanded, "synonyms": synonyms,
                    "synonyms_available": self.dictionary.path.is_file()}


asset_search = AssetSearch()

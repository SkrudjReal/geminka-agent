"""Ready-made RuWordNet synonyms, queried read-only and cached per process."""

import re
import threading
from functools import lru_cache
from pathlib import Path

from app.core import config

DATABASE = config.DATA_DIR / "ruwordnet.db"


class Thesaurus:
    def __init__(self, path: Path = DATABASE):
        self.path = path
        self.lock = threading.RLock()
        self.morph = None
        self.engine = None
        self.synonyms = lru_cache(maxsize=4096)(self._synonyms)

    def _synonyms(self, word: str) -> tuple[str, ...]:
        if not self.path.is_file():
            return ()
        from pymorphy3 import MorphAnalyzer
        from ruwordnet import RuWordNet
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        with self.lock:
            if self.engine is None:
                self.engine = create_engine(
                    "sqlite:///" + self.path.resolve().as_uri() + "?mode=ro&uri=true",
                    connect_args={"check_same_thread": False},
                )
            if self.morph is None:
                self.morph = MorphAnalyzer()
            lemma = self.morph.parse(word)[0].normal_form if re.search("[а-яё]", word) else word
            with Session(self.engine) as session:
                # Supplying a session bypasses RuWordNet's default create_all().
                wn = RuWordNet(session)
                synsets = wn.get_synsets(lemma) if re.search("[а-яё]", lemma) else wn.get_en_synsets(lemma)
                found = []
                for synset in synsets[:3]:
                    found.extend(sense.name.lower().replace("_", " ") for sense in synset.senses)
                return tuple(sorted(set(found) - {word, lemma}))[:16]

    def expand(self, query: str) -> list[str]:
        if not self.path.is_file():
            return []
        words = re.findall(r"[^\W_]+", query.lower(), re.UNICODE)[:12]
        return list(dict.fromkeys(synonym for word in words for synonym in self.synonyms(word)))[:48]


thesaurus = Thesaurus()

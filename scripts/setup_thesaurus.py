"""Download the author's prebuilt RuWordNet 2021 corpus once, never on a search."""

import os
import shutil
import sqlite3
import tempfile
import urllib.request

from app.services.thesaurus import DATABASE

URL = "https://github.com/avidale/python-ruwordnet/releases/download/0.0.4/ruwordnet-2021.db"


def main():
    if DATABASE.is_file():
        print("RuWordNet corpus already present:", DATABASE)
        return
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=DATABASE.parent, prefix=".ruwordnet-") as folder:
        from pathlib import Path

        temporary = Path(folder) / "corpus.db"
        with urllib.request.urlopen(URL, timeout=60) as source, temporary.open("wb") as target:
            shutil.copyfileobj(source, target)
        with sqlite3.connect(temporary.as_uri() + "?mode=ro", uri=True) as db:
            assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            count = db.execute("SELECT count(*) FROM sense").fetchone()[0]
            if count < 100000:
                raise RuntimeError("Unexpected RuWordNet corpus")
        temporary.chmod(0o600)
        os.replace(temporary, DATABASE)
    print("RuWordNet corpus installed:", count, "senses;", DATABASE.stat().st_size, "bytes")


if __name__ == "__main__":
    main()

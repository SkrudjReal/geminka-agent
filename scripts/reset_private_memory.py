"""Explicit one-time local wipe. Stop Geminka before running with --confirm-delete."""

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def reset(root: Path):
    root = root.resolve(strict=True)
    if not (root / "pyproject.toml").is_file() or not (root / "app/core/state.py").is_file():
        raise ValueError("Expected a Geminka project root")
    paths = [root / "memory"]
    runtime = root / "data/agy_sandbox"
    paths += [runtime / name for name in (
        "conversations", "brain", "annotations", "implicit", "cache", "log", "cli.log", "crashes",
        "conversation_summaries.db", "conversation_summaries.db-wal", "conversation_summaries.db-shm",
        "jetbox_summaries_proto.pb",
    )]
    paths += [root / "data" / name for name in ("memory.db", "memory.db-wal", "memory.db-shm")]
    checked = paths + [root / "data" / name for name in (
        "state.db", "emotional_state.json", "adaptive_profiles.json", "sessions.json", "user_assets.json",
    )]
    # Validate every target before deleting anything; never follow runtime symlinks.
    for path in checked:
        if (path.is_symlink() and path != runtime / "cli.log") or not path.resolve().is_relative_to(root):
            raise ValueError(f"Refusing an external or symlinked target: {path}")
    for path in paths:
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    state = root / "data/state.db"
    if state.exists():
        with sqlite3.connect(state) as db:
            db.execute("PRAGMA secure_delete=ON")
            for table in ("conversation_messages", "user_memories"):
                db.execute(f"DELETE FROM {table}")
            db.execute("UPDATE user_preferences SET conversation_id=NULL")
        with sqlite3.connect(state) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("VACUUM")
    for name in ("emotional_state.json", "adaptive_profiles.json", "sessions.json"):
        path = root / "data" / name
        if path.exists():
            path.write_text("{}\n", encoding="utf-8")
    assets = root / "data/user_assets.json"
    if assets.exists():
        data = json.loads(assets.read_text())
        data["user_preferences"] = {}
        data["recent_sent_stickers"] = {}
        assets.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Deleted local memories, conversation history, portraits and per-user adaptation. Auth/settings/catalogs retained.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-delete", action="store_true", required=True)
    parser.parse_args()
    reset(ROOT)

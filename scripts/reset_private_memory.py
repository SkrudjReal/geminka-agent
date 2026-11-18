"""Explicit one-time local wipe. Stop Geminka before running with --confirm-delete."""

import argparse
import shutil
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def reset(root: Path, *, backup: Path | None = None):
    root = root.resolve(strict=True)
    if not (root / "pyproject.toml").is_file() or not (root / "app/core/state.py").is_file():
        raise ValueError("Expected a Geminka project root")
    paths = [root / "memory"]
    runtimes = [root / 'data/agy_sandbox', *sorted((root / 'data/agy_groups').glob('*'))]
    paths += [runtime / name for runtime in runtimes for name in (
        "conversations", "brain", "annotations", "implicit", "cache", "log", "cli.log", "crashes",
        "conversation_summaries.db", "conversation_summaries.db-wal", "conversation_summaries.db-shm",
        "jetbox_summaries_proto.pb", "persistent_terminals", "presence",
    )]
    paths += [root / 'data' / name for name in ('chat_observer.db', 'chat_observer.db-wal', 'chat_observer.db-shm')]
    paths += list((root / 'data').glob('emotional_state.before-*.json'))
    databases = [root / 'data/state.db', root / 'data/memory.db',
                 *sorted((root / 'data/group_memory').glob('*/memory.db'))]
    checked = paths + [root / "data" / name for name in (
        "emotional_state.json", "emotional_state_relationship.json", "adaptive_profiles.json", "sessions.json",
    )] + databases + runtimes
    # Validate every target before deleting anything; never follow runtime symlinks.
    for path in checked:
        if (path.is_symlink() and path.name != 'cli.log') or not path.resolve().is_relative_to(root):
            raise ValueError(f"Refusing an external or symlinked target: {path}")
    backup = backup or Path(tempfile.mkdtemp(prefix='geminka-memory-reset-'))
    backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup.chmod(0o700)
    if backup.resolve().is_relative_to(root):
        raise ValueError('Backup must be outside the project')
    if any(backup.iterdir()):
        raise ValueError('Backup directory must be empty')
    for path in checked:
        if path in paths or path in runtimes or not path.is_file():
            continue
        target = backup / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path in databases:
            with sqlite3.connect(path) as source, sqlite3.connect(target) as dest:
                source.backup(dest)
        else:
            shutil.copy2(path, target)
    for path in paths:
        if path.exists() or path.is_symlink():
            target = backup / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
    for path in databases:
        if not path.exists():
            continue
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA secure_delete=ON")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('conversation_messages', 'user_memories', 'records', 'summary_state',
                          'style_events', 'style_deliveries'):
                if table in tables:
                    db.execute(f'DELETE FROM {table}')
            if 'user_preferences' in tables:
                db.execute("UPDATE user_preferences SET conversation_id=NULL")
            if 'style_usage' in tables:
                db.execute("DELETE FROM style_usage WHERE kind NOT IN ('emoji','sticker','gif')")
            if 'records_fts' in tables:
                db.execute("INSERT INTO records_fts(records_fts) VALUES('rebuild')")
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("VACUUM")
    for name in ("emotional_state.json", "emotional_state_relationship.json", "adaptive_profiles.json", "sessions.json"):
        path = root / "data" / name
        if path.exists():
            path.write_text("{}\n", encoding="utf-8")
    print(f'Reset local memory/history/emotions. Media preferences, auth and settings retained. Backup: {backup}')
    return backup


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-delete", action="store_true", required=True)
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    reset(ROOT, backup=args.backup)

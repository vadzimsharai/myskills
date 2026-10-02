import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SOURCE_AI = "ai"
SOURCE_CLIP = "clip"
SOURCE_MANUAL = "manual"

DEFAULT_ACTUAL_MINUTES = 60
MAX_CLIP_BYTES = 256 * 1024
SQLITE_BUSY_TIMEOUT_SECONDS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,
    text TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    created_at REAL NOT NULL,
    batch_at REAL NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    paste_count INTEGER NOT NULL DEFAULT 0,
    last_pasted_at REAL
);
CREATE TABLE IF NOT EXISTS seen_buffers (
    name TEXT NOT NULL,
    created INTEGER NOT NULL,
    PRIMARY KEY (name, created)
);
"""

# A re-added text moves to the top only when its new batch is newer; a stale tmux buffer must not bump it.
UPSERT_CLIP = """
INSERT INTO clips (hash, text, label, source, created_at, batch_at, ordinal)
VALUES (:hash, :text, :label, :source, :at, :at, :ordinal)
ON CONFLICT (hash) DO UPDATE SET
    ordinal = CASE WHEN excluded.batch_at >= clips.batch_at THEN excluded.ordinal ELSE clips.ordinal END,
    batch_at = MAX(clips.batch_at, excluded.batch_at),
    source = CASE WHEN excluded.batch_at >= clips.batch_at THEN excluded.source ELSE clips.source END,
    label = CASE WHEN excluded.label <> '' THEN excluded.label ELSE clips.label END
"""


@dataclass(frozen=True)
class Clip:
    id: int
    text: str
    label: str
    source: str
    created_at: float
    batch_at: float
    ordinal: int
    paste_count: int
    last_pasted_at: float | None


def default_db_path() -> Path:
    if os.environ.get("TCP_DB"):
        return Path(os.environ["TCP_DB"])
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(data_home) / "tmuxcopypast" / "clips.db"


def actual_minutes() -> int:
    return int(os.environ.get("TCP_ACTUAL_MINUTES", DEFAULT_ACTUAL_MINUTES))


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


class Store:
    def __init__(self, path: Path | None = None, now=time.time):
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.now = now
        self.db = sqlite3.connect(self.path, timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def add_batch(self, items: list[tuple[str, str]], source: str, at: float | None = None) -> list[int]:
        batch_at = self.now() if at is None else at
        ids = []
        with self.db:
            for ordinal, (text, label) in enumerate(items):
                if not text.strip() or len(text.encode("utf-8", "surrogateescape")) > MAX_CLIP_BYTES:
                    continue
                digest = text_hash(text)
                self.db.execute(UPSERT_CLIP, {
                    "hash": digest, "text": text, "label": label, "source": source,
                    "at": batch_at, "ordinal": ordinal,
                })
                ids.append(self.db.execute("SELECT id FROM clips WHERE hash = ?", (digest,)).fetchone()["id"])
        return ids

    def has_seen_buffer(self, name: str, created: int) -> bool:
        row = self.db.execute("SELECT 1 FROM seen_buffers WHERE name = ? AND created = ?", (name, created)).fetchone()
        return row is not None

    def import_buffer(self, name: str, created: int, text: str) -> bool:
        with self.db:
            inserted = self.db.execute("INSERT OR IGNORE INTO seen_buffers VALUES (?, ?)", (name, created)).rowcount
        if not inserted:
            return False
        self.add_batch([(text, "")], SOURCE_CLIP, at=float(created))
        return True

    def all(self) -> list[Clip]:
        rows = self.db.execute(
            "SELECT id, text, label, source, created_at, batch_at, ordinal, paste_count, last_pasted_at"
            " FROM clips ORDER BY batch_at DESC, ordinal ASC, id DESC"
        ).fetchall()
        return [Clip(**dict(row)) for row in rows]

    def split(self, clips: list[Clip], minutes: int | None = None) -> tuple[list[Clip], list[Clip]]:
        cutoff = self.now() - (actual_minutes() if minutes is None else minutes) * 60
        actual = [clip for clip in clips if clip.batch_at >= cutoff]
        history = [clip for clip in clips if clip.batch_at < cutoff]
        return actual, history

    def get(self, clip_id: int) -> Clip | None:
        row = self.db.execute(
            "SELECT id, text, label, source, created_at, batch_at, ordinal, paste_count, last_pasted_at"
            " FROM clips WHERE id = ?", (clip_id,)
        ).fetchone()
        return Clip(**dict(row)) if row else None

    def mark_pasted(self, clip_id: int) -> None:
        with self.db:
            self.db.execute(
                "UPDATE clips SET paste_count = paste_count + 1, last_pasted_at = ? WHERE id = ?",
                (self.now(), clip_id),
            )

    def delete(self, clip_id: int) -> None:
        with self.db:
            self.db.execute("DELETE FROM clips WHERE id = ?", (clip_id,))

    # seen_buffers is kept so cleared clips are not re-imported from tmux buffers that still exist.
    def delete_all(self) -> int:
        with self.db:
            return self.db.execute("DELETE FROM clips").rowcount

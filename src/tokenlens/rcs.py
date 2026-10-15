"""RCS: Reversible Content Store for reversible compression.

When compression runs, the original content is stored here keyed by a content
hash, and the model is given a ``tokenlens_retrieve`` tool. If compression ever
drops something the model needs, it fetches the original instead of guessing —
turning a potential wrong answer into one extra lookup.

Storage is **bounded** so it can never fill the disk:

- **TTL**: entries older than ``ttl_seconds`` are purged. Originals are only
  useful during the active task, so short retention is safe.
- **LRU size cap**: when total bytes exceed ``max_bytes`` or the entry count
  exceeds ``max_entries``, the least-recently-used entries are evicted.
- **Per-entry cap**: blobs larger than ``max_entry_bytes`` are not stored.

Backed by SQLite (standard library, single file, no extra dependency).
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path


class RCSStore:
    def __init__(
        self,
        directory: str,
        enabled: bool = False,
        ttl_seconds: int = 86_400,
        max_bytes: int = 200 * 1024 * 1024,
        max_entries: int = 1000,
        max_entry_bytes: int = 1024 * 1024,
    ):
        self.enabled = enabled
        self.ttl_seconds = ttl_seconds
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.max_entry_bytes = max_entry_bytes
        self.dir = Path(directory)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

        if self.enabled:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.enabled = False
                return
            # isolation_level=None -> autocommit; simplest for a KV cache.
            self._conn = sqlite3.connect(
                str(self.dir / "rcs.db"), check_same_thread=False, isolation_level=None
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    key TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    last_access REAL NOT NULL
                )
                """
            )

    def put(self, content: str) -> str | None:
        """Store original content and return its retrieval key.

        Returns None if the store is disabled or the content exceeds the
        per-entry size cap (in which case no retrieval is offered).
        """
        if not self.enabled or self._conn is None:
            return None
        data = content.encode("utf-8")
        size = len(data)
        if size > self.max_entry_bytes:
            return None

        key = hashlib.sha256(data).hexdigest()[:16]
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            # Content-hash keys give free deduplication of identical content.
            self._conn.execute(
                """
                INSERT INTO entries (key, content, size_bytes, created_at, last_access)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET last_access = excluded.last_access
                """,
                (key, content, size, now, now),
            )
            self._evict()
        return key

    def get(self, key: str) -> str | None:
        """Return the original content for a key, or None if missing/expired."""
        if not self.enabled or self._conn is None:
            return None
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            row = self._conn.execute(
                "SELECT content FROM entries WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE entries SET last_access = ? WHERE key = ?", (now, key)
            )
            return row[0]

    def stats(self) -> dict:
        if not self.enabled or self._conn is None:
            return {"enabled": False, "entries": 0, "bytes": 0}
        with self._lock:
            self._purge_expired(time.time())
            count, total = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM entries"
            ).fetchone()
        return {
            "enabled": True,
            "entries": count,
            "bytes": total,
            "max_bytes": self.max_bytes,
            "max_entries": self.max_entries,
            "ttl_seconds": self.ttl_seconds,
        }

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ---- internal (callers hold the lock) ---------------------------------

    def _purge_expired(self, now: float) -> None:
        self._conn.execute(
            "DELETE FROM entries WHERE created_at < ?", (now - self.ttl_seconds,)
        )

    def _evict(self) -> None:
        """Evict least-recently-used entries until within size and count caps."""
        count, total = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM entries"
        ).fetchone()
        while count > self.max_entries or total > self.max_bytes:
            row = self._conn.execute(
                "SELECT key, size_bytes FROM entries ORDER BY last_access ASC LIMIT 1"
            ).fetchone()
            if row is None:
                break
            self._conn.execute("DELETE FROM entries WHERE key = ?", (row[0],))
            count -= 1
            total -= row[1]

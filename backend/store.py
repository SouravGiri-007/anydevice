"""SQLite metadata store for shares and their items.

Metadata lives separate from blobs on purpose: blobs go through BlobStore, so
a future Firestore backend replaces only this module's persistence while the
API layer and blob keys stay identical to the PRD's data model.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shares (
    code       TEXT PRIMARY KEY,
    ttl_key    TEXT NOT NULL,
    ttl_seconds INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    burn       INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | viewed
    enc        INTEGER NOT NULL DEFAULT 0,       -- 1 = client-side encrypted content
    key        TEXT                               -- server at-rest AES key (base64url)
);
CREATE TABLE IF NOT EXISTS items (
    id         TEXT PRIMARY KEY,
    code       TEXT NOT NULL REFERENCES shares(code) ON DELETE CASCADE,
    pos        INTEGER NOT NULL,
    type       TEXT NOT NULL,            -- 'file' | 'text'
    name       TEXT NOT NULL DEFAULT '',
    mime       TEXT NOT NULL DEFAULT 'application/octet-stream',
    size       INTEGER NOT NULL DEFAULT 0,
    content    TEXT,
    blob_key   TEXT,
    downloaded INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_code ON items(code);
CREATE INDEX IF NOT EXISTS idx_shares_expires ON shares(expires_at);
"""


def _row_to_share(code: str, row: sqlite3.Row) -> dict[str, Any]:
    return {
        "code": code,
        "ttl_key": row["ttl_key"],
        "ttl_seconds": row["ttl_seconds"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "burn": bool(row["burn"]),
        "status": row["status"],
        "enc": bool(row["enc"]),
        "key": row["key"],
    }


def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "type": row["type"],
        "name": row["name"],
        "mime": row["mime"],
        "size": row["size"],
        "content": row["content"],
        "blob_key": row["blob_key"],
        "downloaded": bool(row["downloaded"]),
    }


class SQLiteStore:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()
        # Same API shape a Firestore-backed store would expose.
        self._lock = __import__("threading").Lock()

    def _migrate(self) -> None:
        """Tiny forward migrations for SQLite DBs created before a column existed."""
        cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(shares)")}
        if "status" not in cols:
            self._conn.execute("ALTER TABLE shares ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
        if "enc" not in cols:
            self._conn.execute("ALTER TABLE shares ADD COLUMN enc INTEGER NOT NULL DEFAULT 0")
        if "key" not in cols:
            self._conn.execute("ALTER TABLE shares ADD COLUMN key TEXT")

    def heartbeat(self) -> None:
        """Prove the DB is reachable (used by /api/health)."""
        with self._lock:
            self._conn.execute("SELECT 1")

    # -- writes -----------------------------------------------------------

    def create_share(
        self,
        code: str,
        ttl_key: str,
        ttl_seconds: int,
        burn: bool,
        items: list[dict[str, Any]],
        enc: bool = False,
        key: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self._lock:
            self._conn.execute(
                "INSERT INTO shares (code, ttl_key, ttl_seconds, created_at, expires_at, burn, enc, key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (code, ttl_key, ttl_seconds, now, now + ttl_seconds, int(burn), int(enc), key),
            )
            self._insert_items(code, items)
            self._conn.commit()
        return self.get(code)

    def append_items(self, code: str, items: list[dict[str, Any]]) -> bool:
        with self._lock:
            cur = self._conn.execute("SELECT 1 FROM shares WHERE code = ?", (code,))
            if cur.fetchone() is None:
                return False
            next_pos = self._conn.execute(
                "SELECT COALESCE(MAX(pos), -1) + 1 FROM items WHERE code = ?", (code,)
            ).fetchone()[0]
            for item in items:
                item["pos"] = next_pos
                next_pos += 1
            self._insert_items(code, items)
            self._conn.commit()
        return True

    def _insert_items(self, code: str, items: list[dict[str, Any]]) -> None:
        for i, item in enumerate(items):
            item = dict(item)
            pos = item.pop("pos", i)
            self._conn.execute(
                "INSERT INTO items (id, code, pos, type, name, mime, size, content, blob_key, downloaded) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item["id"],
                    code,
                    pos,
                    item["type"],
                    item.get("name", ""),
                    item.get("mime", "application/octet-stream"),
                    item.get("size", 0),
                    item.get("content"),
                    item.get("blob_key"),
                    0,
                ),
            )

    # -- reads ------------------------------------------------------------

    def get(self, code: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM shares WHERE code = ?", (code,)).fetchone()
        if row is None:
            return None
        share = _row_to_share(code, row)
        item_rows = self._conn.execute(
            "SELECT * FROM items WHERE code = ? ORDER BY pos ASC", (code,)
        ).fetchall()
        share["items"] = [_row_to_item(r) for r in item_rows]
        share["bytes_total"] = sum(i["size"] for i in share["items"])
        return share

    def code_exists(self, code: str) -> bool:
        return (
            self._conn.execute("SELECT 1 FROM shares WHERE code = ?", (code,)).fetchone()
            is not None
        )

    def item(self, code: str, item_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM items WHERE code = ? AND id = ?", (code, item_id)
        ).fetchone()
        return _row_to_item(row) if row else None

    # -- pickup status ------------------------------------------------------

    def set_viewed(self, code: str) -> bool:
        """Flip pending → viewed. Returns True if the state actually changed."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE shares SET status = 'viewed' WHERE code = ? AND status = 'pending'",
                (code,),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # -- download / burn bookkeeping --------------------------------------

    def mark_item_downloaded(self, code: str, item_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE items SET downloaded = 1 WHERE code = ? AND id = ?",
                (code, item_id),
            )
            self._conn.commit()

    def all_items_downloaded(self, code: str) -> bool:
        row = self._conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN downloaded = 1 THEN 1 ELSE 0 END) AS done "
            "FROM items WHERE code = ?",
            (code,),
        ).fetchone()
        total = row["total"] or 0
        done = row["done"] or 0
        return total > 0 and total == done

    def mark_all_downloaded(self, code: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE items SET downloaded = 1 WHERE code = ?", (code,)
            )
            self._conn.commit()

    # -- expiry / deletion --------------------------------------------------

    def expired_codes(self, now: float | None = None) -> list[str]:
        now = time.time() if now is None else now
        rows = self._conn.execute(
            "SELECT code FROM shares WHERE expires_at <= ?", (now,)
        ).fetchall()
        return [r["code"] for r in rows]

    def delete(self, code: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM shares WHERE code = ?", (code,))
            self._conn.commit()

    def wipe(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM items")
            self._conn.execute("DELETE FROM shares")
            self._conn.commit()

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM shares").fetchone()[0]

    # -- operator stats --------------------------------------------------------

    def stats(self, now: float | None = None) -> dict[str, Any]:
        """Aggregate, PII-free operational stats.

        Pure counts/averages over the whole store — no codes, names, filenames,
        IPs, or content ever leave this method. Matches the no-tracking stance.
        Daily breakdown is calendar-based in UTC: ``shares_today`` /
        ``shares_yesterday`` and ``last_7_days`` (oldest first, zero-filled).
        """
        now = time.time() if now is None else now
        day_ago = now - 86400
        day_start = int(now) - (int(now) % 86400)
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM shares").fetchone()[0]
            recent = self._conn.execute(
                "SELECT COUNT(*) FROM shares WHERE created_at >= ?", (day_ago,)
            ).fetchone()[0]
            active = self._conn.execute(
                "SELECT COUNT(*) FROM shares WHERE expires_at > ?", (now,)
            ).fetchone()[0]
            burn = self._conn.execute(
                "SELECT COUNT(*) FROM shares WHERE burn = 1"
            ).fetchone()[0]
            avg_row = self._conn.execute(
                "SELECT AVG(sz) FROM (SELECT SUM(size) AS sz FROM items GROUP BY code)"
            ).fetchone()
            daily = self._conn.execute(
                "SELECT date(created_at, 'unixepoch') AS d, COUNT(*) AS n "
                "FROM shares WHERE created_at >= ? GROUP BY d",
                (day_start - 6 * 86400,),
            ).fetchall()
        avg = avg_row[0] if avg_row and avg_row[0] is not None else 0
        today = datetime.fromtimestamp(day_start, tz=timezone.utc).date()
        counts = {r["d"]: int(r["n"]) for r in daily}
        last7 = []
        for i in range(6, -1, -1):
            key = (today - timedelta(days=i)).isoformat()
            last7.append({"date": key, "count": counts.get(key, 0)})
        return {
            "shares_total": total,
            "shares_24h": recent,
            "shares_active": active,
            "burn_pct": round(100.0 * burn / total, 1) if total else 0.0,
            "avg_share_bytes": round(float(avg), 1),
            "shares_today": last7[-1]["count"],
            "shares_yesterday": last7[-2]["count"],
            "last_7_days": last7,
        }

    def admin_shares(self, now: float | None = None) -> list[dict[str, Any]]:
        """Per-share detail for the private admin dashboard.

        Intentionally admin-only: exposes transfer codes, item names, sizes and
        download counts. The system never stores sender/receiver identities, IPs
        or device info, so none of that is ever returned. Burned shares that were
        purged are already deleted and therefore absent here.
        """
        now = time.time() if now is None else now
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.code AS code, s.created_at AS created_at, "
                "s.expires_at AS expires_at, s.burn AS burn, s.status AS status, "
                "i.name AS name, i.type AS type, i.size AS size, "
                "i.downloaded AS downloaded "
                "FROM shares s LEFT JOIN items i ON i.code = s.code "
                "ORDER BY s.created_at DESC"
            ).fetchall()
        by_code: dict[str, dict[str, Any]] = {}
        for r in rows:
            share = by_code.setdefault(
                r["code"],
                {
                    "code": r["code"],
                    "created_at": r["created_at"],
                    "expires_at": r["expires_at"],
                    "burn": bool(r["burn"]),
                    "status": r["status"],
                    "bytes_total": 0,
                    "downloads": 0,
                    "items": [],
                },
            )
            if r["name"] is not None:
                share["items"].append(
                    {
                        "name": r["name"],
                        "type": r["type"],
                        "size": r["size"],
                        "downloaded": bool(r["downloaded"]),
                    }
                )
                share["bytes_total"] += r["size"]
                if r["downloaded"]:
                    share["downloads"] += 1
        for share in by_code.values():
            share["active"] = share["expires_at"] > now
            share["expires_in"] = max(0.0, share["expires_at"] - now)
        return list(by_code.values())

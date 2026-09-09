"""Supabase (Postgres) metadata store for shares and their items.

Implements the same surface as ``SQLiteStore`` so the API layer and blob keys
stay identical; only persistence moves from SQLite to the Supabase Postgres
database (project DSN). Row-level security is left to Supabase, but the backend
writes with the database user, which bypasses RLS for the service role — the
flask app is the only client that touches these tables.
"""
from __future__ import annotations

import time
from typing import Any

import psycopg
from psycopg.rows import dict_row

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shares (
    code       TEXT PRIMARY KEY,
    ttl_key    TEXT NOT NULL,
    ttl_seconds BIGINT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    burn       BOOLEAN NOT NULL DEFAULT FALSE,
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | viewed
    enc        BOOLEAN NOT NULL DEFAULT FALSE,   -- 1 = client-side encrypted content
    key        TEXT                               -- server at-rest AES key (base64url)
);
CREATE TABLE IF NOT EXISTS items (
    id         TEXT PRIMARY KEY,
    code       TEXT NOT NULL REFERENCES shares(code) ON DELETE CASCADE,
    pos        BIGINT NOT NULL,
    type       TEXT NOT NULL,            -- 'file' | 'text'
    name       TEXT NOT NULL DEFAULT '',
    mime       TEXT NOT NULL DEFAULT 'application/octet-stream',
    size       BIGINT NOT NULL DEFAULT 0,
    content    TEXT,
    blob_key   TEXT,
    downloaded BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_items_code ON items(code);
CREATE INDEX IF NOT EXISTS idx_shares_expires ON shares(expires_at);
"""


class SupabaseStore:
    """Postgres-backed metadata store. One short-lived connection per call:
    appetizer-class traffic, and it sidesteps psycopg thread-safety entirely.
    """

    def __init__(self, dsn: str):
        self._dsn = dsn
        with self._conn() as conn:
            conn.execute(_SCHEMA)

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self._dsn, row_factory=dict_row, autocommit=False)

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
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO shares (code, ttl_key, ttl_seconds, created_at, expires_at, burn, enc, key) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (code, ttl_key, ttl_seconds, now, now + ttl_seconds, bool(burn), bool(enc), key),
            )
            self._insert_items(conn, code, items)
        return self.get(code)

    def append_items(self, code: str, items: list[dict[str, Any]]) -> bool:
        with self._conn() as conn:
            exists = conn.execute(
                "SELECT 1 AS ok FROM shares WHERE code = %s", (code,)
            ).fetchone()
            if exists is None:
                return False
            next_pos = conn.execute(
                "SELECT COALESCE(MAX(pos), -1) + 1 AS p FROM items WHERE code = %s", (code,)
            ).fetchone()["p"]
            for item in items:
                item["pos"] = next_pos
                next_pos += 1
            self._insert_items(conn, code, items)
        return True

    @staticmethod
    def _insert_items(conn: psycopg.Connection, code: str, items: list[dict[str, Any]]) -> None:
        for i, item in enumerate(items):
            item = dict(item)
            pos = item.pop("pos", i)
            conn.execute(
                "INSERT INTO items (id, code, pos, type, name, mime, size, content, blob_key, downloaded) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                    False,
                ),
            )

    # -- reads ------------------------------------------------------------

    def get(self, code: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM shares WHERE code = %s", (code,)
            ).fetchone()
            if row is None:
                return None
            share = {
                "code": code,
                "ttl_key": row["ttl_key"],
                "ttl_seconds": row["ttl_seconds"],
                "created_at": float(row["created_at"]),
                "expires_at": float(row["expires_at"]),
                "burn": bool(row["burn"]),
                "status": row["status"],
                "enc": bool(row["enc"]),
                "key": row["key"],
            }
            item_rows = conn.execute(
                "SELECT * FROM items WHERE code = %s ORDER BY pos ASC", (code,)
            ).fetchall()
            share["items"] = [self._row_to_item(r) for r in item_rows]
            share["bytes_total"] = sum(i["size"] for i in share["items"])
            return share

    @staticmethod
    def _row_to_item(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "type": row["type"],
            "name": row["name"],
            "mime": row["mime"],
            "size": int(row["size"]),
            "content": row["content"],
            "blob_key": row["blob_key"],
            "downloaded": bool(row["downloaded"]),
        }

    def code_exists(self, code: str) -> bool:
        with self._conn() as conn:
            return (
                conn.execute("SELECT 1 FROM shares WHERE code = %s", (code,)).fetchone()
                is not None
            )

    def item(self, code: str, item_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM items WHERE code = %s AND id = %s", (code, item_id)
            ).fetchone()
            return self._row_to_item(row) if row else None

    # -- pickup status ------------------------------------------------------

    def set_viewed(self, code: str) -> bool:
        """Flip pending → viewed. Returns True if the state actually changed."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE shares SET status = 'viewed' WHERE code = %s AND status = 'pending'",
                (code,),
            )
            return cur.rowcount > 0

    # -- download / burn bookkeeping --------------------------------------

    def mark_item_downloaded(self, code: str, item_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE items SET downloaded = TRUE WHERE code = %s AND id = %s",
                (code, item_id),
            )

    def all_items_downloaded(self, code: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, "
                "COUNT(*) FILTER (WHERE downloaded) AS done "
                "FROM items WHERE code = %s",
                (code,),
            ).fetchone()
            total = row["total"] or 0
            done = row["done"] or 0
            return total > 0 and total == done

    def mark_all_downloaded(self, code: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE items SET downloaded = TRUE WHERE code = %s", (code,))

    # -- expiry / deletion --------------------------------------------------

    def expired_codes(self, now: float | None = None) -> list[str]:
        now = time.time() if now is None else now
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT code FROM shares WHERE expires_at <= %s", (now,)
            ).fetchall()
            return [r["code"] for r in rows]

    def delete(self, code: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM shares WHERE code = %s", (code,))

    def wipe(self) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM items")
            conn.execute("DELETE FROM shares")

    def count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM shares").fetchone()["n"]

    def heartbeat(self) -> None:
        """Tiny read that keeps the Supabase free project awake."""
        with self._conn() as conn:
            conn.execute("SELECT 1")

    # -- operator stats --------------------------------------------------------

    def stats(self, now: float | None = None) -> dict[str, Any]:
        """Aggregate, PII-free operational stats.

        Pure counts/averages over the whole store — no codes, names, filenames,
        IPs, or content ever leave this method. Matches the no-tracking stance.
        """
        now = time.time() if now is None else now
        day_ago = now - 86400
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM shares").fetchone()["n"]
            recent = conn.execute(
                "SELECT COUNT(*) AS n FROM shares WHERE created_at >= %s", (day_ago,)
            ).fetchone()["n"]
            active = conn.execute(
                "SELECT COUNT(*) AS n FROM shares WHERE expires_at > %s", (now,)
            ).fetchone()["n"]
            burn = conn.execute(
                "SELECT COUNT(*) AS n FROM shares WHERE burn = TRUE"
            ).fetchone()["n"]
            avg_row = conn.execute(
                "SELECT AVG(sz) AS a FROM (SELECT SUM(size) AS sz FROM items GROUP BY code) t"
            ).fetchone()
        avg = avg_row["a"] if avg_row and avg_row["a"] is not None else 0
        return {
            "shares_total": total,
            "shares_24h": recent,
            "shares_active": active,
            "burn_pct": round(100.0 * burn / total, 1) if total else 0.0,
            "avg_share_bytes": round(float(avg), 1),
        }
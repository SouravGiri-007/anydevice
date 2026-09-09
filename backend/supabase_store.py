"""Supabase (Postgres) metadata store for shares and their items.

Implements the same surface as ``SQLiteStore`` so the API layer and blob keys
stay identical; only persistence moves from SQLite to the Supabase Postgres
database (project DSN). Row-level security is left to Supabase, but the backend
writes with the database user, which bypasses RLS for the service role — the
flask app is the only client that touches these tables.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
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
    key        TEXT,                              -- server at-rest AES key (base64url)
    creator_ip TEXT,                              -- operator history: creator's IP
    picked_at  DOUBLE PRECISION,                  -- first receiver pickup (view) time
    picked_ip  TEXT,                              -- receiver's IP at first pickup
    sender_token TEXT                             -- secret only the creator's device knows (scrap)
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
CREATE TABLE IF NOT EXISTS download_log (
    id        TEXT PRIMARY KEY,
    code      TEXT NOT NULL REFERENCES shares(code) ON DELETE CASCADE,
    item_id   TEXT,
    item_name TEXT NOT NULL,
    ip        TEXT,
    at        DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dlog_code ON download_log(code);
CREATE TABLE IF NOT EXISTS share_history (
    code         TEXT PRIMARY KEY,
    created_at   DOUBLE PRECISION NOT NULL,
    expires_at   DOUBLE PRECISION NOT NULL,
    ended_at     DOUBLE PRECISION NOT NULL,
    ended_reason TEXT NOT NULL,            -- 'expired' | 'burned' | 'scraped'
    burn         BOOLEAN NOT NULL DEFAULT FALSE,
    downloads    BIGINT NOT NULL DEFAULT 0,
    bytes_total  BIGINT NOT NULL DEFAULT 0,
    items        TEXT NOT NULL,            -- JSON [{name,type,size}] — metadata only
    creator_ip   TEXT,
    picked_at    DOUBLE PRECISION,
    picked_ip    TEXT,
    download_log TEXT NOT NULL DEFAULT '[]'  -- JSON [{item_name,ip,at}] — receiver audit
);
CREATE INDEX IF NOT EXISTS idx_history_ended ON share_history(ended_at);
"""


class SupabaseStore:
    """Postgres-backed metadata store. One short-lived connection per call:
    appetizer-class traffic, and it sidesteps psycopg thread-safety entirely.
    """

    def __init__(self, dsn: str):
        self._dsn = dsn
        with self._conn() as conn:
            conn.execute(_SCHEMA)
            # Forward-migrate stores created before the receiver-tracking feature.
            conn.execute("ALTER TABLE shares ADD COLUMN IF NOT EXISTS creator_ip TEXT")
            conn.execute("ALTER TABLE shares ADD COLUMN IF NOT EXISTS picked_at DOUBLE PRECISION")
            conn.execute("ALTER TABLE shares ADD COLUMN IF NOT EXISTS picked_ip TEXT")
            conn.execute("ALTER TABLE shares ADD COLUMN IF NOT EXISTS sender_token TEXT")
            conn.execute("ALTER TABLE share_history ADD COLUMN IF NOT EXISTS picked_at DOUBLE PRECISION")
            conn.execute("ALTER TABLE share_history ADD COLUMN IF NOT EXISTS picked_ip TEXT")
            conn.execute("ALTER TABLE share_history ADD COLUMN IF NOT EXISTS download_log TEXT NOT NULL DEFAULT '[]'")

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
        creator_ip: str | None = None,
        sender_token: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO shares (code, ttl_key, ttl_seconds, created_at, expires_at, burn, enc, key, creator_ip, sender_token) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (code, ttl_key, ttl_seconds, now, now + ttl_seconds, bool(burn), bool(enc), key, creator_ip, sender_token),
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
                "creator_ip": row["creator_ip"],
                "picked_at": row["picked_at"],
                "picked_ip": row["picked_ip"],
                "sender_token": row["sender_token"],
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

    def set_viewed(self, code: str, ip: str | None = None, now: float | None = None) -> bool:
        """Flip pending → viewed (first receiver pickup). Returns True on change.

        Records the receiver's IP + timestamp on the first pickup only — later
        polls never overwrite it (privacy-sensitive, so captured once).
        """
        now = time.time() if now is None else now
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE shares SET status = 'viewed', picked_at = %s, picked_ip = %s "
                "WHERE code = %s AND status = 'pending'",
                (now, ip, code),
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

    def log_download(
        self,
        code: str,
        item_id: str,
        item_name: str,
        ip: str | None = None,
        now: float | None = None,
    ) -> None:
        """Append a receiver download event to the admin-only audit log."""
        now = time.time() if now is None else now
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO download_log (id, code, item_id, item_name, ip, at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), code, item_id, item_name, ip, now),
            )

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
            conn.execute("DELETE FROM share_history")

    def count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM shares").fetchone()["n"]

    # -- persistent share history -------------------------------------------

    def finalize(self, code: str, ended_reason: str, now: float | None = None) -> bool:
        """End a share's life: snapshot it to history and delete its metadata.

        See ``SQLiteStore.finalize`` — same contract, Postgres dialect. The
        ``ON CONFLICT (code) DO NOTHING`` makes retries/cleanup idempotent.
        """
        now = time.time() if now is None else now
        with self._conn() as conn:
            srow = conn.execute(
                "SELECT * FROM shares WHERE code = %s", (code,)
            ).fetchone()
            if srow is None:
                return False
            items = conn.execute(
                "SELECT name, type, size, downloaded FROM items "
                "WHERE code = %s ORDER BY pos ASC",
                (code,),
            ).fetchall()
            dl_rows = conn.execute(
                "SELECT item_name AS item_name, ip AS ip, at AS at FROM download_log "
                "WHERE code = %s ORDER BY at ASC",
                (code,),
            ).fetchall()
            downloads = sum(1 for it in items if it["downloaded"])
            bytes_total = sum(int(it["size"]) for it in items)
            item_meta = [
                {"name": it["name"], "type": it["type"], "size": int(it["size"])}
                for it in items
            ]
            dl_meta = [
                {"item": r["item_name"], "ip": r["ip"], "at": float(r["at"])}
                for r in dl_rows
            ]
            conn.execute(
                "INSERT INTO share_history "
                "(code, created_at, expires_at, ended_at, ended_reason, burn, "
                "downloads, bytes_total, items, creator_ip, picked_at, picked_ip, download_log) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (code) DO NOTHING",
                (
                    code,
                    float(srow["created_at"]),
                    float(srow["expires_at"]),
                    now,
                    ended_reason,
                    bool(srow["burn"]),
                    downloads,
                    bytes_total,
                    json.dumps(item_meta),
                    srow["creator_ip"],
                    srow["picked_at"],
                    srow["picked_ip"],
                    json.dumps(dl_meta),
                ),
            )
            conn.execute("DELETE FROM shares WHERE code = %s", (code,))
        return True

    def history(self, now: float | None = None) -> list[dict[str, Any]]:
        """All historical entries, newest-ended first. Metadata only."""
        del now  # kept for signature symmetry with other store
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT code, created_at, expires_at, ended_at, ended_reason, burn, "
                "downloads, bytes_total, items, creator_ip, picked_at, picked_ip, download_log "
                "FROM share_history ORDER BY ended_at DESC"
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "code": r["code"],
                    "created_at": float(r["created_at"]),
                    "expires_at": float(r["expires_at"]),
                    "ended_at": float(r["ended_at"]),
                    "status": r["ended_reason"],
                    "burn": bool(r["burn"]),
                    "downloads": int(r["downloads"]),
                    "bytes_total": int(r["bytes_total"]),
                    "items": json.loads(r["items"]),
                    "creator_ip": r["creator_ip"],
                    "picked_at": r["picked_at"],
                    "picked_ip": r["picked_ip"],
                    "download_log": json.loads(r["download_log"]),
                }
            )
        return out

    def heartbeat(self) -> None:
        """Tiny read that keeps the Supabase free project awake."""
        with self._conn() as conn:
            conn.execute("SELECT 1")

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
            daily = conn.execute(
                "SELECT (to_timestamp(created_at) AT TIME ZONE 'UTC')::date AS d, "
                "COUNT(*) AS n FROM shares "
                "WHERE created_at >= %s GROUP BY d",
                (day_start - 6 * 86400,),
            ).fetchall()
        avg = avg_row["a"] if avg_row and avg_row["a"] is not None else 0
        today = datetime.fromtimestamp(day_start, tz=timezone.utc).date()
        counts = {str(r["d"]): int(r["n"]) for r in daily}
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
        """Per-share detail for the private admin dashboard. See SQLiteStore."""
        now = time.time() if now is None else now
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT s.code AS code, s.created_at AS created_at, "
                "s.expires_at AS expires_at, s.burn AS burn, s.status AS status, "
                "s.picked_at AS picked_at, s.picked_ip AS picked_ip, "
                "i.name AS name, i.type AS type, i.size AS size, "
                "i.downloaded AS downloaded "
                "FROM shares s LEFT JOIN items i ON i.code = s.code "
                "ORDER BY s.created_at DESC"
            ).fetchall()
            dl_rows = conn.execute(
                "SELECT code AS code, item_name AS item_name, ip AS ip, at AS at "
                "FROM download_log ORDER BY at ASC"
            ).fetchall()
        dl_by_code: dict[str, list[dict[str, Any]]] = {}
        for r in dl_rows:
            dl_by_code.setdefault(r["code"], []).append(
                {"item": r["item_name"], "ip": r["ip"], "at": float(r["at"])}
            )
        by_code: dict[str, dict[str, Any]] = {}
        for r in rows:
            share = by_code.setdefault(
                r["code"],
                {
                    "code": r["code"],
                    "created_at": float(r["created_at"]),
                    "expires_at": float(r["expires_at"]),
                    "burn": bool(r["burn"]),
                    "status": r["status"],
                    "picked_at": r["picked_at"],
                    "picked_ip": r["picked_ip"],
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
                        "size": int(r["size"]),
                        "downloaded": bool(r["downloaded"]),
                    }
                )
                share["bytes_total"] += int(r["size"])
                if r["downloaded"]:
                    share["downloads"] += 1
        for share in by_code.values():
            share["download_log"] = dl_by_code.get(share["code"], [])
            share["active"] = share["expires_at"] > now
            share["expires_in"] = max(0.0, share["expires_at"] - now)
        return list(by_code.values())
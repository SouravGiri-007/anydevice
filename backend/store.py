"""SQLite metadata store for shares and their items.

Metadata lives separate from blobs on purpose: blobs go through BlobStore, so
a future Firestore backend replaces only this module's persistence while the
API layer and blob keys stay identical to the PRD's data model.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
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
    key        TEXT,                              -- server at-rest AES key (base64url)
    creator_ip TEXT,                              -- operator history: creator's IP
    picked_at  REAL,                              -- first receiver pickup (view) time
    picked_ip  TEXT                               -- receiver's IP at first pickup
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
CREATE TABLE IF NOT EXISTS download_log (
    id        TEXT PRIMARY KEY,
    code      TEXT NOT NULL REFERENCES shares(code) ON DELETE CASCADE,
    item_id   TEXT,
    item_name TEXT NOT NULL,
    ip        TEXT,
    at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dlog_code ON download_log(code);
CREATE TABLE IF NOT EXISTS share_history (
    code         TEXT PRIMARY KEY,
    created_at   REAL NOT NULL,
    expires_at   REAL NOT NULL,
    ended_at     REAL NOT NULL,
    ended_reason TEXT NOT NULL,            -- 'expired' | 'burned'
    burn         INTEGER NOT NULL DEFAULT 0,
    downloads    INTEGER NOT NULL DEFAULT 0,
    bytes_total  INTEGER NOT NULL DEFAULT 0,
    items        TEXT NOT NULL,            -- JSON [{name,type,size}] — metadata only
    creator_ip   TEXT,
    picked_at    REAL,                     -- first receiver pickup (view) time
    picked_ip    TEXT,                     -- receiver's IP at first pickup
    download_log TEXT                      -- JSON [{item_name,ip,at}] — receiver audit
);
CREATE INDEX IF NOT EXISTS idx_history_ended ON share_history(ended_at);
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
        "creator_ip": row["creator_ip"],
        "picked_at": row["picked_at"],
        "picked_ip": row["picked_ip"],
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
        if "creator_ip" not in cols:
            self._conn.execute("ALTER TABLE shares ADD COLUMN creator_ip TEXT")
        if "picked_at" not in cols:
            self._conn.execute("ALTER TABLE shares ADD COLUMN picked_at REAL")
        if "picked_ip" not in cols:
            self._conn.execute("ALTER TABLE shares ADD COLUMN picked_ip TEXT")
        hcols = {row["name"] for row in self._conn.execute("PRAGMA table_info(share_history)")}
        if "picked_at" not in hcols:
            self._conn.execute("ALTER TABLE share_history ADD COLUMN picked_at REAL")
        if "picked_ip" not in hcols:
            self._conn.execute("ALTER TABLE share_history ADD COLUMN picked_ip TEXT")
        if "download_log" not in hcols:
            self._conn.execute("ALTER TABLE share_history ADD COLUMN download_log TEXT NOT NULL DEFAULT '[]'")

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
        creator_ip: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self._lock:
            self._conn.execute(
                "INSERT INTO shares (code, ttl_key, ttl_seconds, created_at, expires_at, burn, enc, key, creator_ip) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (code, ttl_key, ttl_seconds, now, now + ttl_seconds, int(burn), int(enc), key, creator_ip),
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

    def set_viewed(self, code: str, ip: str | None = None, now: float | None = None) -> bool:
        """Flip pending → viewed (first receiver pickup). Returns True on change.

        Records the receiver's IP + timestamp on the first pickup only — later
        polls never overwrite it (privacy-sensitive, so captured once).
        """
        now = time.time() if now is None else now
        with self._lock:
            cur = self._conn.execute(
                "UPDATE shares SET status = 'viewed', picked_at = ?, picked_ip = ? "
                "WHERE code = ? AND status = 'pending'",
                (now, ip, code),
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
        with self._lock:
            self._conn.execute(
                "INSERT INTO download_log (id, code, item_id, item_name, ip, at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), code, item_id, item_name, ip, now),
            )
            self._conn.commit()

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
            self._conn.execute("DELETE FROM share_history")
            self._conn.commit()

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM shares").fetchone()[0]

    # -- persistent share history -------------------------------------------

    def finalize(self, code: str, ended_reason: str, now: float | None = None) -> bool:
        """End a share's life: snapshot it to history and delete its metadata.

        ``ended_reason`` is 'expired' or 'burned'. Idempotent: the history row is
        keyed on the code, so re-running after a crash/retry can't duplicate it.
        Physical content is NOT retained — only names/types/sizes plus the final
        download count. Returns False when the code was already gone (no-op).
        """
        now = time.time() if now is None else now
        with self._lock:
            srow = self._conn.execute(
                "SELECT * FROM shares WHERE code = ?", (code,)
            ).fetchone()
            if srow is None:
                return False
            items = self._conn.execute(
                "SELECT name, type, size, downloaded FROM items "
                "WHERE code = ? ORDER BY pos ASC",
                (code,),
            ).fetchall()
            dl_rows = self._conn.execute(
                "SELECT item_name, ip, at FROM download_log "
                "WHERE code = ? ORDER BY at ASC",
                (code,),
            ).fetchall()
            downloads = sum(1 for it in items if it["downloaded"])
            bytes_total = sum(it["size"] for it in items)
            item_meta = [
                {"name": it["name"], "type": it["type"], "size": it["size"]}
                for it in items
            ]
            dl_meta = [
                {"item": r["item_name"], "ip": r["ip"], "at": r["at"]}
                for r in dl_rows
            ]
            self._conn.execute(
                "INSERT OR IGNORE INTO share_history "
                "(code, created_at, expires_at, ended_at, ended_reason, burn, "
                "downloads, bytes_total, items, creator_ip, picked_at, picked_ip, download_log) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    code,
                    srow["created_at"],
                    srow["expires_at"],
                    now,
                    ended_reason,
                    int(srow["burn"]),
                    downloads,
                    bytes_total,
                    json.dumps(item_meta),
                    srow["creator_ip"],
                    srow["picked_at"],
                    srow["picked_ip"],
                    json.dumps(dl_meta),
                ),
            )
            self._conn.execute("DELETE FROM shares WHERE code = ?", (code,))
            self._conn.commit()
        return True

    def history(self, now: float | None = None) -> list[dict[str, Any]]:
        """All historical entries, newest-ended first. Metadata only."""
        del now  # kept for signature symmetry with other store
        rows = self._conn.execute(
            "SELECT code, created_at, expires_at, ended_at, ended_reason, burn, "
            "downloads, bytes_total, items, creator_ip, picked_at, picked_ip, download_log "
            "FROM share_history ORDER BY ended_at DESC"
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "code": r["code"],
                    "created_at": r["created_at"],
                    "expires_at": r["expires_at"],
                    "ended_at": r["ended_at"],
                    "status": r["ended_reason"],
                    "burn": bool(r["burn"]),
                    "downloads": r["downloads"],
                    "bytes_total": r["bytes_total"],
                    "items": json.loads(r["items"]),
                    "creator_ip": r["creator_ip"],
                    "picked_at": r["picked_at"],
                    "picked_ip": r["picked_ip"],
                    "download_log": json.loads(r["download_log"]),
                }
            )
        return out

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
                "s.picked_at AS picked_at, s.picked_ip AS picked_ip, "
                "i.name AS name, i.type AS type, i.size AS size, "
                "i.downloaded AS downloaded "
                "FROM shares s LEFT JOIN items i ON i.code = s.code "
                "ORDER BY s.created_at DESC"
            ).fetchall()
            dl_rows = self._conn.execute(
                "SELECT code AS code, item_name AS item_name, ip AS ip, at AS at "
                "FROM download_log ORDER BY at ASC"
            ).fetchall()
        dl_by_code: dict[str, list[dict[str, Any]]] = {}
        for r in dl_rows:
            dl_by_code.setdefault(r["code"], []).append(
                {"item": r["item_name"], "ip": r["ip"], "at": r["at"]}
            )
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
                        "size": r["size"],
                        "downloaded": bool(r["downloaded"]),
                    }
                )
                share["bytes_total"] += r["size"]
                if r["downloaded"]:
                    share["downloads"] += 1
        for share in by_code.values():
            share["download_log"] = dl_by_code.get(share["code"], [])
            share["active"] = share["expires_at"] > now
            share["expires_in"] = max(0.0, share["expires_at"] - now)
        return list(by_code.values())

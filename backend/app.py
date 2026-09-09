"""AnyDevice Share — Flask REST API (local-first backend).

Endpoints (per PRD section 9):
  POST /api/share                       create a code + attach initial item(s)
  POST /api/share/<code>                append item(s) to an existing share
  GET  /api/share/<code>                fetch metadata + item list
  GET  /api/share/<code>/status         sender's cheap pickup poll
  GET  /api/share/<code>/clipboard      receiver's live text sync (inline content)
  GET  /api/share/<code>/download/<id>  stream one item
  GET  /api/share/<code>/download-all   zip + stream everything
  GET  /api/admin/stats                 operator-only aggregate stats (X-Admin-Key)
  GET  /api/admin/shares                operator-only share detail feed (X-Admin-Key)
  GET  /admin                           static admin dashboard shell (public; data is key-gated)

Run:  python -m backend.app            (or: flask --app backend.app run)
"""
from __future__ import annotations

import io
import logging
import os
import re
import tempfile
import time
import uuid
import zipfile
from functools import wraps
from pathlib import Path
from secrets import compare_digest
from urllib.parse import quote

from flask import Flask, Response, jsonify, request, send_file

from .cleanup import purge_once, start_cleanup_thread
from .codes import generate_unique_code, normalise_code
from .config import Config, DEFAULT_TTL_KEY, TTL_OPTIONS
from .limits import RateLimiter
from .storage import BlobTooLargeError, DiskBlobStore
from .store import SQLiteStore
from .crypto import decrypt_bytes, encrypt_bytes, new_key

try:
    from .supabase_storage import SupabaseBlobStore
    from .supabase_store import SupabaseStore
except ImportError:  # psycopg/requests not installed — supabase backend unavailable
    SupabaseBlobStore = None  # type: ignore[assignment, misc]
    SupabaseStore = None  # type: ignore[assignment, misc]

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("anydevice.app")


def _client_ip(cfg: Config) -> str:
    """Best-effort client IP.

    X-Forwarded-For is only consulted when the deployment sits behind a trusted
    proxy (Render, etc.) — see ANYDEVICE_TRUST_PROXY. Off by default so clients
    can't trivially spoof their rate-limit bucket.
    """
    if cfg.trust_proxy:
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _clean_display_name(raw: str, fallback: str = "file") -> str:
    name = (raw or "").replace("\x00", "").strip()
    name = "".join(c for c in name if c not in "\r\n\t")
    name = name.strip()
    return name[:200] if name else fallback


def _guess_mime(name: str) -> str:
    import mimetypes

    return mimetypes.guess_type(name)[0] or "application/octet-stream"


# --------------------------------------------------------------------------
# Payload parsing
# --------------------------------------------------------------------------

def _parse_share_form(json_items: list) -> list[dict]:
    """Validate the `items` array of a JSON create/append body."""
    if not isinstance(json_items, list) or not json_items:
        raise ValueError("'items' must be a non-empty array")
    out: list[dict] = []
    for entry in json_items:
        if not isinstance(entry, dict):
            raise ValueError("each item must be an object")
        kind = entry.get("type")
        if kind not in ("text", "code_snippet"):
            raise ValueError("item type must be 'text' (files use multipart upload)")
        name = _clean_display_name(entry.get("name", ""), "note")
        content = entry.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("text items need non-empty 'content'")
        out.append(
            {
                "type": "text",
                "name": name,
                "mime": _guess_mime(name) or "text/plain",
                "content": content,
                "size": len(content.encode("utf-8")),
            }
        )
    return out


def _parse_payload(cfg: Config) -> tuple[str, bool, bool, list[dict]]:
    """Return (ttl_key, burn, enc, item_specs) for create / append.

    `enc` marks that item content is client-side encrypted (ciphertext never
    touches the key — the key travels in the URL fragment only).

    Accepts two wire formats:
      * application/json: {"ttl", "burn", "enc", "items": [...]}  (text only)
      * multipart/form-data: field `meta` = JSON {"ttl", "burn", "enc"},
        field `text_items` = JSON [{name, content}], file parts under `files`.
    """
    if request.is_json:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError("expected a JSON object body")
        ttl = body.get("ttl", DEFAULT_TTL_KEY)
        burn = bool(body.get("burn", False))
        enc = bool(body.get("enc", False))
        specs = _parse_share_form(body.get("items"))
        return ttl, burn, enc, specs

    meta_raw = request.form.get("meta", "{}")
    try:
        import json as _json

        meta = _json.loads(meta_raw)
    except Exception:
        raise ValueError("form field 'meta' must be valid JSON")
    if not isinstance(meta, dict):
        raise ValueError("form field 'meta' must be a JSON object")
    ttl = meta.get("ttl", DEFAULT_TTL_KEY)
    burn = bool(meta.get("burn", False))
    enc = bool(meta.get("enc", False))

    specs: list[dict] = []
    text_raw = request.form.get("text_items")
    if text_raw:
        try:
            import json as _json

            entries = _json.loads(text_raw)
        except Exception:
            raise ValueError("form field 'text_items' must be valid JSON")
        specs.extend(_parse_share_form(entries))

    # Accept parts under an explicit `files` key (our UI) or any other name
    # (handy for curl/testing) — never both, to avoid double-counting.
    file_parts = request.files.getlist("files") or list(request.files.values())
    for f in file_parts:
        if f.filename is None or f.filename == "":
            continue
        name = _clean_display_name(Path(f.filename).name, "file")
        specs.append(
            {
                "type": "file",
                "name": name,
                "mime": f.mimetype or _guess_mime(name),
                "stream": f.stream,
                "size": 0,  # filled in once written
            }
        )
    if not specs:
        raise ValueError("share needs at least one file or text item")
    return ttl, burn, enc, specs


def _validate_ttl(ttl) -> tuple[str, int]:
    if not isinstance(ttl, str) or ttl not in TTL_OPTIONS:
        raise ValueError(f"ttl must be one of {', '.join(TTL_OPTIONS)}")
    return ttl, TTL_OPTIONS[ttl]


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------

def _item_json(item: dict, key: str | None = None) -> dict:
    # Server decrypts content at-rest → the API always returns plaintext.
    content = item.get("content")
    if item["type"] == "text" and content and key:
        try:
            content = decrypt_bytes(_unb64url(content), key).decode("utf-8")
        except Exception:
            content = ""
    return {
        "id": item["id"],
        "type": item["type"],
        "name": item.get("name", ""),
        "mime": item.get("mime", "application/octet-stream"),
        "size": item.get("size", 0),
        "downloaded": bool(item.get("downloaded")),
        "content": content if item["type"] == "text" else None,
    }


def _share_json(share: dict) -> dict:
    now = time.time()
    key = share.get("key")
    items = [_item_json(i, key) for i in share["items"]]
    return {
        "code": share["code"],
        "created_at": share["created_at"],
        "expires_at": share["expires_at"],
        "expires_in": max(0.0, share["expires_at"] - now),
        "ttl_key": share["ttl_key"],
        "ttl_seconds": share["ttl_seconds"],
        "burn": share["burn"],
        "status": share.get("status", "pending"),
        "enc": False,  # server decrypts everything at rest → wire is always plaintext
        "items": items,
        "bytes_total": sum(i["size"] for i in share["items"]),
    }


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code or "error"


def _err(status: int, message: str, **extra) -> tuple[Response, int]:
    payload = {"error": message, **extra}
    return jsonify(payload), status


# --------------------------------------------------------------------------
# Blob staging helpers
# --------------------------------------------------------------------------

def _blob_key(code: str, item_id: str, name: str) -> str:
    from .storage import storage_safe_name

    return f"shares/{code}/{item_id}/{storage_safe_name(name)}"


def _b64url(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64url(text: str) -> bytes:
    import base64

    raw = text.replace("-", "+").replace("_", "/")
    raw += "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw)


def _decrypt_item_text(item: dict, key: str | None) -> bytes:
    """Plaintext bytes for a text item (decrypts at-rest ciphertext)."""
    content = item.get("content") or ""
    if key:
        try:
            return decrypt_bytes(_unb64url(content), key)
        except Exception:
            return b""
    return content.encode("utf-8")


def _decrypt_file_blob(enc_blob: bytes, key: str | None) -> bytes:
    if key:
        try:
            return decrypt_bytes(enc_blob, key)
        except Exception:
            return b""
    return enc_blob


def _stage_file_items(cfg: Config, code: str, specs: list[dict], key: str) -> list[dict]:
    """Write file blobs to storage, enforcing per-file and running-total caps.

    Every item is wrapped with the share's at-rest `key` before it's persisted:
    text content becomes base64url([iv][ciphertext]), file blobs are stored as
    raw [iv][ciphertext] bytes. Plaintext never touches disk.
    """
    total = 0
    staged: list[str] = []
    items: list[dict] = []
    try:
        for spec in specs:
            if spec["type"] == "text":
                item = dict(spec)
                item["id"] = uuid.uuid4().hex
                plain = (spec["content"] or "").encode("utf-8")
                item["content"] = _b64url(encrypt_bytes(plain, key)) if key else plain.decode("utf-8", "replace")
                items.append(item)
                total += len(plain)
                continue
            item_id = uuid.uuid4().hex
            blob_key = _blob_key(code, item_id, spec["name"])
            plain = spec["stream"].read(cfg.file_max_bytes + 1)
            if len(plain) > cfg.file_max_bytes:
                raise BlobTooLargeError(len(plain))
            stored = encrypt_bytes(plain, key) if key else plain
            size = cfg.blob_store.put(
                key=blob_key, stream=io.BytesIO(stored), max_bytes=None
            )
            item = {
                "id": item_id,
                "type": "file",
                "name": spec["name"],
                "mime": spec["mime"] or "application/octet-stream",
                "size": len(plain),
                "blob_key": blob_key,
            }
            staged.append(blob_key)
            items.append(item)
            total += len(plain)
            if total > cfg.total_max_bytes:
                raise ValueError(
                    f"total content exceeds the {cfg.total_max_bytes // (1024*1024)}MB per-share limit"
                )
        if len(items) > cfg.items_max:
            raise ValueError(f"too many items (max {cfg.items_max} per share)")
        return items
    except BaseException:
        for k in staged:
            cfg.blob_store.delete(k)
        raise


# --------------------------------------------------------------------------
# App factory
# --------------------------------------------------------------------------

def _build_stores(cfg: Config) -> None:
    """Attach blob_store + meta_store to cfg, choosing the engine from
    ANYDEVICE_BACKEND: "disk" (default) or "supabase"."""
    if cfg.backend == "supabase":
        if SupabaseStore is None or SupabaseBlobStore is None:
            raise RuntimeError(
                "ANYDEVICE_BACKEND=supabase needs 'psycopg[binary]' and 'requests' "
                "(add them with: pip install -r backend/requirements.txt)"
            )
        if not (cfg.supabase_url and cfg.supabase_service_key and cfg.supabase_database_url):
            raise RuntimeError(
                "ANYDEVICE_BACKEND=supabase requires SUPABASE_URL, "
                "SUPABASE_SERVICE_ROLE_KEY and SUPABASE_DATABASE_URL"
            )
        cfg.meta_store = SupabaseStore(cfg.supabase_database_url)  # type: ignore[attr-defined]
        cfg.blob_store = SupabaseBlobStore(  # type: ignore[attr-defined]
            cfg.supabase_url, cfg.supabase_service_key, cfg.supabase_bucket
        )
        return
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.blob_store = DiskBlobStore(cfg.data_dir / "blobs")  # type: ignore[attr-defined]
    cfg.meta_store = SQLiteStore(cfg.data_dir / "db.sqlite3")  # type: ignore[attr-defined]


def _start_heartbeat(cfg: Config) -> None:
    """Keep a Supabase free-tier project awake: a tiny read every couple of
    minutes costs nothing and prevents the 7-day-idle auto-pause."""
    if cfg.backend != "supabase":
        return

    def tick() -> None:
        while True:
            try:
                cfg.meta_store.heartbeat()  # type: ignore[attr-defined]
            except Exception:
                pass
            time.sleep(120)

    import threading

    threading.Thread(target=tick, daemon=True, name="supabase-heartbeat").start()


def create_app(cfg: Config | None = None) -> Flask:
    cfg = cfg or Config.from_env()
    _build_stores(cfg)
    limiter = RateLimiter()
    global _ADMIN_HTML
    _ADMIN_HTML = Path(__file__).resolve().parent / "static" / "admin.html"

    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    app.config["MAX_CONTENT_LENGTH"] = cfg.total_max_bytes + 2 * 1024 * 1024
    app.extensions["anydevice_cfg"] = cfg

    if cfg.start_cleanup:
        start_cleanup_thread(cfg.meta_store, cfg.blob_store, cfg.cleanup_interval_seconds)
    _start_heartbeat(cfg)

    @app.after_request
    def cors_headers(response: Response) -> Response:
        origin = request.headers.get("Origin")
        if "*" in cfg.cors_origins:
            response.headers["Access-Control-Allow-Origin"] = "*"
        elif origin in cfg.cors_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Key"
        response.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
        return response

    @app.route("/api/health")
    def health():
        db_ok = True
        error = None
        try:
            cfg.meta_store.heartbeat()
        except Exception as e:  # noqa: BLE001
            db_ok = False
            error = type(e).__name__
        payload = {"ok": db_ok, "service": "anydevice-share", "backend": cfg.backend}
        if error is not None:
            payload["error"] = error
        return jsonify(payload), (200 if db_ok else 503)

    @app.errorhandler(ApiError)
    def handle_api_error(e: ApiError):
        return _err(e.status, e.message, code=e.code)

    @app.errorhandler(413)
    def too_large(_e):
        limit_mb = cfg.file_max_bytes // (1024 * 1024)
        return _err(413, f"Upload too large — per-file cap is {limit_mb}MB")

    @app.errorhandler(404)
    def not_found(_e):
        return _err(404, "Nothing here.")

    @app.errorhandler(405)
    def method_not_allowed(_e):
        return _err(405, "Method not allowed.")

    @app.errorhandler(500)
    def internal(_e):
        return _err(500, "Something broke on our side. The share probably still exists — try again.")

    def rate_limit(bucket: str, limit: int) -> None:
        allowed, retry = limiter.allow(f"{bucket}:{_client_ip(cfg)}", limit, cfg.limit_window_seconds)
        if not allowed:
            raise ApiError(429, f"Too many tries — wait {max(1, int(retry) + 1)}s.", code="rate_limited")

    def fetch_live_share(code_raw: str) -> dict:
        """Normalise + load a share; purge it first if it has expired."""
        code = normalise_code(code_raw)
        if code is None:
            raise ApiError(400, "That doesn't look like a code. Use 5–6 letters/numbers.")
        share = cfg.meta_store.get(code)
        if share is None:
            raise ApiError(404, "No share with that code — it may have self-destructed already.", code="not_found")
        if time.time() >= share["expires_at"]:
            purge_once(cfg.meta_store, cfg.blob_store)
            raise ApiError(404, "That share has expired.", code="expired")
        return share

    # -- operator stats ------------------------------------------------------

    def require_admin(f):
        """Shared-secret gate for operator-only routes (ANYDEVICE_ADMIN_KEY).

        No user accounts: this is a single service-level key for the operator.
        Requests are throttled on the same in-memory limiter used everywhere
        else so a brute-forcer can't sit and guess the header forever. When no
        key is configured the route is disabled entirely (503)."""

        @wraps(f)
        def wrapper(*args, **kwargs):
            rate_limit("admin", 10)
            expected = cfg.admin_key
            if not expected:
                raise ApiError(503, "Operator stats are disabled — set ANYDEVICE_ADMIN_KEY.")
            given = request.headers.get("X-Admin-Key", "")
            if not given or not compare_digest(given, expected):
                raise ApiError(401, "Missing or invalid admin key.", code="forbidden")
            return f(*args, **kwargs)

        return wrapper

    @app.get("/api/admin/stats")
    @require_admin
    def admin_stats():
        """Operator-only aggregate stats (PII-free).

        Purposely returns plain counts/averages — no IPs, codes, filenames, or
        content ever cross the wire here, matching the project's no-tracking
        stance. Hit it with curl/Postman: -H \"X-Admin-Key: <ANYDEVICE_ADMIN_KEY>\"
        """
        now = time.time()
        return jsonify(
            {
                "ok": True,
                "backend": cfg.backend,
                "generated_at": now,
                **cfg.meta_store.stats(now=now),
            }
        )

    @app.get("/api/admin/shares")
    @require_admin
    def admin_shares():
        """Operator-only per-share detail feed for the private dashboard.

        Same X-Admin-Key gate as /api/admin/stats. Returns only what the system
        actually tracks (code, item names/sizes, download counts, expiry) — no
        sender/receiver identities, IPs, or device info exist anywhere, so none
        is returned. Codes are single-use transfer handles, not user accounts.
        """
        now = time.time()
        shares = cfg.meta_store.admin_shares(now=now)
        return jsonify(
            {
                "ok": True,
                "backend": cfg.backend,
                "generated_at": now,
                "count": len(shares),
                "shares": shares,
            }
        )

    @app.get("/admin")
    def admin_page():
        """Static shell for the admin dashboard (no data).

        Public on purpose: browsers can't send the X-Admin-Key header, so the
        page prompts for the key client-side and every data call goes through
        the require_admin gate above. Serving the shell leaks nothing — without
        the key it cannot load a single share or stat.
        """
        html = _ADMIN_HTML
        if not html.exists():
            return _err(404, "admin.html is not bundled with this build.")
        return Response(
            html.read_text(encoding="utf-8"),
            mimetype="text/html",
            headers={"Cache-Control": "no-store"},
        )

    # -- create -------------------------------------------------------------

    @app.post("/api/share")
    def create_share():
        rate_limit("create", cfg.create_limit)
        try:
            ttl_key, burn, enc, specs = _parse_payload(cfg)
            ttl_key, ttl_seconds = _validate_ttl(ttl_key)
        except ValueError as e:
            raise ApiError(400, str(e))
        # Individual text cap (they don't go through blob storage).
        for s in specs:
            if s["type"] == "text" and s["size"] > cfg.max_text_bytes:
                raise ApiError(413, "Text pastes are capped at 500KB.")

        code = generate_unique_code(cfg.meta_store.code_exists)
        key = new_key()
        staged_keys: list[str] = []
        try:
            items = _stage_file_items(cfg, code, specs, key)
            share = cfg.meta_store.create_share(code, ttl_key, ttl_seconds, burn, items, enc=False, key=key)
        except BlobTooLargeError:
            raise ApiError(413, f"File too large — cap is {cfg.file_max_bytes // (1024*1024)}MB per file.")
        except ValueError as e:
            raise ApiError(400, str(e))
        except Exception:
            cfg.blob_store.delete_prefix(f"shares/{code}")
            cfg.meta_store.delete(code)
            raise
        return jsonify(_share_json(share)), 201

    # -- fetch --------------------------------------------------------------

    @app.get("/api/share/<code_raw>")
    def get_share(code_raw: str):
        rate_limit("lookup", cfg.lookup_limit)
        share = fetch_live_share(code_raw)
        # ?mark_viewed=1 — the receiver opening the share. Flips pending → viewed
        # so the sender's page can show "picked up". Sender-side status polls use
        # the /status endpoint instead and never flip it.
        if request.args.get("mark_viewed") == "1" and share.get("status") != "viewed":
            cfg.meta_store.set_viewed(share["code"])
            share["status"] = "viewed"
        return jsonify(_share_json(share))

    @app.get("/api/share/<code_raw>/status")
    def share_status(code_raw: str):
        """Lightweight pickup-status poll used by the sender's page while waiting.

        Deliberately cheap and NOT counted against the lookup rate limit, so a
        sender can poll every couple of seconds without tripping brute-force
        protection. Never flips status (only GET ?mark_viewed=1 does)."""
        rate_limit("poll", cfg.poll_limit)
        share = fetch_live_share(code_raw)
        return jsonify(
            {
                "code": share["code"],
                "status": share.get("status", "pending"),
                "expires_in": max(0.0, share["expires_at"] - time.time()),
            }
        )

    @app.get("/api/share/<code_raw>/clipboard")
    def share_clipboard(code_raw: str):
        """Live clipboard sync for the receiving device.

        Returns every text item inline (server-decrypted plaintext) so the grab
        side can surface pasted text without a download click. Same cheap poll
        bucket as /status — never flips status, never marks anything downloaded,
        and never affects burn. New pastes arrive simply by polling again."""
        rate_limit("poll", cfg.poll_limit)
        share = fetch_live_share(code_raw)
        key = share.get("key")
        items = [_item_json(i, key) for i in share["items"] if i["type"] == "text"]
        return jsonify({"code": share["code"], "items": items})

    # -- append -------------------------------------------------------------

    @app.post("/api/share/<code_raw>")
    def append_to_share(code_raw: str):
        rate_limit("create", cfg.create_limit)
        share = fetch_live_share(code_raw)
        try:
            _ttl, _burn, _enc, specs = _parse_payload(cfg)
        except ValueError as e:
            raise ApiError(400, str(e))
        for s in specs:
            if s["type"] == "text" and s["size"] > cfg.max_text_bytes:
                raise ApiError(413, "Text pastes are capped at 500KB.")
        if len(share["items"]) + len(specs) > cfg.items_max:
            raise ApiError(400, f"Too many items (max {cfg.items_max} per share).")
        if share["bytes_total"] >= cfg.total_max_bytes:
            raise ApiError(413, "This share is already at its total size limit.")

        try:
            items = _stage_file_items(cfg, share["code"], specs, share["key"])
            # Enforce total cap across existing + incoming after staging.
            if share["bytes_total"] + sum(i["size"] for i in items) > cfg.total_max_bytes:
                for i in items:
                    if i.get("blob_key"):
                        cfg.blob_store.delete(i["blob_key"])
                raise ValueError(
                    f"total content would exceed the {cfg.total_max_bytes // (1024*1024)}MB per-share limit"
                )
            cfg.meta_store.append_items(share["code"], items)
        except BlobTooLargeError:
            raise ApiError(413, f"File too large — cap is {cfg.file_max_bytes // (1024*1024)}MB per file.")
        except ValueError as e:
            raise ApiError(400, str(e))
        updated = fetch_live_share(share["code"])
        return jsonify(_share_json(updated)), 200

    def _burn_now(share: dict) -> bool:
        """Share should self-destruct once the current download finishes."""
        if not share["burn"]:
            return False
        return cfg.meta_store.all_items_downloaded(share["code"])

    def _purge_share(share: dict) -> None:
        cfg.blob_store.delete_prefix(f"shares/{share['code']}")
        cfg.meta_store.delete(share["code"])

    # -- download one -------------------------------------------------------

    @app.get("/api/share/<code_raw>/download/<item_id>")
    def download_item(code_raw: str, item_id: str):
        rate_limit("download", cfg.download_limit)
        share = fetch_live_share(code_raw)
        item = cfg.meta_store.item(share["code"], item_id)
        if item is None:
            raise ApiError(404, "That item isn't part of this share.", code="not_found")
        inline = request.args.get("inline") == "1"
        name = item["name"] or ("note" if item["type"] == "text" else "file")
        disposition = _content_disposition(name, inline=inline)
        key = share.get("key")

        if not inline:
            cfg.meta_store.mark_item_downloaded(share["code"], item_id)
            burn_now = _burn_now(share)
        else:
            burn_now = False

        if item["type"] == "text":
            data = _decrypt_item_text(item, key)
            resp = Response(
                io.BytesIO(data),
                mimetype=item.get("mime") or "text/plain",
                headers={"Content-Disposition": disposition},
            )
            if burn_now:
                _purge_share(share)
            return resp

        # File: decrypt the whole bounded blob, then stream it out. Cleanup
        # (and burn-purge) happens after the body has been fully sent.
        handle = cfg.blob_store.open(item["blob_key"])
        enc_blob = handle.read()
        handle.close()
        plain = _decrypt_file_blob(enc_blob, key if key else None)

        def stream_plain():
            try:
                yield plain
            finally:
                if burn_now:
                    _purge_share(share)

        return Response(
            stream_plain(),
            mimetype=item["mime"] or "application/octet-stream",
            headers={"Content-Disposition": disposition},
        )

    # -- download all -------------------------------------------------------

    @app.get("/api/share/<code_raw>/download-all")
    def download_all(code_raw: str):
        rate_limit("download", cfg.download_limit)
        share = fetch_live_share(code_raw)
        items = share["items"]
        if not items:
            raise ApiError(404, "This share has no items left.")

        cfg.meta_store.mark_all_downloaded(share["code"])
        burn_now = _burn_now(share)

        tmp = tempfile.NamedTemporaryFile(prefix="anydevice-", suffix=".zip", delete=False)
        tmp_path = Path(tmp.name)
        key = share.get("key")
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
                for i, item in enumerate(items, start=1):
                    entry = _zip_name(item["name"], i)
                    if item["type"] == "file":
                        with cfg.blob_store.open(item["blob_key"]) as fh:
                            payload = _decrypt_file_blob(fh.read(), key)
                        zf.writestr(_zip_info(entry), payload)
                    else:
                        zf.writestr(_zip_info(entry), _decrypt_item_text(item, key))
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

        def stream_zip():
            try:
                with open(tmp_path, "rb") as fh:
                    while chunk := fh.read(256 * 1024):
                        yield chunk
            finally:
                tmp_path.unlink(missing_ok=True)
                if burn_now:
                    _purge_share(share)

        return Response(
            stream_zip(),
            mimetype="application/zip",
            headers={"Content-Disposition": _attachment_header(f"{share['code']}.zip")},
        )

    return app


def _zip_name(name: str, pos: int) -> str:
    name = re.sub(r"[/\\]", "_", name or "").strip(". ").strip()
    if not name:
        name = f"note-{pos}.txt"
    return name[:200]


def _zip_info(name: str) -> zipfile.ZipInfo:
    """ZipInfo with the UTF-8 filename flag set (unicode-safe archives)."""
    info = zipfile.ZipInfo(name)
    info.flag_bits |= 0x800  # 0x800 => filename/comment are UTF-8
    info.date_time = time.localtime(time.time())[:6]
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _content_disposition(filename: str, inline: bool) -> str:
    ascii_name = filename.encode("ascii", "ignore").decode() or "download"
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _attachment_header(filename: str) -> str:
    return _content_disposition(filename, inline=False)


if __name__ == "__main__":
    _cfg = Config.from_env()
    if os.environ.get("ANYDEVICE_CLEANUP") in (None, ""):
        os.environ["ANYDEVICE_CLEANUP"] = "1"  # dev convenience: run the purge loop
        _cfg = Config.from_env()
    # Honor ANYDEVICE_PORT; tolerate a PORT of 0/absent (hosting envs set it
    # randomly and we don't want a surprise port in dev).
    _raw_port = os.environ.get("ANYDEVICE_PORT") or os.environ.get("PORT", "")
    try:
        _port = int(_raw_port)
    except (TypeError, ValueError):
        _port = 0
    _app = create_app(_cfg)
    _app.run(host="127.0.0.1", port=_port or 5000, debug=False)

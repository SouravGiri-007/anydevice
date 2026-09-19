"""Share management endpoints.

Handles share creation, fetching, appending, downloading, and self-destruction.
"""
from __future__ import annotations

import io
import logging
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from secrets import token_urlsafe

from flask import Blueprint, Response, jsonify, request, send_file

from ..cleanup import purge_once
from ..codes import generate_unique_code, normalise_code
from ..config import DEFAULT_TTL_KEY, TTL_OPTIONS
from ..crypto import decrypt_bytes, encrypt_bytes, new_key
from ..limits import RateLimiter
from ..storage import BlobTooLargeError

log = logging.getLogger("anydevice.routes.shares")


def create_shares_blueprint(
    cfg,
    limiter: RateLimiter,
    _client_ip,
    _parse_payload,
    _validate_ttl,
    _parse_share_form,
    _stage_file_items,
    _share_json,
    _item_json,
    _content_disposition,
    _attachment_header,
    _zip_name,
    _zip_info,
    _b64url,
    _unb64url,
    _decrypt_item_text,
    _decrypt_file_blob,
    ApiError,
) -> Blueprint:
    """Create the shares blueprint with all share-related endpoints.

    Args:
        cfg: Configuration object
        limiter: Rate limiter instance
        _client_ip: Function to get client IP
        _parse_payload: Function to parse request payload
        _validate_ttl: Function to validate TTL
        _parse_share_form: Function to parse share form
        _stage_file_items: Function to stage files
        _share_json: Function to serialize share to JSON
        _item_json: Function to serialize item to JSON
        _content_disposition: Function to create content disposition header
        _attachment_header: Function to create attachment header
        _zip_name: Function to sanitize zip entry name
        _zip_info: Function to create ZipInfo
        _b64url: Function to base64url encode
        _unb64url: Function to base64url decode
        _decrypt_item_text: Function to decrypt text item
        _decrypt_file_blob: Function to decrypt file blob
        ApiError: ApiError exception class

    Returns:
        Blueprint with share endpoints
    """
    bp = Blueprint("shares", __name__)

    def rate_limit(bucket: str, limit: int) -> None:
        """Check rate limit for the current request."""
        allowed, retry = limiter.allow(
            f"{bucket}:{_client_ip(cfg)}", limit, cfg.limit_window_seconds
        )
        if not allowed:
            retry_seconds = max(1, int(retry) + 1)
            log.warning(
                f"Rate limit exceeded for {bucket} from {_client_ip(cfg)}, retry in {retry_seconds}s"
            )
            raise ApiError(429, f"Too many tries — wait {retry_seconds}s.", code="rate_limited")

    def fetch_live_share(code_raw: str) -> dict:
        """Fetch and validate a live share."""
        code = normalise_code(code_raw)
        if code is None:
            log.debug(f"Invalid code format: {code_raw!r}")
            raise ApiError(
                400, "That doesn't look like a code. Use 5–6 letters/numbers.", code="invalid_code"
            )
        share = cfg.meta_store.get(code)
        if share is None:
            log.debug(f"Share not found: {code}")
            raise ApiError(
                404,
                "No share with that code — it may have self-destructed already.",
                code="not_found",
            )
        if time.time() >= share["expires_at"]:
            log.info(f"Share expired, purging: {code}")
            purge_once(cfg.meta_store, cfg.blob_store)
            raise ApiError(404, "That share has expired.", code="expired")
        return share

    def _blob_key(code: str, item_id: str, name: str) -> str:
        from ..storage import storage_safe_name

        return f"shares/{code}/{item_id}/{storage_safe_name(name)}"

    def _burn_now(share: dict) -> bool:
        """Share should self-destruct once the current download finishes."""
        if not share["burn"]:
            return False
        return cfg.meta_store.all_items_downloaded(share["code"])

    def _purge_share(share: dict, reason: str = "burned") -> None:
        """Delete a share's data AND snapshot it to admin history."""
        try:
            cfg.meta_store.finalize(share["code"], reason)
            cfg.blob_store.delete_prefix(f"shares/{share['code']}")
        except Exception:  # noqa: BLE001
            log.exception("purge failed for %s — will retry on next cleanup", share["code"])

    @bp.post("/api/share")
    def create_share():
        """Create a new share with items."""
        rate_limit("create", cfg.create_limit)
        try:
            ttl_key, burn, enc, specs = _parse_payload(cfg)
            ttl_key, ttl_seconds = _validate_ttl(ttl_key)
        except ValueError as e:
            log.debug(f"Invalid payload: {e}")
            raise ApiError(400, str(e), code="invalid_payload")
        for s in specs:
            if s["type"] == "text" and s["size"] > cfg.max_text_bytes:
                log.debug(f"Text item exceeds limit: {s['size']} > {cfg.max_text_bytes}")
                raise ApiError(413, "Text pastes are capped at 500KB.", code="text_too_large")

        code = generate_unique_code(cfg.meta_store.code_exists)
        key = new_key()
        sender_token = token_urlsafe(24)
        try:
            items = _stage_file_items(cfg, code, specs, key)
            share = cfg.meta_store.create_share(
                code,
                ttl_key,
                ttl_seconds,
                burn,
                items,
                enc=False,
                key=key,
                creator_ip=_client_ip(cfg),
                sender_token=sender_token,
            )
            log.info(f"Share created: {code} with {len(items)} items, TTL={ttl_key}, burn={burn}")
        except BlobTooLargeError:
            log.warning(f"File too large for share {code}")
            raise ApiError(
                413,
                f"File too large — cap is {cfg.file_max_bytes // (1024*1024)}MB per file.",
                code="file_too_large",
            )
        except ValueError as e:
            raise ApiError(400, str(e))
        except Exception:
            cfg.blob_store.delete_prefix(f"shares/{code}")
            cfg.meta_store.delete(code)
            raise
        payload = _share_json(share)
        payload["sender_token"] = sender_token
        return jsonify(payload), 201

    @bp.get("/api/share/<code_raw>")
    def get_share(code_raw: str):
        """Fetch a share and optionally mark it as viewed."""
        rate_limit("lookup", cfg.lookup_limit)
        share = fetch_live_share(code_raw)
        if request.args.get("mark_viewed") == "1" and share.get("status") != "viewed":
            cfg.meta_store.set_viewed(share["code"], ip=_client_ip(cfg))
            share["status"] = "viewed"
        return jsonify(_share_json(share))

    @bp.get("/api/share/<code_raw>/status")
    def share_status(code_raw: str):
        """Lightweight pickup-status poll (sender-side)."""
        rate_limit("poll", cfg.poll_limit)
        share = fetch_live_share(code_raw)
        return jsonify(
            {
                "code": share["code"],
                "status": share.get("status", "pending"),
                "expires_in": max(0.0, share["expires_at"] - time.time()),
            }
        )

    @bp.get("/api/share/<code_raw>/clipboard")
    def share_clipboard(code_raw: str):
        """Live clipboard sync for the receiving device."""
        rate_limit("poll", cfg.poll_limit)
        share = fetch_live_share(code_raw)
        key = share.get("key")
        items = [_item_json(i, key) for i in share["items"] if i["type"] == "text"]
        return jsonify({"code": share["code"], "items": items})

    @bp.post("/api/share/<code_raw>")
    def append_to_share(code_raw: str):
        """Append items to an existing share."""
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
            if share["bytes_total"] + sum(i["size"] for i in items) > cfg.total_max_bytes:
                for i in items:
                    if i.get("blob_key"):
                        cfg.blob_store.delete(i["blob_key"])
                raise ValueError(
                    f"total content would exceed the {cfg.total_max_bytes // (1024*1024)}MB per-share limit"
                )
            cfg.meta_store.append_items(share["code"], items)
        except BlobTooLargeError:
            raise ApiError(
                413,
                f"File too large — cap is {cfg.file_max_bytes // (1024*1024)}MB per file.",
            )
        except ValueError as e:
            raise ApiError(400, str(e))
        updated = fetch_live_share(share["code"])
        return jsonify(_share_json(updated)), 200

    @bp.post("/api/share/<code_raw>/scrap")
    def scrap_share(code_raw: str):
        """Immediately destroy a share (sender-only self-destruct)."""
        from secrets import compare_digest

        rate_limit("create", cfg.create_limit)
        share = fetch_live_share(code_raw)
        expected = share.get("sender_token") or ""
        body = request.get_json(silent=True) or {}
        given = body.get("sender_token") or ""
        if not expected or not given or not compare_digest(given, expected):
            log.warning(f"Scrap attempt without valid token for {code_raw} from {_client_ip(cfg)}")
            raise ApiError(403, "That code belongs to another device.", code="forbidden")
        log.info(f"Share scraped by creator: {share['code']}")
        _purge_share(share, reason="scraped")
        return jsonify({"ok": True, "code": share["code"]}), 200

    @bp.get("/api/share/<code_raw>/download/<item_id>")
    def download_item(code_raw: str, item_id: str):
        """Download a single item from a share."""
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
            cfg.meta_store.log_download(share["code"], item_id, name, ip=_client_ip(cfg))
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

    @bp.get("/api/share/<code_raw>/download-all")
    def download_all(code_raw: str):
        """Download all items as a zip archive."""
        rate_limit("download", cfg.download_limit)
        share = fetch_live_share(code_raw)
        items = share["items"]
        if not items:
            raise ApiError(404, "This share has no items left.")

        cfg.meta_store.mark_all_downloaded(share["code"])
        for it in items:
            cfg.meta_store.log_download(share["code"], it["id"], it["name"] or "file", ip=_client_ip(cfg))
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

    return bp

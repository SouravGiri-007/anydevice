"""Admin endpoints for operational statistics and monitoring.

Operator-only routes protected by X-Admin-Key header.
"""
from __future__ import annotations

import logging
import time
from functools import wraps

from flask import Blueprint, Response, jsonify, request

log = logging.getLogger("anydevice.routes.admin")


def create_admin_blueprint(
    cfg,
    limiter,
    _client_ip,
    _ADMIN_HTML,
    ApiError,
) -> Blueprint:
    """Create the admin blueprint with operator endpoints.

    Args:
        cfg: Configuration object
        limiter: Rate limiter instance
        _client_ip: Function to get client IP
        _ADMIN_HTML: Path to admin.html file
        ApiError: ApiError exception class

    Returns:
        Blueprint with admin endpoints
    """
    from secrets import compare_digest

    bp = Blueprint("admin", __name__)

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

    def require_admin(f):
        """Shared-secret gate for operator-only routes."""

        @wraps(f)
        def wrapper(*args, **kwargs):
            rate_limit("admin", 10)
            expected = cfg.admin_key
            if not expected:
                log.warning("Admin route accessed but ANYDEVICE_ADMIN_KEY not configured")
                raise ApiError(
                    503, "Operator stats are disabled — set ANYDEVICE_ADMIN_KEY.", code="disabled"
                )
            given = request.headers.get("X-Admin-Key", "")
            if not given or not compare_digest(given, expected):
                log.warning(f"Admin auth failed from {_client_ip(cfg)}")
                raise ApiError(401, "Missing or invalid admin key.", code="forbidden")
            log.info(f"Admin access granted to {request.path} from {_client_ip(cfg)}")
            return f(*args, **kwargs)

        return wrapper

    @bp.get("/api/admin/stats")
    @require_admin
    def admin_stats():
        """Operator-only aggregate stats (PII-free)."""
        now = time.time()
        return jsonify(
            {
                "ok": True,
                "backend": cfg.backend,
                "generated_at": now,
                **cfg.meta_store.stats(now=now),
            }
        )

    @bp.get("/api/admin/shares")
    @require_admin
    def admin_shares():
        """Operator-only per-share detail feed for the private dashboard."""
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

    @bp.get("/api/admin/history")
    @require_admin
    def admin_history():
        """Operator-only persistent Share History (metadata only)."""
        now = time.time()
        entries = cfg.meta_store.history()
        return jsonify(
            {
                "ok": True,
                "backend": cfg.backend,
                "generated_at": now,
                "count": len(entries),
                "history": entries,
            }
        )

    @bp.get("/admin")
    def admin_page():
        """Static shell for the admin dashboard (no data)."""
        html = _ADMIN_HTML
        if not html.exists():
            return {"error": "admin.html is not bundled with this build."}, 404
        return Response(
            html.read_text(encoding="utf-8"),
            mimetype="text/html",
            headers={"Cache-Control": "no-store"},
        )

    return bp

"""Health check endpoint.

Provides a simple liveness probe for deployment monitoring.
"""
from __future__ import annotations

import logging
from flask import Blueprint, jsonify

log = logging.getLogger("anydevice.routes.health")


def create_health_blueprint(cfg) -> Blueprint:
    """Create the health check blueprint.

    Args:
        cfg: Configuration object with meta_store and backend info

    Returns:
        Blueprint with health check routes
    """
    bp = Blueprint("health", __name__)

    @bp.get("/api/health")
    def health():
        """Health check endpoint for deployment monitoring.

        Returns:
            JSON response with service health status (200 if healthy, 503 if unhealthy)
        """
        db_ok = True
        error = None
        try:
            cfg.meta_store.heartbeat()
        except Exception as e:  # noqa: BLE001
            db_ok = False
            error = type(e).__name__
            log.error(f"Health check failed: {error}", exc_info=True)
        payload = {"ok": db_ok, "service": "anydevice-share", "backend": cfg.backend}
        if error is not None:
            payload["error"] = error
        return jsonify(payload), (200 if db_ok else 503)

    return bp

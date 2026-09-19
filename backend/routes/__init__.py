"""Flask blueprints for AnyDevice Share API routes.

This module organizes API endpoints into logical blueprints:
- health: Liveness probe for deployment monitoring
- shares: Share creation, fetching, appending, downloading
- admin: Operator statistics and monitoring
"""
from __future__ import annotations

from .admin import create_admin_blueprint
from .health import create_health_blueprint
from .shares import create_shares_blueprint

__all__ = [
    "create_health_blueprint",
    "create_shares_blueprint",
    "create_admin_blueprint",
]

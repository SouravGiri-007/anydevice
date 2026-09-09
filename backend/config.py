"""Environment-driven configuration for the backend.

Every value can be overridden with ANYDEVICE_* environment variables, which
keeps the same code runnable in dev and (later) on Render.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Characters we generate codes from. 0/O and 1/I are excluded (ambiguous when
# a human types a code they saw on another screen).
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 5
CODE_LENGTH_LONG = 6

# TTL options surfaced to the UI. Key is the wire value, value is seconds.
DEFAULT_TTL_KEY = "1h"
TTL_OPTIONS = {
    "5m": 5 * 60,
    "1h": 60 * 60,
    "24h": 24 * 60 * 60,
}

# File type allow-list. Anything else is rejected with a clear message.
ALLOWED_MIME_PREFIXES = ("image/", "text/", "application/")
ALLOWED_EXTENSIONS = {
    "pdf", "docx", "doc", "xlsx", "xls", "pptx", "txt", "md", "py", "js", "ts",
    "tsx", "html", "css", "json", "csv", "zip",
}
# application/octet-stream is allowed so genuinely unknown files still work.
ALLOWED_MIME_EXACT = {"application/octet-stream"}


@dataclass
class Config:
    data_dir: Path
    # File / total size caps in bytes.
    file_max_bytes: int = 50 * 1024 * 1024
    total_max_bytes: int = 100 * 1024 * 1024
    items_max: int = 50
    # Rate limits (per IP per window).
    lookup_limit: int = 5
    download_limit: int = 60
    create_limit: int = 15
    poll_limit: int = 120  # sender-side pickup polls must never trip the lookup cap
    limit_window_seconds: int = 60
    # Cleanup.
    cleanup_interval_seconds: int = 60
    start_cleanup: bool = False
    # Human-facing product name.
    product_name: str = "AnyDevice"
    max_filename_length: int = 150
    max_text_bytes: int = 500_000
    cors_origins: tuple[str, ...] = ("*",)
    # Set true when deployed behind a trusted reverse proxy (Render/Vercel)
    # so rate limiting keys on the real client IP.
    trust_proxy: bool = False

    # Operator-only admin endpoint secret. When empty the /api/admin/stats
    # route is disabled (no key configured → nothing to brute-force).
    admin_key: str = ""

    # Backend storage engine: "disk" (SQLite + local blobs) or "supabase"
    # (Postgres + Supabase Storage).
    backend: str = "disk"
    # Supabase credentials (only read when backend == "supabase").
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = "shares"
    supabase_database_url: str = ""

    # Injected at create_app time (kept on cfg for route access).
    blob_store: object = None
    meta_store: object = None

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)

    @classmethod
    def from_env(cls) -> "Config":
        # Local dev convenience: read optional project .env (a no-op when the
        # file is absent, so deployed hosts that set real env vars are unaffected).
        load_dotenv()
        return cls(
            data_dir=Path(os.environ.get("ANYDEVICE_DATA_DIR", "./data")).resolve(),
            file_max_bytes=_env_int("ANYDEVICE_FILE_MAX", cls.file_max_bytes),
            total_max_bytes=_env_int("ANYDEVICE_TOTAL_MAX", cls.total_max_bytes),
            items_max=_env_int("ANYDEVICE_ITEMS_MAX", cls.items_max),
            lookup_limit=_env_int("ANYDEVICE_LOOKUP_LIMIT", cls.lookup_limit),
            download_limit=_env_int("ANYDEVICE_DOWNLOAD_LIMIT", cls.download_limit),
            create_limit=_env_int("ANYDEVICE_CREATE_LIMIT", cls.create_limit),
            poll_limit=_env_int("ANYDEVICE_POLL_LIMIT", cls.poll_limit),
            limit_window_seconds=_env_int("ANYDEVICE_LIMIT_WINDOW", cls.limit_window_seconds),
            cleanup_interval_seconds=_env_int("ANYDEVICE_CLEANUP_INTERVAL", cls.cleanup_interval_seconds),
            start_cleanup=_env_bool("ANYDEVICE_CLEANUP", False),
            max_filename_length=_env_int("ANYDEVICE_MAX_FILENAME", cls.max_filename_length),
            max_text_bytes=_env_int("ANYDEVICE_MAX_TEXT", cls.max_text_bytes),
            trust_proxy=_env_bool("ANYDEVICE_TRUST_PROXY", False),
            admin_key=os.environ.get("ANYDEVICE_ADMIN_KEY", "").strip(),
            backend=os.environ.get("ANYDEVICE_BACKEND", "disk").strip().lower(),
            supabase_url=os.environ.get("SUPABASE_URL", "").strip().rstrip("/"),
            supabase_service_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
            supabase_bucket=os.environ.get("SUPABASE_BUCKET", "shares").strip(),
            supabase_database_url=os.environ.get("SUPABASE_DATABASE_URL", "").strip(),
        )

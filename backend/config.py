"""Environment-driven configuration for the backend.

Every value can be overridden with ANYDEVICE_* environment variables, which
keeps the same code runnable in dev and (later) on Render.

This module provides a Config dataclass that loads settings from environment
variables with sensible defaults. All settings are validated on load to catch
configuration errors early.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _env_int(name: str, default: int) -> int:
    """Read an integer from an environment variable with a default fallback.

    Args:
        name: Environment variable name
        default: Default value if not set or empty

    Returns:
        Integer value from environment or default

    Raises:
        ValueError: If the environment variable is set but not a valid integer
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Environment variable {name} must be an integer, got: {raw!r}")


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean from an environment variable with a default fallback.

    Accepts: "1", "true", "yes", "on" (case-insensitive) as True.

    Args:
        name: Environment variable name
        default: Default value if not set or empty

    Returns:
        Boolean value from environment or default
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Characters we generate codes from. 0/O and 1/I are excluded (ambiguous when
# a human types a code they saw on another screen).
CODE_ALPHABET: str = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH: int = 5
CODE_LENGTH_LONG: int = 6

# TTL options surfaced to the UI. Key is the wire value, value is seconds.
DEFAULT_TTL_KEY: str = "1h"
TTL_OPTIONS: dict[str, int] = {
    "5m": 5 * 60,
    "1h": 60 * 60,
    "24h": 24 * 60 * 60,
}

# File type allow-list. Anything else is rejected with a clear message.
ALLOWED_MIME_PREFIXES: tuple[str, ...] = ("image/", "text/", "application/")
ALLOWED_EXTENSIONS: set[str] = {
    "pdf", "docx", "doc", "xlsx", "xls", "pptx", "txt", "md", "py", "js", "ts",
    "tsx", "html", "css", "json", "csv", "zip",
}
# application/octet-stream is allowed so genuinely unknown files still work.
ALLOWED_MIME_EXACT: set[str] = {"application/octet-stream"}


@dataclass
class Config:
    """Application configuration loaded from environment variables.

    All configuration values are validated on instantiation. Size limits are in bytes,
    rate limits are per IP per time window. When backend is "supabase", Supabase
    credentials must be provided.

    Attributes:
        data_dir: Local directory for SQLite database and blob storage (disk backend only).
        file_max_bytes: Maximum size per file in bytes (default: 50 MB).
        total_max_bytes: Maximum total size per share in bytes (default: 100 MB).
        items_max: Maximum number of items per share (default: 50).
        lookup_limit: Lookups allowed per IP per window (default: 5).
        download_limit: Downloads allowed per IP per window (default: 60).
        create_limit: Share creations allowed per IP per window (default: 15).
        poll_limit: Status/clipboard polls allowed per IP per window (default: 120).
        limit_window_seconds: Time window for rate limiting in seconds (default: 60).
        cleanup_interval_seconds: How often to purge expired shares in seconds (default: 60).
        start_cleanup: Whether to start the background cleanup thread (default: False).
        product_name: Human-readable product name for docs (default: "AnyDevice").
        max_filename_length: Max length for stored filenames (default: 150).
        max_text_bytes: Maximum size for text items in bytes (default: 500 KB).
        cors_origins: Tuple of allowed CORS origins (default: ("*",)).
        trust_proxy: Whether to trust X-Forwarded-For header for client IP (default: False).
        admin_key: Shared secret for admin endpoints; disabled when empty (default: "").
        backend: Storage backend - "disk" or "supabase" (default: "disk").
        supabase_url: Supabase project URL (only used when backend == "supabase").
        supabase_service_key: Supabase service role key (only used when backend == "supabase").
        supabase_bucket: Supabase storage bucket name (default: "shares").
        supabase_database_url: Supabase database connection string with pooler.
        blob_store: Injected storage backend instance (set at app creation).
        meta_store: Injected metadata store instance (set at app creation).
    """

    data_dir: Path
    file_max_bytes: int = 50 * 1024 * 1024
    total_max_bytes: int = 100 * 1024 * 1024
    items_max: int = 50
    lookup_limit: int = 5
    download_limit: int = 60
    create_limit: int = 15
    poll_limit: int = 120
    limit_window_seconds: int = 60
    cleanup_interval_seconds: int = 60
    start_cleanup: bool = False
    product_name: str = "AnyDevice"
    max_filename_length: int = 150
    max_text_bytes: int = 500_000
    cors_origins: tuple[str, ...] = ("*",)
    trust_proxy: bool = False
    admin_key: str = ""
    backend: str = "disk"
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = "shares"
    supabase_database_url: str = ""
    blob_store: object | None = None
    meta_store: object | None = None

    def __post_init__(self) -> None:
        """Validate and normalize configuration after instantiation."""
        self.data_dir = Path(self.data_dir)
        self._validate()

    def _validate(self) -> None:
        """Validate configuration values.

        Raises:
            ValueError: If configuration is invalid
        """
        if self.backend not in ("disk", "supabase"):
            raise ValueError(f"backend must be 'disk' or 'supabase', got: {self.backend!r}")

        if self.file_max_bytes <= 0:
            raise ValueError(f"file_max_bytes must be positive, got: {self.file_max_bytes}")

        if self.total_max_bytes <= 0:
            raise ValueError(f"total_max_bytes must be positive, got: {self.total_max_bytes}")

        if self.items_max <= 0:
            raise ValueError(f"items_max must be positive, got: {self.items_max}")

        if self.limit_window_seconds <= 0:
            raise ValueError(f"limit_window_seconds must be positive, got: {self.limit_window_seconds}")

        if self.cleanup_interval_seconds <= 0:
            raise ValueError(f"cleanup_interval_seconds must be positive, got: {self.cleanup_interval_seconds}")

        if self.backend == "supabase":
            if not self.supabase_url:
                raise ValueError("SUPABASE_URL required when backend is 'supabase'")
            if not self.supabase_service_key:
                raise ValueError("SUPABASE_SERVICE_ROLE_KEY required when backend is 'supabase'")
            if not self.supabase_database_url:
                raise ValueError("SUPABASE_DATABASE_URL required when backend is 'supabase'")

    @classmethod
    def from_env(cls) -> Config:
        """Create configuration by reading environment variables.

        Loads optional .env file first (dev convenience), then reads
        ANYDEVICE_* and Supabase environment variables. All values are
        validated on instantiation.

        Returns:
            Config instance with values from environment

        Raises:
            ValueError: If environment variables are invalid or required values are missing
        """
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

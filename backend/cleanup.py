"""Expired-share purge.

Deletes expired metadata rows AND their storage blobs. Blobs live under
``shares/{code}/`` so one prefix delete covers everything, matching the PRD's
Firebase cleanup story.

Runnable three ways:
  * ``python -m backend.cleanup`` — purge once, for a cron / Render job;
  * in-process scheduler thread started by the Flask app (ANYDEVICE_CLEANUP=1).
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("anydevice.cleanup")


def purge_once(store, blob_store) -> int:
    """Delete every expired share. Returns number of shares purged.

    Each expired share is snapshotted to ``share_history`` (metadata only) via
    ``store.finalize(..., 'expired')`` before its blobs are dropped, so admin
    history survives even if the process restarts mid-pass. ``finalize`` is
    idempotent (code primary key), so a retried purge can't duplicate entries.
    """
    expired = store.expired_codes()
    purged = 0
    for code in expired:
        try:
            finalized = store.finalize(code, "expired")
        except Exception:  # noqa: BLE001 - never kill the cleanup loop
            log.exception("history finalize failed for %s", code)
            continue
        if not finalized:
            continue
        try:
            blob_store.delete_prefix(f"shares/{code}")
        except OSError:
            log.warning("blob cleanup failed for %s", code)
        purged += 1
        log.info("purged expired share %s → history", code)
    return purged


def run_cleanup_loop(store, blob_store, interval_seconds: int, stop_event=None) -> None:
    """Blocking loop; intended to run on a daemon thread."""
    import threading

    if stop_event is None:
        stop_event = threading.Event()
    while not stop_event.wait(interval_seconds):
        try:
            purge_once(store, blob_store)
        except Exception:  # noqa: BLE001 - never kill the loop
            log.exception("cleanup pass failed")


def start_cleanup_thread(store, blob_store, interval_seconds: int):
    import threading

    thread = threading.Thread(
        target=run_cleanup_loop,
        args=(store, blob_store, interval_seconds),
        name="anydevice-cleanup",
        daemon=True,
    )
    thread.start()
    return thread


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

    from .config import Config
    from .storage import DiskBlobStore
    from .store import SQLiteStore

    cfg = Config.from_env()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore(cfg.data_dir / "db.sqlite3")
    blobs = DiskBlobStore(cfg.data_dir / "blobs")
    t0 = time.time()
    n = purge_once(store, blobs)
    print(f"purged {n} expired share(s) in {time.time() - t0:.3f}s")

"""Supabase-backed smoke tests.

These run only when a real Supabase project is configured via env vars:

    ANYDEVICE_BACKEND=supabase
    SUPABASE_URL=...            (https://<ref>.supabase.co)
    SUPABASE_SERVICE_ROLE_KEY=...
    SUPABASE_DATABASE_URL=...   (postgresql:// postgres pooler or direct URI)

They exercise the same API surface the SQLite tests do, but against the
configured project (a FREE-tier project is enough). They are skipped when the
vars are absent so `pytest` stays green without Supabase.
"""
from __future__ import annotations

import io
import os
import uuid

import pytest

from backend.supabase_storage import SupabaseBlobStore
from backend.supabase_store import SupabaseStore

_WANT = {
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_DATABASE_URL",
}
_MISSING = [k for k in _WANT if not os.environ.get(k)]

pytestmark = pytest.mark.skipif(
    bool(_MISSING),
    reason="Supabase env vars not set (set SUPABASE_URL, "
    "SUPABASE_SERVICE_ROLE_KEY, SUPABASE_DATABASE_URL to run)",
)


@pytest.fixture()
def stores():
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    dsn = os.environ["SUPABASE_DATABASE_URL"]
    store = SupabaseStore(dsn)
    blobs = SupabaseBlobStore(url, key)
    store.wipe()
    yield store, blobs
    store.wipe()


def _item(item_id: str, **kw) -> dict:
    base = dict(type="text", name="note.txt", mime="text/plain", size=0, content="hi", blob_key=None)
    base.update(kw)
    base["id"] = item_id
    return base


def test_create_and_get_roundtrip(stores):
    store, _ = stores
    code = "SUPA1"
    store.create_share(code, "1h", 3600, burn=False, items=[_item(f"{uuid.uuid4()}")], key="k1")
    share = store.get(code)
    assert share is not None and share["code"] == code
    assert share["burn"] is False and share["status"] == "pending" and share["key"] == "k1"
    assert len(share["items"]) == 1


def test_expired_codes_and_delete(stores):
    store, _ = stores
    store.create_share("SUPA2", "1h", 3600, burn=True, items=[_item(f"{uuid.uuid4()}")], now=1000)
    assert store.expired_codes(5000) == ["SUPA2"]
    store.delete("SUPA2")
    assert store.code_exists("SUPA2") is False


def test_append_and_viewed_and_downloads(stores):
    store, _ = stores
    code = "SUPA3"
    store.create_share(code, "1h", 3600, burn=False, items=[_item("a")])
    assert store.append_items(code, [_item("b")]) is True
    assert store.append_items("NOSUC", [_item("c")]) is False
    share = store.get(code)
    assert [i["id"] for i in share["items"]] == ["a", "b"]
    assert store.set_viewed(code) is True
    assert store.set_viewed(code) is False  # already viewed
    store.mark_item_downloaded(code, "a")
    assert store.all_items_downloaded(code) is False
    store.mark_all_downloaded(code)
    assert store.all_items_downloaded(code) is True


def test_blob_roundtrip_and_delete_prefix(stores):
    _, blobs = stores
    key = f"shares/SBLOB1/{uuid.uuid4()}/x.bin"
    size = blobs.put(key, io.BytesIO(b"hello storage"))
    assert size == 13
    got = blobs.open(key).read()
    assert got == b"hello storage"
    assert blobs.exists(key) is True
    blobs.delete_prefix("shares/SBLOB1")
    assert blobs.exists(key) is False
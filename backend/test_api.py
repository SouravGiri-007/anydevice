"""API tests for the AnyDevice Share backend (local-first mode)."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

import pytest

from backend.app import create_app
from backend.config import Config

TEXT_ITEMS = [
    {"type": "text", "name": "hello.py", "content": 'print("hello from device A")\n'}
]

VALID_CODE_CHARS = set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")


def _cfg(app):
    return app.extensions["anydevice_cfg"]


def _cfg_for(app):
    return app.extensions["anydevice_cfg"]


@pytest.fixture()
def client(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        lookup_limit=1000,
        download_limit=1000,
        create_limit=1000,
    )
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app.test_client()


def _make_share(client, **body):
    payload = {"ttl": "5m", "burn": False, "items": TEXT_ITEMS}
    payload.update(body)
    return client.post("/api/share", json=payload)


def _force_expiry(app, code):
    cfg = _cfg(app)
    cfg.meta_store._conn.execute(
        "UPDATE shares SET expires_at = ? WHERE code = ?", (1, code)
    )
    cfg.meta_store._conn.commit()


def _upload_file(client, filename="hello.txt", content=b"device A file contents", **meta):
    meta = {"ttl": "1h", "burn": False, **meta}
    return client.post(
        "/api/share",
        data={"meta": json.dumps(meta), filename: (io.BytesIO(content), filename)},
        content_type="multipart/form-data",
    )


# -- health & create ---------------------------------------------------------


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["service"] == "anydevice-share"
    assert body["backend"] == "disk"


def test_create_text_share_returns_code(client):
    r = _make_share(client)
    assert r.status_code == 201
    data = r.get_json()
    code = data["code"]
    assert len(code) == 5
    assert set(code) <= VALID_CODE_CHARS  # excludes 0/O/1/I
    assert data["ttl_key"] == "5m"
    assert data["burn"] is False
    item = data["items"][0]
    assert item["type"] == "text"
    assert item["content"] == TEXT_ITEMS[0]["content"]


def test_create_text_share_ttl_validation(client):
    assert _make_share(client, ttl="3d").status_code == 400
    assert _make_share(client, ttl="24h").status_code == 201
    assert _make_share(client, ttl="5m").status_code == 201


def test_create_empty_items_rejected(client):
    r = client.post("/api/share", json={"ttl": "1h", "items": []})
    assert r.status_code == 400


# -- fetch -------------------------------------------------------------------


def test_fetch_roundtrip(client):
    code = _make_share(client).get_json()["code"]
    r = client.get(f"/api/share/{code}")
    assert r.status_code == 200
    data = r.get_json()
    assert data["code"] == code
    assert data["expires_in"] > 0
    assert data["items"][0]["content"].startswith("print(")


def test_unknown_code_404(client):
    r = client.get("/api/share/ZZZZZ")
    assert r.status_code == 404
    assert r.get_json()["code"] == "not_found"


def test_lowercase_code_is_normalised(client):
    code = _make_share(client).get_json()["code"]
    assert client.get(f"/api/share/{code.lower()}").status_code == 200


def test_ambiguous_characters_rejected_fast(client):
    # 0/O/1/I aren't in the alphabet -> 400 without burning a lookup.
    assert client.get("/api/share/00001").status_code == 400


# -- files & multipart -------------------------------------------------------


def test_create_and_download_file(client):
    r = _upload_file(client)
    assert r.status_code == 201
    code = r.get_json()["code"]
    item = r.get_json()["items"][0]
    assert item["type"] == "file"
    assert item["name"] == "hello.txt"

    dl = client.get(f"/api/share/{code}/download/{item['id']}")
    assert dl.status_code == 200
    assert dl.data == b"device A file contents"


def test_inline_preview_header(client):
    r = _upload_file(client, "pic.png", b"\x89PNG fake")
    code = r.get_json()["code"]
    item = r.get_json()["items"][0]
    dl = client.get(f"/api/share/{code}/download/{item['id']}?inline=1")
    assert dl.status_code == 200
    assert "inline" in dl.headers["Content-Disposition"]


def test_text_and_file_together_via_multipart(client):
    r = client.post(
        "/api/share",
        data={
            "meta": json.dumps({"ttl": "1h", "burn": False}),
            "text_items": json.dumps([{"type": "text", "name": "todo.md", "content": "# ship it"}]),
            "notes.txt": (io.BytesIO(b"xyz"), "notes.txt"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 201
    types = {i["type"] for i in r.get_json()["items"]}
    assert types == {"file", "text"}


def test_download_all_zips_everything(client):
    code = _make_share(
        client, items=[{"type": "text", "name": "a.md", "content": "alpha"}]
    ).get_json()["code"]
    r = client.post(
        f"/api/share/{code}",
        data={"meta": json.dumps({"ttl": "1h"}), "b.txt": (io.BytesIO(b"beta"), "b.txt")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert len(r.get_json()["items"]) == 2

    dl = client.get(f"/api/share/{code}/download-all")
    assert dl.status_code == 200
    assert dl.mimetype == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(dl.data))
    assert sorted(zf.namelist()) == ["a.md", "b.txt"]
    assert zf.read("a.md") == b"alpha"
    assert zf.read("b.txt") == b"beta"


# -- append ------------------------------------------------------------------


def test_append_to_share(client):
    code = _make_share(client).get_json()["code"]
    r = client.post(
        f"/api/share/{code}",
        json={"items": [{"type": "text", "name": "second.py", "content": "x = 2"}]},
    )
    assert r.status_code == 200
    assert len(r.get_json()["items"]) == 2


def test_append_to_missing_share_404(client):
    r = client.post(
        "/api/share/NOPE",
        json={"items": [{"type": "text", "name": "x.txt", "content": "x"}]},
    )
    assert r.status_code == 404


# -- pickup status (live "picked up ✓" on the sender's page) ---------------


def test_create_starts_pending(client):
    code = _make_share(client).get_json()["code"]
    data = client.get(f"/api/share/{code}").get_json()
    assert data["status"] == "pending"


def test_plain_get_does_not_flip_status(client):
    code = _make_share(client).get_json()["code"]
    client.get(f"/api/share/{code}")
    client.get(f"/api/share/{code}")
    data = client.get(f"/api/share/{code}").get_json()
    assert data["status"] == "pending"


def test_receiver_get_marks_viewed_once(client):
    code = _make_share(client).get_json()["code"]
    r = client.get(f"/api/share/{code}?mark_viewed=1")
    assert r.status_code == 200
    assert r.get_json()["status"] == "viewed"
    # Stays viewed for everyone after that.
    assert client.get(f"/api/share/{code}").get_json()["status"] == "viewed"


def test_status_endpoint_polls_without_flipping(client):
    code = _make_share(client).get_json()["code"]
    r = client.get(f"/api/share/{code}/status")
    assert r.status_code == 200
    assert r.get_json()["status"] == "pending"
    assert r.get_json()["expires_in"] > 0
    # Polling must never flip it.
    assert client.get(f"/api/share/{code}/status").get_json()["status"] == "pending"
    # Receiver opens -> poll now reports viewed.
    client.get(f"/api/share/{code}?mark_viewed=1")
    assert client.get(f"/api/share/{code}/status").get_json()["status"] == "viewed"


def test_status_endpoint_404_when_expired(client):
    code = _make_share(client).get_json()["code"]
    _force_expiry(client.application, code)
    assert client.get(f"/api/share/{code}/status").status_code == 404


def test_status_polls_bypass_lookup_rate_limit(tmp_path):
    cfg = Config(data_dir=tmp_path / "data", lookup_limit=1, download_limit=100, create_limit=100)
    app = create_app(cfg)
    app.config["TESTING"] = True
    client = app.test_client()
    code = _make_share(client).get_json()["code"]
    # Lookup budget exhausted after one GET...
    assert client.get(f"/api/share/{code}").status_code == 200
    assert client.get(f"/api/share/{code}").status_code == 429
    # ...but the sender can keep polling status freely.
    for _ in range(5):
        assert client.get(f"/api/share/{code}/status").status_code == 200


# -- live clipboard sync (receiver's text poll) -----------------------------


def test_clipboard_returns_text_inline(client):
    code = _make_share(client).get_json()["code"]
    r = client.get(f"/api/share/{code}/clipboard")
    assert r.status_code == 200
    body = r.get_json()
    assert body["code"] == code
    assert [i["type"] for i in body["items"]] == ["text"]
    assert body["items"][0]["content"] == 'print("hello from device A")\n'


def test_clipboard_ignores_files_and_adds_new_text(client):
    r = _make_share(
        client,
        items=[
            {"type": "text", "name": "greet.txt", "content": "hi"},
            {"type": "text", "name": "note.txt", "content": "hello"},
        ],
    )
    assert r.status_code == 201
    code = r.get_json()["code"]
    # Append a file — should not appear in clipboard.
    client.post(
        f"/api/share/{code}",
        data={
            "meta": json.dumps({"ttl": "1h"}),
            "clip.txt": (io.BytesIO(b"binary blob"), "clip.txt"),
        },
        content_type="multipart/form-data",
    )
    # Append a text item — should appear in clipboard.
    client.post(
        f"/api/share/{code}",
        data={
            "meta": json.dumps({"ttl": "1h"}),
            "text_items": json.dumps(
                [{"type": "text", "name": "clip.txt", "content": "copied on device B"}]
            ),
        },
        content_type="multipart/form-data",
    )
    items = client.get(f"/api/share/{code}/clipboard").get_json()["items"]
    assert [i["content"] for i in items] == ["hi", "hello", "copied on device B"]


def test_clipboard_is_readonly_and_never_flips_status(client):
    code = _make_share(client, burn=True).get_json()["code"]
    # Polling repeatedly neither marks viewed nor downloads items.
    assert client.get(f"/api/share/{code}/clipboard").status_code == 200
    assert client.get(f"/api/share/{code}/clipboard").status_code == 200
    data = client.get(f"/api/share/{code}").get_json()
    assert data["status"] == "pending"
    assert all(not i["downloaded"] for i in data["items"])


def test_clipboard_404_when_expired(client):
    code = _make_share(client).get_json()["code"]
    _force_expiry(client.application, code)
    assert client.get(f"/api/share/{code}/clipboard").status_code == 404


# -- operator stats (admin) ---------------------------------------------------


def _admin_app(tmp_path, admin_key="hk-operator-key", **cfg_kw):
    cfg = Config(
        data_dir=tmp_path / "data",
        lookup_limit=1000,
        download_limit=1000,
        create_limit=1000,
        admin_key=admin_key,
        **cfg_kw,
    )
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app.test_client(), app


def test_admin_stats_disabled_when_unconfigured(client):
    r = client.get("/api/admin/stats")
    assert r.status_code == 503


def test_admin_stats_requires_valid_key(tmp_path):
    client, _ = _admin_app(tmp_path, admin_key="hk-secret")
    assert client.get("/api/admin/stats").status_code == 401
    assert client.get("/api/admin/stats", headers={"X-Admin-Key": "wrong"}).status_code == 401
    r = client.get("/api/admin/stats", headers={"X-Admin-Key": "hk-secret"})
    assert r.status_code == 200


def test_admin_stats_aggregates_pii_free(tmp_path):
    client, app = _admin_app(tmp_path, admin_key="hk-secret")
    # Three shares: 2-byte text, 5-byte burn text, 20-byte file.
    _make_share(client, items=[{"type": "text", "name": "a.txt", "content": "hi"}])
    _make_share(client, burn=True, items=[{"type": "text", "name": "b.txt", "content": "hello"}])
    _upload_file(client, "c.bin", b"01234567890123456789")
    # Expire the burn share → inactive but still counted in totals.
    code = _cfg_for(app).meta_store._conn.execute("SELECT code FROM shares").fetchall()[1]["code"]
    _force_expiry(app, code)

    r = client.get("/api/admin/stats", headers={"X-Admin-Key": "hk-secret"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["backend"] == "disk"
    assert body["shares_total"] == 3
    assert body["shares_24h"] == 3
    assert body["shares_active"] == 2
    assert body["burn_pct"] == 33.3
    assert body["avg_share_bytes"] == 9.0
    # Daily breakdown (calendar-based, UTC): all three created "now".
    today = datetime.now(timezone.utc).date().isoformat()
    assert body["shares_today"] == 3
    assert body["shares_yesterday"] == 0
    last7 = body["last_7_days"]
    assert len(last7) == 7
    assert last7[-1] == {"date": today, "count": 3}
    assert sum(d["count"] for d in last7) == 3
    # No PII / content-identifying data anywhere in the payload.
    payload = json.dumps(body)
    for banned in ("a.txt", "b.txt", "c.bin", "hello", "0123456789"):
        assert banned not in payload


def test_admin_page_serves_standalone_dashboard(client):
    # Static shell is public (browsers can't send X-Admin-Key) but contains no data.
    r = client.get("/admin")
    assert r.status_code == 200
    assert r.mimetype == "text/html"
    text = r.get_data(as_text=True)
    assert "AnyDevice" in text
    assert "/api/admin/stats" in text
    assert "/api/admin/shares" in text
    assert 'type="password"' in text
    assert client.get("/admin").status_code == 200


def test_admin_stats_rate_limited(tmp_path):
    client, _ = _admin_app(tmp_path, admin_key="hk-secret")
    for _ in range(10):
        assert client.get("/api/admin/stats", headers={"X-Admin-Key": "hk-secret"}).status_code == 200
    assert (
        client.get("/api/admin/stats", headers={"X-Admin-Key": "hk-secret"}).status_code == 429
    )


def test_admin_shares_requires_key(tmp_path):
    client, _ = _admin_app(tmp_path, admin_key="hk-secret")
    assert client.get("/api/admin/shares").status_code == 401
    assert client.get("/api/admin/shares", headers={"X-Admin-Key": "wrong"}).status_code == 401
    r = client.get("/api/admin/shares", headers={"X-Admin-Key": "hk-secret"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["shares"] == []


def test_admin_shares_lists_active_and_expired(tmp_path):
    client, app = _admin_app(tmp_path, admin_key="hk-secret")
    code = _make_share(client).get_json()["code"]
    body = client.get("/api/admin/shares", headers={"X-Admin-Key": "hk-secret"}).get_json()
    assert body["count"] == 1
    sh = body["shares"][0]
    assert sh["code"] == code
    assert sh["status"] == "pending"
    assert sh["burn"] is False
    assert sh["active"] is True
    assert sh["bytes_total"] == len(TEXT_ITEMS[0]["content"].encode())
    assert sh["downloads"] == 0
    assert sh["items"] == [
        {"name": "hello.py", "type": "text", "size": len(TEXT_ITEMS[0]["content"].encode()),
         "downloaded": False}
    ]
    _force_expiry(app, code)
    body2 = client.get("/api/admin/shares", headers={"X-Admin-Key": "hk-secret"}).get_json()
    assert body2["shares"][0]["active"] is False
    assert body2["shares"][0]["expires_in"] == 0


def test_admin_shares_tracks_downloads_and_burn(tmp_path):
    client, _ = _admin_app(tmp_path, admin_key="hk-secret")
    _make_share(client, items=[{"type": "text", "name": "note", "content": "hello world"}])
    r = _upload_file(client, "pic.png", b"\x89PNG fake image bytes", burn=True)
    j = r.get_json()
    code, item = j["code"], j["items"][0]
    # Downloading a burn share's last file self-destructs it.
    assert client.get(f"/api/share/{code}/download/{item['id']}").status_code == 200

    body = client.get("/api/admin/shares", headers={"X-Admin-Key": "hk-secret"}).get_json()
    assert body["count"] == 1  # burned share is gone, text share remains
    sh = body["shares"][0]
    assert sh["items"][0]["name"] == "note"
    assert sh["downloads"] == 0

    # Re-create a burn file share but don't finish downloading: still listed.
    r2 = _upload_file(client, "keep.png", b"still here", burn=True)
    j2 = r2.get_json()
    code2, item2 = j2["code"], j2["items"][0]
    body2 = client.get("/api/admin/shares", headers={"X-Admin-Key": "hk-secret"}).get_json()
    burn_s = next(s for s in body2["shares"] if s["code"] == code2)
    assert burn_s["burn"] is True
    assert burn_s["downloads"] == 0
    assert burn_s["bytes_total"] == len(b"still here")
    assert burn_s["active"] is True
    # Download the file → downloads count and the share disappears.
    assert client.get(f"/api/share/{code2}/download/{item2['id']}").status_code == 200
    body3 = client.get("/api/admin/shares", headers={"X-Admin-Key": "hk-secret"}).get_json()
    assert all(s["code"] != code2 for s in body3["shares"])


# -- server-managed at-rest encryption ----------------------------------------


def test_api_always_returns_plaintext(client):
    """Content is encrypted at rest, but the API returns plaintext (the server
    holds the key and decrypts on read) — no client key entry required."""
    body = {"ttl": "5m", "enc": False, "items": TEXT_ITEMS}
    r = _make_share(client, **body)
    assert r.status_code == 201
    data = r.get_json()
    assert data["enc"] is False
    # Text comes back decrypted by the server.
    assert data["items"][0]["content"] == 'print("hello from device A")\n'
    got = client.get(f"/api/share/{data['code']}").get_json()
    assert got["items"][0]["content"] == 'print("hello from device A")\n'


def test_at_rest_encryption_on_disk(client):
    """What's written to the DB is ciphertext, never the plaintext user sent."""
    r = _make_share(client, **{"items": TEXT_ITEMS})
    code = r.get_json()["code"]
    cfg = _cfg(client.application)
    share = cfg.meta_store.get(code)
    assert share["key"], "share must hold a server key"
    assert share["items"][0]["content"] != 'print("hello from device A")\n'
    # ...but round-trips back to plaintext over the API.
    got = client.get(f"/api/share/{code}").get_json()
    assert got["items"][0]["content"] == 'print("hello from device A")\n'


def test_file_roundtrip_plaintext(client):
    plain = b"hello from the sender's device"
    r = _upload_file(client, "hello.txt", content=plain)
    assert r.status_code == 201
    data = r.get_json()
    item = data["items"][0]
    dl = client.get(f"/api/share/{data['code']}/download/{item['id']}")
    assert dl.status_code == 200
    assert dl.data == plain  # server decrypts at-rest layer before streaming

    cfg = _cfg(client.application)
    share = cfg.meta_store.get(data["code"])
    assert share["key"]
    stored = cfg.meta_store.item(data["code"], item["id"])
    with cfg.blob_store.open(stored["blob_key"]) as fh:
        assert fh.read() != plain  # stored blob is ciphertext


# -- expiry & burn -----------------------------------------------------------


def test_expired_share_is_purged_and_404(client):
    code = _make_share(client).get_json()["code"]
    _force_expiry(client.application, code)
    r = client.get(f"/api/share/{code}")
    assert r.status_code == 404
    assert r.get_json()["code"] == "expired"
    cfg = _cfg(client.application)
    assert cfg.meta_store.get(code) is None
    assert cfg.meta_store.count() == 0


def test_burn_after_full_download(client):
    r = _upload_file(client, "only.pdf", b"%PDF-1.4 fake", burn=True)
    assert r.status_code == 201
    code, item = r.get_json()["code"], r.get_json()["items"][0]

    assert client.get(f"/api/share/{code}/download/{item['id']}").status_code == 200
    # First full download burns the share.
    assert client.get(f"/api/share/{code}").status_code == 404


def test_burn_waits_until_all_items_downloaded(client):
    code = _make_share(
        client,
        burn=True,
        items=[
            {"type": "text", "name": "one.txt", "content": "one"},
            {"type": "text", "name": "two.txt", "content": "two"},
        ],
    ).get_json()["code"]

    item_one = client.get(f"/api/share/{code}").get_json()["items"][0]
    assert client.get(f"/api/share/{code}/download/{item_one['id']}").status_code == 200
    assert client.get(f"/api/share/{code}").status_code == 200  # still alive

    assert client.get(f"/api/share/{code}/download-all").status_code == 200
    assert client.get(f"/api/share/{code}").status_code == 404  # now burned


def test_ttl_respected_on_poll(client):
    code = _make_share(client).get_json()["code"]
    data = client.get(f"/api/share/{code}").get_json()
    assert data["ttl_key"] == "5m"
    assert abs(data["expires_in"] - 300) < 2


# -- caps & rate limits ------------------------------------------------------


def test_per_file_size_cap_rejected(client):
    big = b"x" * (50 * 1024 * 1024 + 1)
    r = _upload_file(client, "big.bin", big)
    assert r.status_code in (400, 413)


def test_total_cap_across_share(tmp_path):
    cfg = Config(data_dir=tmp_path / "data", total_max_bytes=100, file_max_bytes=10_000)
    app = create_app(cfg)
    app.config["TESTING"] = True
    client = app.test_client()
    code = _upload_file(client, "a.bin", b"a" * 60).get_json()["code"]
    # Second file pushes past the 100-byte total.
    r = client.post(
        f"/api/share/{code}",
        data={"meta": json.dumps({"ttl": "1h"}), "b.bin": (io.BytesIO(b"b" * 60), "b.bin")},
        content_type="multipart/form-data",
    )
    assert r.status_code in (400, 413)
    # Share still healthy, nothing half-written.
    assert len(client.get(f"/api/share/{code}").get_json()["items"]) == 1


def test_text_cap(client):
    huge = "x" * 600_000
    r = _make_share(client, items=[{"type": "text", "name": "huge.txt", "content": huge}])
    assert r.status_code == 413


def test_rate_limiting_on_lookups(tmp_path):
    cfg = Config(data_dir=tmp_path / "data", lookup_limit=2, download_limit=100, create_limit=100)
    app = create_app(cfg)
    app.config["TESTING"] = True
    client = app.test_client()
    code = _make_share(client).get_json()["code"]
    assert client.get(f"/api/share/{code}").status_code == 200
    assert client.get(f"/api/share/{code}").status_code == 200
    assert client.get(f"/api/share/{code}").status_code == 429


def test_cleanup_purge_job(tmp_path):
    cfg = Config(data_dir=tmp_path / "data")
    app = create_app(cfg)
    client = app.test_client()
    code = _make_share(client).get_json()["code"]
    cfg.blob_store.put(f"shares/{code}/stale/file.bin", io.BytesIO(b"stale"))
    assert cfg.meta_store.count() == 1

    _force_expiry(app, code)
    from backend.cleanup import purge_once

    n = purge_once(cfg.meta_store, cfg.blob_store)
    assert n == 1
    assert cfg.meta_store.count() == 0
    assert cfg.blob_store.exists(f"shares/{code}/stale/file.bin") is False

# AnyDevice Architecture

## Repo layout

```
├─ backend/                 Flask REST API
│  ├─ app.py                routes, payload parsing, auth, rate limiting
│  ├─ config.py             ANYDEVICE_* environment-driven configuration
│  ├─ store.py              metadata: shares + items (SQLite)
│  ├─ supabase_store.py     same surface, backed by Supabase Postgres
│  ├─ storage.py            blob persistence (local disk)
│  ├─ supabase_storage.py   blob persistence (Supabase Storage)
│  ├─ crypto.py             AES-GCM at-rest encryption (key held server-side)
│  ├─ limits.py             in-memory sliding-window rate limiter
│  └─ cleanup.py            expired-share purge loop
└─ frontend/                React + Vite + PWA
   ├─ src/components/       AttachPanel, GrabPanel, Portal, CodeBox, ItemsList, …
   ├─ src/lib/              api client, formatting, recent-codes local store
   └─ public/               service worker, manifest, icons
```

## Request flow

1. Sender drops a file or pastes text → `POST /api/share` (multipart or JSON).
2. The server mints a 5-character code, wraps every item with AES-GCM using a
   random per-share key, and stores ciphertext (blobs through the blob store,
   text in the metadata store). Plaintext never touches disk.
3. Sender shares the code; receiver opens `GET /api/share/<code>`, which
   decrypts on read so the API always returns plaintext.
4. Sender polls `GET /api/share/<code>/status` for pickup. Receiver polls
   `GET /api/share/<code>/clipboard` to live-sync newly pasted text items.
5. Items download individually (`/download/<id>`) or zipped (`/download-all`);
   `burn` shares self-destruct once every item is downloaded.
6. Expired codes are purged by the cleanup loop; TTLs are 5m / 1h / 24h.

## Storage backends

`ANYDEVICE_BACKEND=disk` (default) uses SQLite + a local blobs folder — zero setup.
`ANYDEVICE_BACKEND=supabase` uses the project's Postgres database + a private
Storage bucket; blobs are still encrypted at rest before upload.

## Privacy stance

No accounts, no cookies, no tracking, no persistent user data. The store keeps
share codes, per-item metadata, and encrypted content for the TTL window only.
The one aggregate surface is `GET /api/admin/stats`:

- **Operator-only**: requires `X-Admin-Key: <ANYDEVICE_ADMIN_KEY>` (set via env;
  endpoint is disabled with `503` when the key is unset). Requests are
  rate-limited on the same in-memory limiter used by public routes.
- **Non-PII by construction**: the response is plain counts/averages — total
  shares (all-time and last 24h), active (non-expired) shares, burn-mode usage
  percentage, and average share size. It never includes IPs, codes, filenames,
  or any content-identifying data.

## Configuration

Everything reads from `ANYDEVICE_*` / `SUPABASE_*` environment variables
(see `backend/config.py` and `.env.example`).
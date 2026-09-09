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

The operator front-door is a **static shell at `GET /admin`**: it is served
publicly (a browser can't send the `X-Admin-Key` header), but it contains zero
data — the key is entered client-side and kept in `sessionStorage` for the tab
only (never `localStorage`). Every data request from the page goes through the
`X-Admin-Key` gate below.

Behind that gate there are two admin-only API surfaces:

- `GET /api/admin/stats` — **non-PII by construction**: plain counts/averages —
  total shares (all-time + last 24h), active (non-expired) shares, burn-mode
  usage, average share size, and a calendar-based daily breakdown
  (`shares_today`, `shares_yesterday`, `last_7_days`) in UTC. No IPs, codes,
  filenames, or content-identifying data.
- `GET /api/admin/shares` — per-share **metadata only**: transfer codes, item
  names/sizes, download counts, expiry/status, and the burn flag. This exists
  purely so the operator can monitor individual shares; it is intentionally
  richer than `stats` but still bounded by what the system tracks.

Both are:
- **Operator-only**: require `X-Admin-Key: <ANYDEVICE_ADMIN_KEY>` (env-set);
  endpoints are disabled with `503` when the key is unset. Requests are
  rate-limited on the same in-memory limiter used by public routes.
- **Bounded by design**: any device sends/receives via anonymous codes; there is
  no sender/receiver identity, IP, device fingerprint, or user account anywhere
  in the system, so the admin API has nothing like that to return. The dashboard
  UI states this explicitly.

## Configuration

Everything reads from `ANYDEVICE_*` / `SUPABASE_*` environment variables
(see `backend/config.py` and `.env.example`).
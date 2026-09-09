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
One exception exists and is scoped to the operator's monitoring surfaces:

- **creator IP** is stored on the share row at creation time;
- **receiver pickup** — the first time a receiver opens the share
  (`GET /api/share/<code>?mark_viewed=1`), their IP and timestamp are stored once
  (never overwritten by later polls);
- **per-download audit** — each explicit download (single-item or zip) appends a
  `download_log` row with the downloader's IP and time.

None of this is ever exposed through public endpoints: `_share_json` / status /
clipboard responses stay free of IPs, and the data lives only in admin-only
feeds. No receiver identity, device fingerprint, or user accounts are collected.

The operator front-door is a **static shell at `GET /admin`**: it is served
publicly (a browser can't send the `X-Admin-Key` header), but it contains zero
data — the key is entered client-side and kept in `sessionStorage` for the tab
only (never `localStorage`). Every data request from the page goes through the
`X-Admin-Key` gate below.

Behind that gate there are three admin-only API surfaces:

- `GET /api/admin/stats` — **non-PII by construction**: plain counts/averages —
  total shares (all-time + last 24h), active (non-expired) shares, burn-mode
  usage, average share size, and a calendar-based daily breakdown
  (`shares_today`, `shares_yesterday`, `last_7_days`) in UTC. No IPs, codes,
  filenames, or content-identifying data.
- `GET /api/admin/shares` — per-share feed for the dashboard: transfer codes,
  item names/sizes, download counts, expiry/status, burn flag, plus the pickup
  record and live download log for the share. Admin-only.
- `GET /api/admin/history` — **persistent Share History**: when a share expires
  or self-destructs via burn, `store.finalize(code, reason)` atomically snapshots
  its metadata (code, item names/types/sizes, created/expires/ended timestamps,
  final download count, burn flag, creator IP, pickup record, and the full
  download audit) into a `share_history` row keyed on the code — an idempotent
  write, so a crashed/retried purge can never duplicate it — and then deletes the
  share's rows, its download log, and its blobs. Only metadata is retained: no
  item content, no blob keys, no encryption keys, so a history entry can never
  make a deleted share downloadable again. Kept in the same SQLite/Postgres
  database, so it survives backend and Render restarts. `wipe()` clears it too
  (factory reset).

All three surfaces are:
- **Operator-only**: require `X-Admin-Key: <ANYDEVICE_ADMIN_KEY>` (env-set);
  endpoints are disabled with `503` when the key is unset. Requests are
  rate-limited on the same in-memory limiter used by public routes.
- **Bounded by design**: a sender/receiver only exists as anonymous codes — the
  system records IPs (creator, pickup, downloads) and timestamps but no
  identities or device fingerprints, so the admin API has nothing more to
  return. The dashboard UI states this explicitly.

## Configuration

Everything reads from `ANYDEVICE_*` / `SUPABASE_*` environment variables
(see `backend/config.py` and `.env.example`).
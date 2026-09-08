# Product Requirements Document
## AnyDevice Share (working title)

**Stage:** Ideation → MVP planning
**Author:** Sourav Giri
**Date:** September 2026

---

## 1. Problem Statement

Sharing a file between two devices you own — phone to lab PC, PC to phone, phone to phone — currently requires logging into Gmail, WhatsApp Web, or a cloud drive. On shared/public computers (college labs), this is slow and leaves accounts signed in on a machine you don't control.

## 2. Product Vision

A single web app, no accounts, where anyone can drop content on one device and pull it up on any other device using a short code. Symmetric by design: there is no "sender" or "receiver" role, only "have the code" or "don't."

## 3. Target Users

- Primary: College students transferring files between their phone and shared lab computers.
- Secondary: Anyone needing a quick, throwaway transfer between two devices without an account (coworkers, home users, etc).

## 4. Core User Flow

1. User opens the site on Device A.
2. Drops a file/image/document/PDF, or pastes text/code.
3. Gets back a short code + QR immediately — no submit/confirm step needed beyond the drop.
4. User opens the same site on Device B (any device).
5. Enters the code (or scans the QR) — content list appears.
6. Views inline (image/PDF/text preview) or downloads.
7. Content self-destructs after expiry or first full download.

## 5. Functional Requirements

### 5.1 Upload / Share
- Accept: images (jpg/png/webp), documents (pdf, docx), plain text, code snippets.
- Multiple items can be attached to a single code (not one-file-per-code).
- Generate a 5–6 character code, uppercase alphanumeric, excluding ambiguous characters (0/O, 1/I).
- Generate a QR code encoding the retrieval URL with the code pre-filled.
- No login, no CAPTCHA-heavy friction — instant code generation.

### 5.2 Retrieve
- Single input field for code entry.
- On valid code: list all items attached to that code.
- Inline preview:
  - Images → rendered directly.
  - PDFs → embedded viewer.
  - Text/code → `<pre>` block with syntax highlighting and a copy button.
  - Other documents (docx etc.) → download link, preview if feasible.
- Download individual items or all-as-zip.

### 5.3 Single-Page Design
- One page holds both the drop zone and the code-entry field — no separate "sender" vs "receiver" routes, since either action can happen on either device.

### 5.4 Expiry & Cleanup
- Each code has a TTL (default suggestion: 1 hour, user-adjustable at share time: 5 min / 1 hr / 24 hr).
- Optional: expire immediately after first successful download of all items.
- Scheduled cleanup job purges expired records and their storage blobs.

### 5.5 Adding to an Existing Code (optional/stretch)
- Allow appending more items to an already-generated code, so a second device can also drop something back into the same code session.

## 5.6 Visual Design Direction

The product's whole identity is a *physical handoff* — something leaving one device and landing on another. The UI should make that motion tangible: floating, tilted 3D objects (phone, laptop, a glowing code capsule) that feel like they're mid-air, mid-transfer. This is the one bold visual idea; everything else stays quiet around it.

**Color** — dark base so floating 3D elements pop with depth and glow:
- `#0E0B1A` — near-black violet, base background
- `#7B5CFA` — electric violet, primary accent (buttons, the code capsule glow)
- `#3DDC97` — signal green, success/active states (code copied, transfer complete)
- `#FF6B9D` — hot pink, secondary accent used sparingly (hover states, the QR corner)
- `#F5F3FF` — off-white, primary text
- `#8B85A8` — muted lavender-grey, secondary text/labels

**Type** — one geometric sans for everything, two weights doing the work instead of a second family:
- Display/headlines: a rounded-geometric sans (e.g. Cabinet Grotesk or General Sans), bold, tight tracking — friendly but confident, not corporate.
- Body/UI: same family, regular weight, slightly looser line-height for readability on small phone screens.

**Layout concept**
- Hero: a 3D isometric scene — a phone and a laptop angled toward each other with a glowing capsule (the code) suspended between them, mid-flight. This *is* the explanation of the product; no separate "how it works" copy needed above the fold.
- Below the hero: the single drop-zone/code-entry panel sits on a subtly raised 3D card — soft directional shadow, slight tilt on hover, not the flat SaaS-card default.
- One orchestrated motion moment: when a code generates, the capsule visually "launches" from the drop zone toward the QR/code display — this is the one animation budget item, not per-card hover fades.
- Center-aligned hero, left-aligned utility content (file list, forms) for scannability.

**Principles**
- The 3D scene is load-bearing, not decorative — it visually is the product's mental model (device → code → device).
- Keep chrome minimal: no eyebrow labels, no middle-dot meta strings, no generic SaaS card grid.
- Gen-Z-attractive means confident color and motion with real restraint — one glowing accent, one hero moment, not neon everywhere.
- Every empty/error state speaks in the product's voice ("No code yet — drop something above" rather than a generic error box).

## 6. Non-Functional Requirements

- **No accounts, ever** — zero sign-up/sign-in surface anywhere in the product.
- **Security via obscurity + rate limiting**: since the code is the only gate, rate-limit code lookup attempts (e.g. 5/min per IP) to prevent brute-forcing a 6-char code space.
- **HTTPS only.**
- **File size limits**: cap per-file size (e.g. 50MB) and total size per code, to control storage cost and abuse.
- **Ephemeral by default** — nothing should persist longer than necessary; this is the core trust promise of the product.
- **Fast**: code generation and retrieval should feel instant (<1s round trip on a normal connection).
- **Mobile-first UI**: primary use case starts on a phone.

## 7. Tech Stack (proposed)

| Layer | Choice |
|---|---|
| Frontend | React + Vite + Tailwind |
| Backend | Flask (REST API) |
| Storage (files) | Firebase Storage |
| Database (metadata) | Firebase Firestore |
| Cleanup job | Firebase Cloud Function (scheduled) or Render cron job |
| Frontend hosting | Vercel |
| Backend hosting | Render |
| QR generation | client-side JS lib (e.g. `qrcode`) |
| Syntax highlighting | highlight.js or Prism |
| PDF preview | `react-pdf` or native `<iframe>` |

### 7.1 Why Firebase over Supabase

Supabase's free tier auto-pauses a project after 7 days of inactivity, requiring a manual dashboard login to resume — a bad fit for a side project that isn't touched daily. Alternatives considered:

| Option | Verdict |
|---|---|
| **Firebase (Storage + Firestore)** | **Chosen.** No inactivity pause on the free (Spark) plan; already used on other projects, so no new learning curve. |
| Neon (Postgres) | Rejected — free tier "autosuspends" and wakes on request (no hard pause), but adds a new DB paradigm with no real benefit here. |
| Cloudflare R2 + D1 | Rejected — no pause, but more setup overhead for a project this size. |
| MongoDB Atlas (M0) | Rejected — no auto-pause, but 512MB cap and adds a new DB to learn. |

## 8. Data Model (draft — Firestore)

**`shares` collection** (doc ID = the code itself)
| Field | Type | Notes |
|---|---|---|
| code | string | 5–6 char, also the doc ID |
| created_at | timestamp | |
| expires_at | timestamp | derived from user-chosen TTL |
| download_count | number | optional, for "expire after N downloads" |

**`share_items` subcollection** (under each `shares/{code}` doc)
| Field | Type | Notes |
|---|---|---|
| id | string (auto ID) | |
| type | enum: file / text / code_snippet | |
| storage_url | string | Firebase Storage download URL; null if type = text/code_snippet |
| content | string | null if type = file |
| filename | string | null if type = text/code_snippet |
| mime_type | string | |
| created_at | timestamp | |

Files themselves live in **Firebase Storage** under a path like `shares/{code}/{item_id}/{filename}`, so cleanup can delete the whole `shares/{code}/` folder in one call when a code expires.

## 9. API Endpoints (draft)

- `POST /api/share` — create a new code, attach initial item(s).
- `POST /api/share/<code>` — append item(s) to an existing, non-expired code.
- `GET /api/share/<code>` — fetch metadata + item list for a code.
- `GET /api/share/<code>/download/<item_id>` — stream a single file.
- `GET /api/share/<code>/download-all` — zip and stream all files.
- Internal: scheduled cleanup task, not user-facing.

## 10. Out of Scope (for MVP)

- User accounts / persistent history of past shares.
- Real-time collaborative editing.
- End-to-end encryption (can be a v2 addition, similar pattern to client-side encrypted key-in-URL approach).
- Cross-code messaging or chat.

## 11. Success Metrics (MVP)

- Time from "drop file" to "usable code" — target under 3 seconds.
- Time from "enter code" to "file visible" — target under 2 seconds.
- Zero required user input beyond drop + code entry (no forms, no email, no OTP).

## 12. Milestones

1. **Week 1–2**: Backend API (upload, code generation, retrieval, download) + Supabase schema.
2. **Week 3**: Frontend single-page UI (drop zone + code entry + previews).
3. **Week 4**: QR code integration, expiry logic, cleanup job.
4. **Week 5**: Rate limiting, file size limits, polish, mobile responsiveness pass.
5. **Week 6**: Deploy (Vercel + Render), test across real devices (phone ↔ lab PC), beta with a few classmates.

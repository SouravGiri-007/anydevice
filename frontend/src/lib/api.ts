export type TtlKey = "5m" | "1h" | "24h";
export type ShareStatus = "pending" | "viewed";

export interface ShareItem {
  id: string;
  type: "file" | "text";
  name: string;
  mime: string;
  size: number;
  downloaded: boolean;
  content: string | null;
}

export interface Share {
  code: string;
  created_at: number;
  expires_at: number;
  expires_in: number;
  ttl_key: TtlKey;
  ttl_seconds: number;
  burn: boolean;
  status: ShareStatus;
  enc: boolean; // always false — content is encrypted at rest by the server
  bytes_total: number;
  items: ShareItem[];
}

export interface ShareStatusResponse {
  code: string;
  status: ShareStatus;
  expires_in: number;
}

export interface ClipboardResponse {
  code: string;
  items: ShareItem[];
}

export interface Snippet {
  name: string;
  content: string;
}

export class ApiError extends Error {
  status: number;
  errCode?: string;
  constructor(status: number, message: string, errCode?: string) {
    super(message);
    this.status = status;
    this.errCode = errCode;
  }
}

const TTL_OPTIONS: Record<TtlKey, string> = { "5m": "5 min", "1h": "1 hour", "24h": "24 hours" };
export { TTL_OPTIONS };

// API base. In dev the Vite proxy serves /api from the local backend; in a
// production build, VITE_API_BASE points at the deployed backend (e.g. Render).
const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

function endpoint(path: string) {
  return `${API_BASE}/api${path}`;
}

async function request(path: string, init?: RequestInit): Promise<any> {
  let res: Response;
  try {
    res = await fetch(endpoint(path), init);
  } catch {
    throw new ApiError(0, "Can't reach the AnyDevice server. Is the backend running?");
  }
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) {
    const message =
      (body && body.error) ||
      (res.status === 429 ? "Too many tries — wait a minute." : `Request failed (${res.status}).`);
    throw new ApiError(res.status, message, body && body.code);
  }
  return body;
}

function payload(
  ttl: TtlKey,
  burn: boolean,
  files: File[],
  texts: Snippet[]
): FormData {
  const meta = { ttl, burn };
  const fd = new FormData();
  fd.append("meta", JSON.stringify(meta));
  if (texts.length) {
    fd.append(
      "text_items",
      JSON.stringify(
        texts.map((t) => ({ type: "text", name: t.name || "note.txt", content: t.content }))
      )
    );
  }
  for (const f of files) fd.append("files", f, f.name);
  return fd;
}

export function createShare(
  files: File[],
  texts: Snippet[],
  ttl: TtlKey,
  burn: boolean
): Promise<Share> {
  return request("/share", {
    method: "POST",
    body: payload(ttl, burn, files, texts),
  });
}

export function appendShare(
  code: string,
  files: File[],
  texts: Snippet[]
): Promise<Share> {
  return request(`/share/${code}`, {
    method: "POST",
    body: payload("1h", false, files, texts), // ttl/burn ignored server-side on append
  });
}

/**
 * Fetch a share. `markViewed` mirrors the receiver opening the code: the server
 * flips pending → viewed so the sender's page can show "picked up". The sender's
 * own status polling goes through `fetchShareStatus` and never marks.
 */
export function fetchShare(code: string, markViewed = true): Promise<Share> {
  return request(`/share/${code}${markViewed ? "?mark_viewed=1" : ""}`);
}

/** Lightweight, unmarked poll for the sender's "waiting for pickup" state. */
export function fetchShareStatus(code: string): Promise<ShareStatusResponse> {
  return request(`/share/${code}/status`);
}

/** Live clipboard sync: all text items inline for the receiving device. */
export function fetchClipboard(code: string): Promise<ClipboardResponse> {
  return request(`/share/${code}/clipboard`);
}

export function itemUrl(code: string, itemId: string, inline = false): string {
  return endpoint(`/share/${code}/download/${itemId}${inline ? "?inline=1" : ""}`);
}

export function downloadAllUrl(code: string): string {
  return endpoint(`/share/${code}/download-all`);
}

/** Full retrieval link — the code alone is enough (server holds the key). */
export function shareLink(code: string): string {
  const url = new URL(window.location.href);
  url.search = "";
  url.hash = "";
  url.searchParams.set("c", code);
  return url.toString();
}

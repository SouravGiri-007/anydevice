import type { Share } from "./api";

/**
 * "Recent codes on this device" lives entirely in localStorage — no account, no
 * server-side association. Server holds the decryption key, so the client only
 * needs the code to reopen a share.
 */
export interface RecentEntry {
  code: string;
  label: string;
  createdAt: number; // epoch ms when this device first saw the code
  expiresAt?: number; // epoch ms, when known; used to prune without a server call
  viewed?: boolean; // pickup status at last check
}

const STORAGE_KEY = "anydevice:recent";

const MAX_ENTRIES = 5;

export function loadRecent(): RecentEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((e) => e && typeof e.code === "string");
  } catch {
    return [];
  }
}

function save(list: RecentEntry[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list.slice(0, MAX_ENTRIES)));
  } catch {
    /* storage full / unavailable — degrade silently */
  }
}

export function rememberShare(share: Share) {
  const list = loadRecent();
  const label = share.items[0]?.name?.trim() || `${share.items.length} item${share.items.length === 1 ? "" : "s"}`;
  const existing = list.find((e) => e.code === share.code);
  const entry: RecentEntry = {
    code: share.code,
    label,
    createdAt: existing?.createdAt ?? Date.now(),
    expiresAt: share.expires_at * 1000,
    viewed: share.status === "viewed",
  };
  save([entry, ...list.filter((e) => e.code !== share.code)]);
}

export function forgetCode(code: string) {
  save(loadRecent().filter((e) => e.code !== code));
}

/** Drop entries the server confirmed dead (also purges their keys). */
export function pruneRecent(codes: string[]) {
  if (!codes.length) return;
  save(loadRecent().filter((e) => !codes.includes(e.code)));
}

/** Persist fresh status/expiry facts after a recheck. */
export function touchRecent(code: string, patch: Partial<RecentEntry>) {
  const list = loadRecent();
  const entry = list.find((e) => e.code === code);
  if (!entry) return;
  Object.assign(entry, patch);
  save(list);
}

export function formatAgo(epochMs: number): string {
  const s = Math.max(0, (Date.now() - epochMs) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

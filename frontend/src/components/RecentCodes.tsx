import { useEffect, useState } from "react";
import { ApiError, fetchShareStatus } from "../lib/api";
import {
  formatAgo,
  forgetCode,
  loadRecent,
  pruneRecent,
  touchRecent,
  type RecentEntry,
} from "../lib/recent";

/**
 * Homepage strip of the last few codes this device generated or opened.
 * Nothing is trusted blindly: every entry is re-checked against /status on
 * mount, and codes the server says are gone (or that have expired locally) are
 * pruned automatically — along with their cached decryption keys.
 */
export default function RecentCodes({ onOpen }: { onOpen: (code: string) => void }) {
  const [entries, setEntries] = useState<RecentEntry[] | null>(null);

  useEffect(() => {
    let stopped = false;
    const raw = loadRecent();

    async function recheck() {
      const alive: RecentEntry[] = [];
      const dead: string[] = [];
      for (const entry of raw) {
        if (entry.expiresAt && entry.expiresAt <= Date.now()) {
          dead.push(entry.code);
          continue;
        }
        try {
          const st = await fetchShareStatus(entry.code);
          if (stopped) return;
          touchRecent(entry.code, {
            expiresAt: Date.now() + st.expires_in * 1000,
            viewed: st.status === "viewed",
          });
          alive.push({ ...entry, expiresAt: Date.now() + st.expires_in * 1000, viewed: st.status === "viewed" });
        } catch (e) {
          if (e instanceof ApiError && (e.errCode === "not_found" || e.errCode === "expired")) {
            dead.push(entry.code);
          } else if (!stopped) {
            // Offline / transient — keep the entry but don't trust it blindly.
            alive.push(entry);
          }
        }
      }
      pruneRecent(dead);
      if (!stopped) setEntries(alive);
    }

    recheck();
    return () => {
      stopped = true;
    };
  }, []);

  if (!entries) return null; // still checking
  if (!entries.length) return null;

  return (
    <div className="mx-auto mt-6 w-full max-w-md">
      <p className="text-[11px] font-medium tracking-[0.18em] text-mute uppercase">
        recent codes on this device
      </p>
      <ul className="mt-2 space-y-1.5">
        {entries.map((entry) => (
          <li key={entry.code}>
            <button
              onClick={() => onOpen(entry.code)}
              className="group flex w-full items-center gap-3 rounded-xl border border-transparent px-3 py-2 text-left transition hover:border-edge hover:bg-panel/60"
            >
              <span className="font-mono text-sm font-bold tracking-widest text-violets group-hover:text-ink">
                {entry.code}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs text-ink/90">{entry.label}</span>
                <span className="block text-[11px] text-mute">
                  {formatAgo(entry.createdAt)}
                  {entry.viewed ? " · opened" : ""}
                </span>
              </span>
              <span
                className="text-[11px] text-mute/60 opacity-0 transition group-hover:opacity-100"
                onClick={(e) => {
                  e.stopPropagation();
                  forgetCode(entry.code);
                  setEntries((prev) => prev?.filter((x) => x.code !== entry.code) ?? prev);
                }}
                role="button"
                aria-label={`Remove ${entry.code} from recent codes`}
              >
                ✕
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

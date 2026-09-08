import { useCallback, useEffect, useState } from "react";
import QRCode from "qrcode";
import { shareLink, type Share } from "../lib/api";
import { formatCountdown } from "../lib/format";
import { useRemainingMs } from "../lib/useTicker";
import { CopyIcon } from "./icons";

export default function CodeBox({
  share,
  mode,
  onExpired,
  onDiscard,
}: {
  share: Share;
  mode: "mine" | "theirs";
  onExpired: () => void;
  onDiscard: () => void;
}) {
  const [qr, setQr] = useState("");
  const [copied, setCopied] = useState(false);
  const remainingMs = useRemainingMs(share.expires_at * 1000);
  const expired = remainingMs <= 0;

  useEffect(() => {
    if (expired) onExpired();
  }, [expired, onExpired]);

  const link = shareLink(share.code);

  useEffect(() => {
    let alive = true;
    QRCode.toDataURL(link, {
      width: 132,
      margin: 1,
      color: { dark: "#0a0a12", light: "#e5e3f0" },
    })
      .then((url) => alive && setQr(url))
      .catch(() => setQr(""));
    return () => {
      alive = false;
    };
  }, [link]);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(share.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard unavailable */
    }
  }, [share.code]);

  if (expired) return null;

  return (
    <div className="flex flex-col items-center gap-3">
      <p className="text-[11px] font-medium tracking-[0.22em] text-mute uppercase">
        {mode === "mine" ? "your code — receive it anywhere" : "received — this code is open"}
      </p>

      <button
        onClick={copy}
        title="Copy code"
        className="group relative focus:outline-none"
      >
        <span
          className="block text-6xl font-bold tracking-[0.18em] text-ink transition group-hover:text-violets sm:text-7xl"
          style={{ textShadow: "0 0 34px rgba(159,122,234,0.55)" }}
        >
          {share.code}
        </span>
      </button>

      <div className="flex flex-wrap items-center justify-center gap-2">
        <button
          onClick={copy}
          className={`inline-flex items-center gap-1.5 rounded-full border px-4 py-1.5 text-xs font-semibold transition ${
            copied
              ? "border-mint/70 text-mint"
              : "border-edge text-mute hover:border-violets hover:text-ink"
          }`}
        >
          <CopyIcon className="h-3.5 w-3.5" />
          {copied ? "copied" : "copy code"}
        </button>
        <button
          onClick={() => {
            navigator.clipboard?.writeText(link).catch(() => {});
          }}
          className="rounded-full border border-edge px-4 py-1.5 text-xs font-semibold text-mute transition hover:border-violets hover:text-ink"
        >
          copy link
        </button>
        <button
          onClick={onDiscard}
          className="rounded-full border border-transparent px-3 py-1.5 text-xs text-mute/80 transition hover:text-ink"
        >
          {mode === "mine" ? "scrap" : "close"}
        </button>
      </div>

      {/* Live pickup status — sender's screen only */}
      {mode === "mine" && (
        <p
          className={`flex items-center gap-2 text-xs font-medium ${
            share.status === "viewed" ? "text-mint" : "text-violets"
          }`}
          role="status"
        >
          {share.status === "viewed" ? (
            <>
              <span className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-mint/60">
                ✓
              </span>
              picked up on the other device
            </>
          ) : (
            <>
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-violet" />
              waiting for pickup…
            </>
          )}
        </p>
      )}

      <div className="flex flex-col items-center gap-2.5">
        {qr ? (
          <img
            src={qr}
            alt={`QR code for share ${share.code}`}
            className="h-[116px] w-[116px] rounded-lg bg-ink p-1.5"
          />
        ) : (
          <div className="h-[116px] w-[116px] animate-pulse rounded-lg bg-panel2" />
        )}
        <p className="text-[11px] text-mute">
          scan with the other device
          {share.burn && <span className="text-violets"> · vanishes after download</span>}
        </p>
      </div>

      <p
        className={`text-xs font-medium ${
          remainingMs < 60_000 ? "text-ink" : "text-mute"
        }`}
      >
        {formatCountdown(remainingMs / 1000)} left
      </p>
    </div>
  );
}

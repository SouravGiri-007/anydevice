import { useCallback, useMemo, useState } from "react";
import hljs from "highlight.js/lib/common";
import {
  downloadAllUrl,
  itemUrl,
  type Share,
  type ShareItem,
} from "../lib/api";
import { formatBytes, formatCountdown, isImage, isPdf, languageFor } from "../lib/format";
import { useRemainingMs } from "../lib/useTicker";
import { DownloadIcon } from "./icons";

function badge(item: ShareItem): { text: string; cls: string } {
  const name = item.name.toLowerCase();
  if (item.type === "text") return { text: "TXT", cls: "text-violets border-violets/40 bg-violets/10" };
  if (item.mime.startsWith("image/") || /\.(png|jpe?g|webp|gif|avif)$/.test(name))
    return { text: "IMG", cls: "text-violets border-violets/40 bg-violets/10" };
  if (isPdf(item.mime, item.name)) return { text: "PDF", cls: "text-violets border-violets/40 bg-violets/10" };
  if (/\.(docx?|xlsx?|pptx?)$/.test(name)) return { text: name.split(".").pop()!.toUpperCase(), cls: "text-violets border-violets/40 bg-violets/10" };
  return { text: "FILE", cls: "text-mute border-edge bg-panel2" };
}

function previewable(item: ShareItem): boolean {
  if (item.type === "text") return true;
  return isImage(item.mime, item.name) || isPdf(item.mime, item.name);
}

export default function ItemsList({
  share,
  onBurnt,
}: {
  share: Share;
  onBurnt?: () => void;
}) {
  const remainingMs = useRemainingMs(share.expires_at * 1000);

  const downloadItem = useCallback(
    (item: ShareItem) => {
      const a = document.createElement("a");
      a.href = itemUrl(share.code, item.id);
      a.download = item.name;
      document.body.appendChild(a);
      a.click();
      a.remove();
    },
    [share.code]
  );

  const downloadAll = useCallback(() => {
    const a = document.createElement("a");
    a.href = downloadAllUrl(share.code);
    a.download = `${share.code}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    if (share.burn) window.setTimeout(() => onBurnt?.(), 1500);
  }, [share.code, share.burn, onBurnt]);

  if (!share.items.length) {
    return (
      <p className="py-6 text-center text-sm text-mute">
        Nothing riding under this code yet — drop something above.
      </p>
    );
  }

  return (
    <section className="fade-up space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-mute">
          <span className="font-semibold text-ink">{share.items.length}</span> item
          {share.items.length === 1 ? "" : "s"} · {formatBytes(share.bytes_total)}
          {share.burn && <span className="ml-2 text-violets">· downloads once, gone after</span>}
        </p>
        <span className="text-xs text-mute">{formatCountdown(remainingMs / 1000)} left</span>
      </div>

      <ul className="space-y-2">
        {share.items.map((item) => (
          <Row
            key={item.id}
            share={share}
            item={item}
            fileUrl={itemUrl(share.code, item.id, true)}
            onDownload={() => downloadItem(item)}
            onBurnt={onBurnt}
          />
        ))}
      </ul>

      {share.items.length > 1 && (
        <button
          onClick={downloadAll}
          className="flex w-full items-center justify-center gap-2 rounded-2xl bg-violet py-3 text-sm font-bold text-[#0a0a12] shadow-[0_0_24px_-6px_rgba(159,122,234,0.9)] transition hover:bg-violets"
        >
          <DownloadIcon className="h-4 w-4" />
          download everything as .zip
          {share.burn && <span className="ml-1 font-normal opacity-80">(this ends the code)</span>}
        </button>
      )}
    </section>
  );
}

function Row({
  share,
  item,
  fileUrl,
  onDownload,
  onBurnt,
}: {
  share: Share;
  item: ShareItem;
  fileUrl: string;
  onDownload: () => void;
  onBurnt?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const tag = badge(item);
  // Downloading this item completes the set → a burn-share self-destructs.
  const willBurn = share.burn && share.items.every((i) => i.id === item.id || i.downloaded);
  const canPreview = previewable(item);
  const isText = item.type === "text";

  return (
    <li className="overflow-hidden rounded-2xl border border-edge bg-panel2/50">
      <div className="flex items-center gap-3 px-4 py-3">
        <span
          className={`flex h-9 w-11 shrink-0 items-center justify-center rounded-lg border text-[10px] font-bold ${tag.cls}`}
        >
          {tag.text}
        </span>
        <button
          type="button"
          onClick={() => !isText && canPreview && setOpen(!open)}
          disabled={!canPreview || isText}
          className={`min-w-0 flex-1 text-left ${canPreview ? "cursor-pointer" : "cursor-default"}`}
          title={isText ? undefined : canPreview ? (open ? "collapse" : "preview") : undefined}
        >
          <span className="block truncate text-sm font-semibold text-ink">{item.name}</span>
          <span className="block text-xs text-mute">
            {formatBytes(item.size)}
            {item.downloaded ? " · downloaded" : ""}
          </span>
        </button>

        <div className="flex shrink-0 items-center gap-2">
          <button
            onClick={() => {
              if (willBurn) window.setTimeout(() => onBurnt?.(), 2500);
              onDownload();
            }}
            className="flex h-8 w-9 items-center justify-center rounded-full border border-edge text-mute transition hover:border-violets hover:text-ink"
            title="Download"
          >
            <DownloadIcon className="h-4 w-4" />
          </button>
        </div>
      </div>

      {isText ? (
        <div className="px-4 pb-4">
          <TextPreview item={item} text={item.content ?? ""} />
        </div>
      ) : (
        (open && (
          <div className="fade-up px-4 pb-4">
            {isImage(item.mime, item.name) ? (
              <a href={fileUrl} target="_blank" rel="noreferrer">
                <img src={fileUrl} alt={item.name} className="max-h-[420px] rounded-xl border border-edge object-contain" />
              </a>
            ) : isPdf(item.mime, item.name) ? (
              <div>
                <iframe src={fileUrl} title={item.name} className="h-[460px] w-full rounded-xl border border-edge bg-panel2/60" />
                <button onClick={onDownload} className="mt-2 text-xs text-violets hover:underline">
                  download instead ↗
                </button>
              </div>
            ) : (
              <button onClick={onDownload} className="text-xs text-violets hover:underline">
                download this file
              </button>
            )}
          </div>
        ))
      )}
    </li>
  );
}

function TextPreview({ item, text }: { item: ShareItem; text: string }) {
  const html = useMemo(() => {
    const lang = languageFor(item.name);
    try {
      return hljs.highlight(text, { language: lang || "plaintext", ignoreIllegals: true }).value;
    } catch {
      return hljs.highlight(text, { language: "plaintext" }).value;
    }
  }, [text, item.name]);
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="rounded-xl border border-edge bg-bg/70">
      <div className="flex items-center justify-between gap-2 border-b border-edge/70 px-3 py-2">
        <span className="font-mono text-[11px] text-mute">{item.name}</span>
        <button
          onClick={copy}
          className={`rounded-md border px-2.5 py-1 text-[11px] font-semibold ${
            copied ? "border-mint/60 text-mint" : "border-edge text-mute hover:text-ink"
          }`}
        >
          {copied ? "✓ copied" : "copy"}
        </button>
      </div>
      <pre className="thin-scroll max-h-96 overflow-auto p-3 text-[13px] leading-relaxed">
        <code className="hljs" dangerouslySetInnerHTML={{ __html: html }} />
      </pre>
    </div>
  );
}
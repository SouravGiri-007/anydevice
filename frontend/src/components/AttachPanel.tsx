import { useRef, useState } from "react";
import { TTL_OPTIONS, type Snippet, type TtlKey } from "../lib/api";
import { UploadIcon } from "./icons";

interface Props {
  busy: boolean;
  ttl: TtlKey;
  burn: boolean;
  canConfigure: boolean;
  activeCode: string | null;
  onAttach: (files: File[], texts: Snippet[]) => void;
  onTtlChange: (ttl: TtlKey) => void;
  onBurnChange: (burn: boolean) => void;
}

/**
 * Two appearances, one behavior:
 *  - hero (activeCode = null): the bare prompt sitting in front of the portal;
 *    every drop/paste sends immediately and opens a new code.
 *  - slim (activeCode set): a quiet "add more" strip under an open share that
 *    appends to the same code.
 */
export default function AttachPanel({
  busy,
  ttl,
  burn,
  canConfigure,
  activeCode,
  onAttach,
  onTtlChange,
  onBurnChange,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [snippet, setSnippet] = useState("");
  const [snippetName, setSnippetName] = useState("");
  const slim = !!activeCode;

  function handleFiles(list: FileList | File[]) {
    const files = Array.from(list);
    if (files.length && !busy) onAttach(files, []);
  }

  function sendSnippet() {
    const content = snippet.replace(/\s+$/, "");
    if (!content || busy) return;
    onAttach([], [{ name: snippetName.trim(), content }]);
    setSnippet("");
    setSnippetName("");
    setPasteOpen(false);
  }

  // ---- hero appearance ---------------------------------------------------
  if (!slim) {
    return (
      <div className="flex w-full flex-col items-center gap-3 text-center">
        <input
          ref={inputRef}
          id="portal-file"
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) handleFiles(e.target.files);
            e.target.value = "";
          }}
        />

        {busy ? (
          <p className="flex items-center gap-2.5 text-base text-ink">
            <span className="spin h-4 w-4 rounded-full border border-edge border-t-violet" />
            opening a portal…
          </p>
        ) : (
          <>
            <p className="text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
              Drop something to share
            </p>
            <div className="flex items-center gap-3 text-sm">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  inputRef.current?.click();
                }}
                className="inline-flex items-center gap-1.5 rounded-full bg-violet px-4 py-2 font-semibold text-[#0a0a12] transition hover:bg-violets hover:text-[#0a0a12]"
              >
                <UploadIcon className="h-4 w-4" />
                choose a file
              </button>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setPasteOpen((v) => !v);
                }}
                className="text-violets underline-offset-4 hover:underline"
              >
                or paste text
              </button>
            </div>
          </>
        )}

        {pasteOpen && !busy && (
          <div
            className="fade-up w-full max-w-md rounded-xl border border-edge bg-panel/80 p-3 text-left"
            onClick={(e) => e.stopPropagation()}
          >
            <textarea
              value={snippet}
              onChange={(e) => setSnippet(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  sendSnippet();
                }
              }}
              placeholder="Paste notes, code, a config… (enter sends)"
              rows={3}
              autoFocus
              className="w-full resize-y rounded-lg bg-transparent text-sm text-ink placeholder:text-mute/70 focus:outline-none"
            />
            <div className="flex flex-wrap items-center gap-2">
              <input
                value={snippetName}
                onChange={(e) => setSnippetName(e.target.value)}
                placeholder="name (optional)"
                className="min-w-0 flex-1 rounded-lg border border-edge bg-panel2/70 px-3 py-1.5 text-xs text-ink placeholder:text-mute/60 focus:border-violets focus:outline-none"
              />
              <button
                type="button"
                onClick={sendSnippet}
                disabled={!snippet.trim()}
                className="rounded-lg bg-violet px-4 py-1.5 text-xs font-semibold text-[#0a0a12] transition hover:bg-violets disabled:cursor-not-allowed disabled:opacity-40"
              >
                send
              </button>
            </div>
          </div>
        )}

        {canConfigure && (
          <div
            className="mt-1 flex flex-wrap items-center justify-center gap-x-4 gap-y-2 text-xs text-mute"
            onClick={(e) => e.stopPropagation()}
          >
            <span className="flex items-center gap-1.5">
              dies in
              {(Object.keys(TTL_OPTIONS) as TtlKey[]).map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => onTtlChange(key)}
                  className={`chip ${ttl === key ? "on" : ""}`}
                >
                  {TTL_OPTIONS[key]}
                </button>
              ))}
            </span>
            <span className="flex items-center gap-2">
              <span
                role="switch"
                aria-checked={burn}
                tabIndex={0}
                onClick={() => onBurnChange(!burn)}
                onKeyDown={(e) => {
                  if (e.key === " " || e.key === "Enter") {
                    e.preventDefault();
                    onBurnChange(!burn);
                  }
                }}
                className={`switch ${burn ? "on" : ""}`}
              />
              delete after download
            </span>
          </div>
        )}
      </div>
    );
  }

  // ---- slim "add more" appearance ----------------------------------------
  return (
    <div
      className={`panel3d relative overflow-hidden p-4 ${busy ? "opacity-70" : ""}`}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        handleFiles(e.dataTransfer.files);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files) handleFiles(e.target.files);
          e.target.value = "";
        }}
      />
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <p className="min-w-0 flex-1 text-sm text-ink">
          Drop more on <span className="font-bold tracking-wide text-violets">{activeCode}</span>
          <span className="block text-xs font-normal text-mute">
            it appears on every device holding the code
          </span>
        </p>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-edge px-3 py-2 text-xs font-semibold text-ink transition hover:border-violets"
          >
            <UploadIcon className="h-3.5 w-3.5 text-violets" />
            {busy ? "sending…" : "add files"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => setPasteOpen((v) => !v)}
            className="rounded-lg border border-edge px-3 py-2 text-xs font-semibold text-mute transition hover:border-violets hover:text-ink"
          >
            {pasteOpen ? "hide" : "paste text"}
          </button>
        </div>
      </div>

      {pasteOpen && !busy && (
        <div className="mt-3 rounded-lg border border-edge bg-bg/50 p-2.5">
          <textarea
            value={snippet}
            onChange={(e) => setSnippet(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendSnippet();
              }
            }}
            placeholder="Paste text or code… (enter sends)"
            rows={3}
            className="w-full resize-y rounded bg-transparent text-sm text-ink placeholder:text-mute/70 focus:outline-none"
          />
          <div className="flex items-center justify-end gap-2">
            <input
              value={snippetName}
              onChange={(e) => setSnippetName(e.target.value)}
              placeholder="name (optional)"
              className="min-w-0 flex-1 rounded-lg border border-edge bg-panel2/70 px-3 py-1.5 text-xs text-ink placeholder:text-mute/60 focus:border-violets focus:outline-none"
            />
            <button
              type="button"
              disabled={!snippet.trim()}
              onClick={sendSnippet}
              className="rounded-lg bg-violet px-4 py-1.5 text-xs font-semibold text-[#0a0a12] transition hover:bg-violets disabled:opacity-40"
            >
              send
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

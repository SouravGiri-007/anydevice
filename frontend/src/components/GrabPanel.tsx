import { useState } from "react";

interface Props {
  busy: boolean;
  error: string | null;
  onOpen: (code: string) => void;
  onClearError: () => void;
}

export default function GrabPanel({ busy, error, onOpen, onClearError }: Props) {
  const [raw, setRaw] = useState("");

  function submit() {
    const code = raw.trim().toUpperCase();
    if (code) {
      onOpen(code);
      setRaw("");
    }
  }

  return (
    <div className="flex flex-col items-center gap-2 py-2 text-center">
      <label htmlFor="code-entry" className="text-sm text-mute">
        Have a code from another device?
      </label>
      <div className="flex w-full max-w-xs gap-1.5">
        <input
          id="code-entry"
          value={raw}
          onChange={(e) =>
            setRaw(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 6))
          }
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="enter code to receive"
          autoCapitalize="characters"
          autoCorrect="off"
          spellCheck={false}
          disabled={busy}
          className="code-input min-w-0 flex-1 rounded-xl border border-edge bg-panel/60 px-3 py-2.5 text-center text-ink placeholder:text-mute/50 focus:border-violets focus:outline-none disabled:opacity-60"
        />
        <button
          onClick={submit}
          disabled={!raw.trim() || busy}
          className="shrink-0 rounded-xl bg-violet px-4 text-sm font-semibold text-[#0a0a12] transition hover:bg-violets disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? "…" : "receive"}
        </button>
      </div>
      {error && (
        <p className="fade-up max-w-sm text-xs text-ink" role="alert">
          {error}{" "}
          <button onClick={onClearError} className="text-mute underline underline-offset-2">
            ok
          </button>
        </p>
      )}
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import AttachPanel from "./components/AttachPanel";
import CodeBox from "./components/CodeBox";
import SolarSystem from "./components/SolarSystem";
import GrabPanel from "./components/GrabPanel";
import GithubBadge from "./components/GithubBadge";
import { StarIcon } from "./components/icons";
import ItemsList from "./components/ItemsList";
import Portal from "./components/Portal";
import RecentCodes from "./components/RecentCodes";
import {
  ApiError,
  appendShare,
  createShare,
  fetchClipboard,
  fetchShare,
  fetchShareStatus,
  type Share,
  type Snippet,
  type TtlKey,
} from "./lib/api";
import { rememberShare } from "./lib/recent";
import { useTicker } from "./lib/useTicker";

interface ActiveShare {
  data: Share;
  mode: "mine" | "theirs";
}

interface ToastState {
  id: number;
  text: string;
  kind: "ok" | "error";
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function errMessage(e: unknown, fallback: string): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return fallback;
}

function friendlyOpenError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.errCode === "not_found") return "Nothing came through that code — it may already be gone.";
    if (e.errCode === "expired") return "That portal closed — codes die when their timer runs out.";
    if (e.errCode === "rate_limited" || e.status === 429)
      return "Too many tries in a row — wait a minute, then try again.";
    return e.message;
  }
  return e instanceof Error ? e.message : "Something went wrong opening that code.";
}

function Toast({ toast }: { toast: ToastState }) {
  const tone = toast.kind === "ok" ? "text-mint" : "text-ink";
  return (
    <div className="fixed inset-x-0 bottom-6 z-50 flex justify-center px-4">
      <div className={`panel3d max-w-md rounded-xl px-5 py-3 text-sm font-medium ${tone}`} role="status">
        {toast.text}
      </div>
    </div>
  );
}

export default function App() {
  const [ttl, setTtl] = useState<TtlKey>("1h");
  const [burn, setBurn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [absorbing, setAbsorbing] = useState(false);
  const [stageOver, setStageOver] = useState(false);
  const [share, setShare] = useState<ActiveShare | null>(null);
  const [grabbing, setGrabbing] = useState(false);
  const [grabError, setGrabError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const toastId = useRef(0);
  const resultsRef = useRef<HTMLDivElement>(null);
  const now = useTicker();

  const notify = useCallback((text: string, kind: ToastState["kind"] = "ok") => {
    toastId.current += 1;
    setToast({ id: toastId.current, text, kind });
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 4500);
    return () => window.clearTimeout(t);
  }, [toast]);

  useEffect(() => {
    if (share && now > share.data.expires_at * 1000) {
      setShare(null);
      setGrabError(null);
      notify(`Code ${share.data.code} closed — poof.`, "ok");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [share, now]);

  // Keep the device-local "recent codes" store in sync.
  useEffect(() => {
    if (share) rememberShare(share.data);
  }, [share]);

  // Live pickup status: poll the cheap /status endpoint while the sender's
  // code is still `pending`; flip to "picked up ✓" when a receiver opens it.
  useEffect(() => {
    const s = share;
    if (!s || s.mode !== "mine" || s.data.status === "viewed") return;
    let stopped = false;
    const tick = async () => {
      try {
        const st = await fetchShareStatus(s.data.code);
        if (stopped) return;
        if (st.status === "viewed") {
          setShare((prev) =>
            prev && prev.data.code === s.data.code
              ? { ...prev, data: { ...prev.data, status: "viewed" } }
              : prev
          );
          notify(`Code ${s.data.code} was picked up ✓`, "ok");
        }
      } catch (e) {
        if (stopped) return;
        if (e instanceof ApiError && (e.errCode === "not_found" || e.errCode === "expired")) {
          setShare(null);
          notify(`Code ${s.data.code} closed — poof.`, "ok");
        }
      }
    };
    tick();
    const id = window.setInterval(tick, 2000);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [share, notify]);

  // Live clipboard sync: while receiving, poll the clipboard endpoint so new
  // text pastes from the paired device show up inline without a refresh.
  useEffect(() => {
    const s = share;
    if (!s || s.mode !== "theirs") return;
    let stopped = false;
    const tick = async () => {
      try {
        const clip = await fetchClipboard(s.data.code);
        if (stopped) return;
        const texts = clip.items.filter((i) => i.type === "text");
        setShare((prev) => {
          if (!prev || prev.data.code !== s.data.code) return prev;
          const known = new Set(prev.data.items.map((i) => i.id));
          const fresh = texts.filter((i) => !known.has(i.id));
          if (!fresh.length) return prev;
          const items = [...prev.data.items, ...fresh];
          return {
            ...prev,
            data: {
              ...prev.data,
              items,
              bytes_total: items.reduce((total, i) => total + (i.size || 0), 0),
            },
          };
        });
      } catch (e) {
        if (stopped) return;
        if (e instanceof ApiError && (e.errCode === "not_found" || e.errCode === "expired")) {
          setShare(null);
          notify(`Code ${s.data.code} closed — poof.`, "ok");
        }
      }
    };
    tick();
    const id = window.setInterval(tick, 2000);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [share, notify]);

  const dismissShare = useCallback(
    (message?: string) => {
      setShare(null);
      setGrabError(null);
      setAbsorbing(false);
      if (message) notify(message, "ok");
    },
    [notify]
  );

  /** Drop/paste: mint a portal or append to the open one. */
  const addItems = useCallback(
    async (files: File[], texts: Snippet[]) => {
      setBusy(true);
      try {
        if (share) {
          // Append to the open share. The server wraps content with the
          // share's at-rest key — the client just sends plaintext.
          const updated = await appendShare(share.data.code, files, texts);
          setShare((prev) => (prev ? { ...prev, data: updated } : prev));
          return;
        }

        // New share: plaintext upload; the server encrypts it at rest.
        setAbsorbing(true);
        const created = await createShare(files, texts, ttl, burn);
        await sleep(650);
        setAbsorbing(false);
        setShare({ data: created, mode: "mine" });
        notify(`Code ${created.code} is open — encrypted at rest.`, "ok");
      } catch (e) {
        setAbsorbing(false);
        notify(errMessage(e, "Upload failed — try again."), "error");
      } finally {
        setBusy(false);
      }
    },
    [share, ttl, burn, notify]
  );

  const openShare = useCallback(
    async (code: string) => {
      setGrabbing(true);
      setGrabError(null);
      try {
        const data = await fetchShare(code);
        setShare({ data, mode: "theirs" });
        window.setTimeout(() => {
          resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        }, 120);
      } catch (e) {
        setGrabError(friendlyOpenError(e));
      } finally {
        setGrabbing(false);
      }
    },
    []
  );

  const booted = useRef(false);
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    const fromQr = new URLSearchParams(window.location.search).get("c");
    if (fromQr) openShare(fromQr);
  }, [openShare]);

  const code = share?.data.code ?? null;

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-3xl flex-col px-4 sm:px-6">
      <SolarSystem />
      <header className="flex items-center gap-3 py-5">
        <span className="mini-logo" aria-hidden="true" />
        <span className="text-[15px] font-bold tracking-tight text-ink">AnyDevice</span>
        <h1 className="sr-only">AnyDevice — drop to share, enter a code to receive</h1>
      </header>

      <main className="flex flex-1 flex-col items-center">
        <section
          className={`portal-stage ${absorbing ? "absorbing" : ""} ${stageOver ? "dragover" : ""}`}
          onClick={() => {
            if (!share && !busy) document.getElementById("portal-file")?.click();
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setStageOver(true);
          }}
          onDragLeave={() => setStageOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setStageOver(false);
            if (busy) return;
            const files = Array.from(e.dataTransfer.files);
            if (files.length) addItems(files, []);
          }}
        >
          <Portal />

          {!share ? (
            <div className="drop-foreground" key="foreground">
              <AttachPanel
                busy={busy}
                ttl={ttl}
                burn={burn}
                canConfigure
                activeCode={null}
                onAttach={addItems}
                onTtlChange={setTtl}
                onBurnChange={setBurn}
              />
            </div>
          ) : (
            <div className="emerge" key={share.data.code}>
              <CodeBox
                share={share.data}
                mode={share.mode}
                onExpired={() => dismissShare(`Code ${share.data.code} closed — poof.`)}
                onDiscard={() =>
                  dismissShare(
                    share.mode === "mine" ? "Fresh slate — drop something new." : undefined
                  )
                }
              />
            </div>
          )}
        </section>

        {/* Receive below the portal, still quiet */}
        <div className="w-full max-w-md">
          <GrabPanel
            busy={grabbing}
            error={grabError}
            onOpen={openShare}
            onClearError={() => setGrabError(null)}
          />
        </div>

        {!share && <RecentCodes onOpen={openShare} />}

        {/* Active share results — below the fold */}
        {share && (
          <div ref={resultsRef} className="mt-10 w-full max-w-2xl scroll-mt-6 pb-16">
            <ItemsList share={share.data} onBurnt={() => dismissShare("Gone — downloaded, then self-destructed.")} />

            {/* Drop more onto the same code from this device */}
            <div className="mt-6">
              <AttachPanel
                busy={busy}
                ttl={ttl}
                burn={burn}
                canConfigure={false}
                activeCode={code}
                onAttach={addItems}
                onTtlChange={setTtl}
                onBurnChange={setBurn}
              />
            </div>
          </div>
        )}

        {!share && (
          <p className="mt-8 max-w-sm px-4 pb-16 text-center text-xs leading-relaxed text-mute">
            No account, nothing saved forever. Content is encrypted on the server
            and the portal closes after its timer — files and all.
          </p>
        )}
      </main>

      <footer className="flex flex-col items-center gap-4 border-t border-edge/50 py-6">
        <p className="text-xs text-mute/80">AnyDevice · drop it here, receive it anywhere</p>
        <GithubBadge />
        <a
          href="https://github.com/SouravGiri-007/anydevice"
          target="_blank"
          rel="noreferrer noopener"
          className="star-cta"
        >
          <span className="star-cta-inner">
            <span className="star-cta-icon">
              <StarIcon />
            </span>
            <span className="star-cta-text">
              Like AnyDevice? <strong>Star it on GitHub</strong>
            </span>
          </span>
        </a>
      </footer>

      {toast && <Toast toast={toast} />}
    </div>
  );
}

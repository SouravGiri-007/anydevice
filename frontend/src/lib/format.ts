export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** i;
  return `${value >= 100 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}

/** mm:ss or h:mm:ss countdown from a duration in seconds. */
export function formatCountdown(totalSeconds: number): string {
  const s = Math.max(0, Math.ceil(totalSeconds));
  const hours = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(mins)}:${pad(secs)}` : `${mins}:${pad(secs)}`;
}

/** Rough language hint from a filename, used for syntax highlighting. */
export function languageFor(name: string): string {
  const ext = (name.split(".").pop() || "").toLowerCase();
  const map: Record<string, string> = {
    py: "python",
    js: "javascript",
    mjs: "javascript",
    cjs: "javascript",
    jsx: "javascript",
    ts: "typescript",
    tsx: "typescript",
    html: "xml",
    htm: "xml",
    svg: "xml",
    xml: "xml",
    css: "css",
    json: "json",
    md: "markdown",
    markdown: "markdown",
    sql: "sql",
    sh: "bash",
    bash: "bash",
    zsh: "bash",
    yaml: "yaml",
    yml: "yaml",
    java: "java",
    c: "c",
    h: "c",
    cpp: "cpp",
    go: "go",
    rs: "rust",
    rb: "ruby",
    php: "php",
  };
  return map[ext] ?? "";
}

export function isImage(mime: string, name: string): boolean {
  return mime.startsWith("image/") || /\.(png|jpe?g|webp|gif|avif|svg)$/i.test(name);
}

export function isPdf(mime: string, name: string): boolean {
  return mime === "application/pdf" || name.toLowerCase().endsWith(".pdf");
}

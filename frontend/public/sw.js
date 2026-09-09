/* AnyDevice service worker.
 *
 * Goal: make the app installable and fast, WITHOUT ever caching dynamic data.
 *
 * Rules:
 *  - Only same-origin GET requests are handled.
 *  - Any URL under /api/ (shares, codes, downloads, health) is NEVER touched
 *    by the service worker — browser requests go straight to the network and
 *    responses are never written to a cache.
 *  - The app shell (/, /index.html, hashed JS/CSS, icons, manifest) is
 *    precached for instant/standalone launch; runtime stale-while-revalidate
 *    keeps asset updates flowing.
 *  - File transfers/downloads are blob/stream responses from the backend — they
 *    are cross-origin or under /api/, so nothing here can cache transfer data.
 */
const VERSION = "anydevice-shell-v1";
const SHELL_CACHE = VERSION;

const PRECACHE_URLS = [
  "/",
  "/index.html",
  "/manifest.webmanifest",
  "/icon-192.png",
  "/icon-512.png",
  "/maskable-512.png",
  "/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  // Cross-origin (e.g. fontshare) → passthrough, never cached.
  if (url.origin !== self.location.origin) return;
  // Dynamic backend traffic → passthrough, NEVER cached. This covers all
  // share data, codes, status polling, and downloads.
  if (url.pathname.startsWith("/api/")) return;

  if (request.mode === "navigate") {
    // Network-first for the shell: fresh HTML when online, cached HTML offline.
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(SHELL_CACHE).then((cache) => cache.put("/index.html", copy));
          }
          return response;
        })
        .catch(() => caches.match("/index.html"))
    );
    return;
  }

  // Hashed static assets (JS/CSS/fonts/images): stale-while-revalidate.
  event.respondWith(
    caches.match(request).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
// Minimal service worker: caches the app shell so it opens instantly and
// installs cleanly as a PWA. Chat requests always go to the network (the
// data changes too often to cache) — this is about making the app itself
// launchable offline, not about offline data.
//
// v1 cached "/" and every page cache-first with a CACHE_NAME that never
// changed — once a page was cached, it stayed frozen at whatever version
// was live at install time FOREVER, even across new deploys, since nothing
// ever told the browser to re-fetch it. That's why UI changes (a loading
// indicator, dictation tweaks) could silently never show up on an already-
// installed phone/browser while server-side/API changes worked fine. Fixed
// by going network-first for pages, so a fresh deploy is picked up on the
// very next load; icons/manifest rarely change so those stay cache-first
// for instant offline install. Bumping CACHE_NAME also clears out anyone's
// old v1 cache on next activate.

const CACHE_NAME = "home-manager-shell-v2";
const SHELL_ASSETS = [
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Never cache API calls — always hit the network for live data.
  if (url.pathname.startsWith("/api/")) return;

  const isPage =
    event.request.mode === "navigate" ||
    url.pathname === "/" ||
    url.pathname.endsWith(".html");

  if (isPage) {
    // Network-first: always try to get the latest page; only fall back to
    // whatever's cached if the network request actually fails (offline).
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return res;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Everything else (icons, manifest, etc.) — cache-first, they rarely change.
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});

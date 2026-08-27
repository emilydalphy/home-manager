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
//
// v2 -> v3: v2's "isPage" network-first check only covered navigations and
// .html files — it missed .js and .css, which fell into the cache-first
// "everything else" bucket alongside icons/manifest. Those files change on
// every deploy just like the pages that load them, so any device that had
// already cached shell.js/shell.css kept serving that exact frozen version
// forever, even though shell.html itself (a "page") was updating fine —
// a working page shell silently running old JS logic. This is the same
// bug the v1->v2 fix addressed, just for a different file-extension bucket
// that got missed. Fixed by making .js/.css network-first too; bumping
// CACHE_NAME again clears any v2 cache (and the stale shell.js/css inside
// it) on next activate for anyone already affected.

const CACHE_NAME = "home-manager-shell-v3";
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

  const isVersioned =
    event.request.mode === "navigate" ||
    url.pathname === "/" ||
    url.pathname.endsWith(".html") ||
    url.pathname.endsWith(".js") ||
    url.pathname.endsWith(".css");

  if (isVersioned) {
    // Network-first: always try to get the latest page/script/stylesheet;
    // only fall back to whatever's cached if the network request actually
    // fails (offline).
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

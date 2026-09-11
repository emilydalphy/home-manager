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

// v3 -> v4 (Pomona repaint, 2026-09-02): this bump matters more than the
// previous two, because the things that changed are precisely the ones this
// worker still serves CACHE-FIRST. icon-192.png, icon-512.png and
// manifest.json all kept their URLs while their *contents* changed
// completely — new apricot-on-spruce app icon, new name, new theme/background
// colours. Without a bump, an already-installed PWA would go on serving the
// old plum icon and the old "Home Manager" manifest from cache forever, since
// nothing about the request would tell it to re-fetch. The name changed too,
// not just the digit, so there is no chance of colliding with a stale entry.
//
// v5 -> v6 (grocery offline, 2026-09-11): navigations fall back to ANY cached
// shell route, not only the exact URL. "/", "/grocery", "/week" and
// "/kitchen" all serve the same shell.html (app/main.py), but the cache is
// keyed by URL — so a phone that opened "/" and reached Shop by tapping the
// tab (a pushState to /grocery, never a navigation) had nothing cached
// under /grocery, and a reload in the store got the browser's own offline
// page. A redirected navigation (signed out: "/" -> /login) is no longer
// cached either, so the offline fallback can't be a login screen. The bump
// clears any such entry already sitting in v5. API responses are still
// never cached here: the grocery list's offline copy is page-level
// (static/grocery-offline.js), where it can be keyed per household and
// carry the ticks made without signal.
const CACHE_NAME = "pomona-shell-v6";
// Where an offline navigation lands when its own URL was never cached.
// Every entry serves shell.html; the order only decides which copy is tried
// first.
const SHELL_ROUTES = ["/", "/grocery", "/week", "/kitchen"];
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

// The cached copy of this exact request, or — for a navigation only — the
// cached copy of any shell route, since they are all the same page.
async function offlineFallback(request, isNavigation) {
  const exact = await caches.match(request);
  if (exact || !isNavigation) return exact;
  const cache = await caches.open(CACHE_NAME);
  for (const route of SHELL_ROUTES) {
    const hit = await cache.match(route);
    if (hit) return hit;
  }
  return undefined;
}

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
    const isNavigation = event.request.mode === "navigate";
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          // A navigation that ended somewhere else (signed out -> /login)
          // is not the shell, and caching it under the shell's URL would
          // make the offline fallback a sign-in screen.
          if (!isNavigation || (res.ok && !res.redirected)) {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return res;
        })
        .catch(() => offlineFallback(event.request, isNavigation))
    );
    return;
  }

  // Everything else (icons, manifest, etc.) — cache-first, they rarely change.
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});

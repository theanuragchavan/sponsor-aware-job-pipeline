/**
 * Service worker.
 *
 * Its job is narrow on purpose: make the app installable, and speed up repeat
 * launches. It is explicitly NOT an offline mode.
 *
 * **There is no offline mode because there is no offline data.** Every screen
 * here is a view onto the API — a review queue, a sponsorship verdict, a
 * decision log. With the server down there is nothing true to show, so the
 * honest behaviour is to fail and say so, which is what `ServerDown` does.
 *
 * Two rules, and the first one is a bug I already shipped once:
 *
 * 1. **Never cache navigations or index.html.** The first version did, and
 *    since Vite fingerprints asset filenames, a cached index.html kept pointing
 *    at hashes that no longer existed. Offline it served a page whose scripts
 *    all failed — a blank white window, which is worse than an error. An HTML
 *    document that references hashed assets is only valid alongside that exact
 *    build, so it must always come from the network.
 *
 * 2. **`/api` is network-only, permanently.** A cached sponsorship verdict is
 *    precisely the failure this project exists to prevent: showing "yes" for an
 *    employer whose licence has lapsed is worse than showing nothing.
 *
 * That leaves `/assets/*`, which Vite content-hashes. Those are immutable by
 * construction — a changed file gets a changed name — so cache-first on them is
 * safe and is the only caching here.
 */
const ASSETS = "resolve-assets-v2";

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== ASSETS).map((k) => caches.delete(k)),
      ))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Rule 1: documents always come from the network.
  if (request.mode === "navigate") return;
  // Rule 2: data always comes from the network.
  if (url.pathname.startsWith("/api/")) return;
  if (url.pathname === "/manifest.webmanifest") return;

  // Only content-hashed build output is cacheable.
  if (!url.pathname.startsWith("/assets/")) return;

  event.respondWith(
    caches.match(request).then((hit) => hit || fetch(request).then((res) => {
      if (res.ok && res.type === "basic") {
        const copy = res.clone();
        caches.open(ASSETS).then((cache) => cache.put(request, copy));
      }
      return res;
    })),
  );
});

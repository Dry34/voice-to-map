// Service worker : le site s'ouvre même sans réseau. Responsable : A.
//
// - Pages, scripts, styles, Leaflet, polices : réseau d'abord (pour avoir la dernière version),
//   cache si le réseau ne répond pas.
// - Tuiles de la carte : cache d'abord (les zones déjà vues restent affichées hors ligne).
// - API : jamais mise en cache ici (les annonces hors ligne passent par la boîte d'envoi).

const SHELL = "vtm-shell-v1";
const TILES = "vtm-tiles-v1";
const MAX_TILES = 1500;

const PRECACHE = [
  "./", "index.html", "app.html", "manifest.webmanifest",
  "css/style.css", "css/landing.css",
  "js/app.js", "js/map.js", "js/socket.js", "js/ptt.js", "js/identity.js", "js/outbox.js", "js/prefs.js", "js/landing.js",
  "icons/icon-192.png", "icons/icon-512.png", "icons/icon.svg",
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css",
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js",
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/MarkerCluster.css",
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/leaflet.markercluster.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    // Un fichier qui échoue ne doit pas empêcher l'installation.
    await Promise.all(PRECACHE.map((url) => cache.add(new Request(url, { cache: "reload" })).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (![SHELL, TILES].includes(key)) await caches.delete(key);
    await self.clients.claim();
  })());
});

async function trimTiles() {
  const cache = await caches.open(TILES);
  const keys = await cache.keys();
  for (const k of keys.slice(0, Math.max(0, keys.length - MAX_TILES))) await cache.delete(k);
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  // API et WebSocket : toujours le réseau.
  if (url.origin === location.origin && (url.pathname.startsWith("/api/") || url.pathname === "/ws")) return;

  // Tuiles OSM : cache d'abord.
  if (url.hostname.endsWith("tile.openstreetmap.org")) {
    event.respondWith((async () => {
      const cache = await caches.open(TILES);
      const hit = await cache.match(req);
      if (hit) return hit;
      try {
        const res = await fetch(req);
        if (res.ok || res.type === "opaque") { cache.put(req, res.clone()); trimTiles(); }
        return res;
      } catch {
        return new Response("", { status: 504 });
      }
    })());
    return;
  }

  // Le reste (pages, scripts, styles, polices, Leaflet) : réseau d'abord, cache en secours.
  event.respondWith((async () => {
    const cache = await caches.open(SHELL);
    try {
      const res = await fetch(req);
      if (res.ok) { cache.put(req, res.clone()); return res; }
      // Erreur serveur ou tunnel coupé (Cloudflare 530…) : la version gardée vaut mieux qu'une page d'erreur.
      if (res.status >= 500) {
        const hit = await cache.match(req, { ignoreSearch: true });
        if (hit) return hit;
      }
      return res;
    } catch {
      const hit = await cache.match(req, { ignoreSearch: true });
      if (hit) return hit;
      if (req.mode === "navigate") return (await cache.match("app.html")) || Response.error();
      return Response.error();
    }
  })());
});

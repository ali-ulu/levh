"use strict";
/* LEVH offline-first service worker.
 *
 * Strategy:
 *  - `/_next/static/*`  : cache-first. Next content-hashes these names, so a
 *                         stale copy is never wrong — the shell only ever
 *                         references hashes, and new hashes fetch fresh.
 *  - other same-origin GET: network-first with cache fallback. While online the
 *                         user always gets a fresh document (the server sets
 *                         `no-cache` on these); offline we serve the last good
 *                         snapshot so the dashboard still opens.
 *  - everything else (API, /api/mcp, WebSocket) : never cached, passed through.
 *
 * Bump CACHE_NAME when changing this file in a way that must invalidate old
 * snapshots, so the old cache is atomically dropped on activation.
 */

const CACHE_NAME = "levh-shell-v2";
const IMMUTABLE_PREFIX = "/_next/static/";

self.addEventListener("install", () => {
  // Don't wait: no expensive work, just take over.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  const keep = new Set([CACHE_NAME]);
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((k) => !keep.has(k)).map((k) => caches.delete(k)),
      );
    }),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // never cache reads/mutations

  const url = new URL(req.url);
  // Only same-origin GETs on the default (dashboard) scope.
  if (url.origin !== self.location.origin) return;
  const path = url.path;
  // Never intercept API, MCP or websocket traffic.
  if (path.startsWith("/api/") || path === "/api") return;
  if (path.startsWith("/api")) return;

  if (path.startsWith(IMMUTABLE_PREFIX)) {
    event.respondWith(cacheFirst(req));
  } else {
    event.respondWith(networkFirst(req));
  }
});

async function cacheFirst(req) {
  const cached = await caches.match(req);
  if (cached) return cached;
  const res = await fetch(req);
  if (res && res.ok) {
    const copy = res.clone();
    const cache = await caches.open(CACHE_NAME);
    cache.put(req, copy);
  }
  return res;
}

async function networkFirst(req) {
  try {
    const res = await fetch(req);
    if (res && res.ok) {
      const copy = res.clone();
      const cache = await caches.open(CACHE_NAME);
      cache.put(req, copy);
    }
    return res;
  } catch (_) {
    const cached = await caches.match(req);
    if (cached) return cached;
    return new Response("LEVH is offline.", {
      status: 503,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }
}
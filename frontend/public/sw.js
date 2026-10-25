// Artha service worker — KILL SWITCH (self-destructing).
//
// The previous caching SW repeatedly served stale builds (cached index.html → deleted JS hash →
// blank screen). The app is always used online, so offline caching isn't worth that
// risk. This worker unregisters ITSELF, clears all caches, and reloads every open tab — after which
// the app has no service worker and always loads fresh from the network (hashed assets cache safely
// via normal HTTP). The browser checks /sw.js on navigation, so even a stuck old SW gets replaced
// by this one and then removed.
self.addEventListener('install', () => self.skipWaiting())

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    try {
      const keys = await caches.keys()
      await Promise.all(keys.map((k) => caches.delete(k)))
      await self.registration.unregister()
      const clients = await self.clients.matchAll({ type: 'window' })
      clients.forEach((c) => c.navigate(c.url))
    } catch (e) { /* noop */ }
  })())
})
// No fetch handler → all requests go straight to the network/browser cache.

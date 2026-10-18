// MyVault service worker — caches the app shell, bypasses /api, /raw, /download
const CACHE = 'MyVault-v1';
const SHELL = [
  '/',
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/icons/icon.svg',
  '/manifest.json',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE)
      .then(cache => cache.addAll(SHELL).catch(() => {}))
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/raw/') ||
    url.pathname.startsWith('/download/') ||
    url.pathname.startsWith('/viewer/')
  ) {
    return;
  }
  event.respondWith(
    caches.match(req).then(hit => {
      if (hit) return hit;
      return fetch(req).then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(req, clone)).catch(() => {});
        return res;
      }).catch(() => caches.match('/'));
    })
  );
});
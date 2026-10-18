// ---------------------------------------------------------------------------
// MyVault service worker
//
// Strategy:
//   • Static assets (CSS/JS/icons/manifest): NETWORK-FIRST → cache fallback
//     so updates ship the moment you edit a file. Offline still works.
//   • HTML pages (/ and /login), /api/*, /raw/*, /download/*, /viewer/*,
//     /health: bypass the SW entirely — always go to the network.
//     This is required so the auth middleware can redirect unauthenticated
//     users and so revoked/expired sessions take effect immediately.
// ---------------------------------------------------------------------------

const CACHE = 'MyVault-v3';

const SHELL = [
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/js/login.js',
  '/static/icons/icon.svg',
  '/manifest.json',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE).then(cache =>
      Promise.allSettled(
        SHELL.map(url => cache.add(url).catch(() => {}))
      )
    )
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys =>
        Promise.all(
          keys.filter(k => k !== CACHE).map(k => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const req = event.request;

  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Bypass — never cache these
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/raw/') ||
    url.pathname.startsWith('/download/') ||
    url.pathname.startsWith('/viewer/') ||
    url.pathname === '/login' ||
    url.pathname === '/' ||
    url.pathname === '/health'
  ) {
    return;
  }

  // Network-first: always try network, cache as fallback.
  event.respondWith(
    fetch(req)
      .then(res => {
        if (res && res.status === 200) {
          const ct = res.headers.get('content-type') || '';
          if (!ct.includes('text/html')) {
            const clone = res.clone();
            caches.open(CACHE)
              .then(c => c.put(req, clone))
              .catch(() => {});
          }
        }
        return res;
      })
      .catch(() => caches.match(req))
  );
});
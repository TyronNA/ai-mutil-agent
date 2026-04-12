const CACHE_NAME = "ai-multi-agent-pwa-v2";
const APP_SHELL = ["/", "/manifest.webmanifest", "/icon.svg", "/icon-512.svg"];

function isAppShellRequest(request) {
  const url = new URL(request.url);
  return APP_SHELL.includes(url.pathname);
}

function isStaticAssetRequest(request) {
  const url = new URL(request.url);
  return url.pathname.startsWith("/_next/") || url.pathname.startsWith("/assets/");
}

function isApiRequest(request) {
  const url = new URL(request.url);
  return (
    url.pathname.startsWith("/api/") ||
    url.pathname === "/queue" ||
    url.pathname.startsWith("/queue/") ||
    url.pathname === "/sessions" ||
    url.pathname.startsWith("/sessions/") ||
    url.pathname === "/status" ||
    url.pathname.startsWith("/status/") ||
    url.pathname === "/analytics" ||
    url.pathname.startsWith("/analytics/") ||
    url.pathname === "/chat" ||
    url.pathname.startsWith("/chat/")
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;

  if (isApiRequest(event.request)) {
    event.respondWith(fetch(event.request));
    return;
  }

  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).catch(() => caches.match("/")));
    return;
  }

  if (!isAppShellRequest(event.request) && !isStaticAssetRequest(event.request)) {
    event.respondWith(fetch(event.request));
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request)
        .then((response) => {
          // Only cache http/https requests, skip chrome-extension and other schemes
          if (
            event.request.url.startsWith("http://") ||
            event.request.url.startsWith("https://")
          ) {
            const cloned = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, cloned));
          }
          return response;
        })
        .catch(() => caches.match("/"));
    }),
  );
});

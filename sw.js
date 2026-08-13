const CACHE = "bld-v6";
const SHELL = ["./", "./index.html", "./leads.json", "./manifest.json", "./icon-192.png", "./icon-512.png", "./apple-touch-icon.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ).then(() => self.clients.claim()));
});
// Real push from the hourly task — buzzes the phone even when the app is closed.
self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) {}
  e.waitUntil(self.registration.showNotification(d.title || "Boat Lead Desk", {
    body: d.body || "New activity on the board",
    icon: "icon-192.png", badge: "icon-192.png",
    tag: d.tag || "bld-push", renotify: true
  }));
});
// Tapping a Tier 1 notification opens (or focuses) the dashboard.
self.addEventListener("notificationclick", e => {
  e.notification.close();
  e.waitUntil(clients.matchAll({type:"window", includeUncontrolled:true}).then(list => {
    for (const c of list) { if ("focus" in c) return c.focus(); }
    return clients.openWindow("./");
  }));
});
// Network-first so fresh leads always show; cache fallback keeps it usable offline.
// cache:"no-cache" makes the browser REVALIDATE with the server instead of
// trusting GitHub Pages' 10-minute cache header — so app updates appear on the
// very next open (the server answers "not modified" almost for free otherwise).
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return; // let store/Facebook requests pass through untouched
  if (e.request.method !== "GET") return;
  e.respondWith(
    fetch(url.href, {cache: "no-cache", credentials: "same-origin"}).then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(e.request, copy));
      return res;
    }).catch(() => caches.match(e.request, {ignoreSearch:true}))
  );
});

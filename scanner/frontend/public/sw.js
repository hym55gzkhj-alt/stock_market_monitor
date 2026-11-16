// Minimaler Service Worker: macht das Dashboard installierbar.
// Kursdaten werden bewusst NICHT zwischengespeichert – sie sollen immer aktuell vom Server kommen.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});

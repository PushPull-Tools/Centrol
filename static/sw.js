// Centrol — service worker minimal : rend l'appli installable (fenêtre sans barre du navigateur).
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});

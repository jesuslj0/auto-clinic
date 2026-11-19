/* Service worker del panel: solo notificaciones push. Sin caché offline en v1.
 *
 * El payload es genérico a propósito (título y cuerpo de la categoría): el aviso
 * pasa por Google/Apple y no lleva nombre de paciente ni texto de mensajes.
 */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
    let data = {};
    try { data = event.data ? event.data.json() : {}; } catch (e) { data = {}; }
    const title = data.title || 'Auto Clinic';
    event.waitUntil(self.registration.showNotification(title, {
        body: data.body || '',
        tag: data.tag || undefined,
        icon: '/static/img/favicon/android-chrome-192x192.png',
        data: { url: data.url || '/notificaciones/' },
    }));
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const target = new URL((event.notification.data && event.notification.data.url) || '/notificaciones/', self.location.origin);
    // Solo rutas del propio panel, que exigen sesión (y 2FA) igualmente.
    if (target.origin !== self.location.origin) return;
    event.waitUntil((async () => {
        const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
        for (const client of windows) {
            if (new URL(client.url).origin === self.location.origin && 'focus' in client) {
                await client.focus();
                if ('navigate' in client) { try { await client.navigate(target.href); } catch (e) { /* ignora */ } }
                return;
            }
        }
        await self.clients.openWindow(target.href);
    })());
});

/* Notificaciones del panel: contador de la campana, toast cerrable y alta de push.
 *
 * Un socket propio (`/ws/notifications/`) que solo avisa de que hay algo nuevo;
 * el contenido se pide por HTTP, filtrado por usuario. Reconexión con backoff
 * simple y, tras 3 fallos, sondeo cada 30 s. No toca `chat_live.js`.
 */
(function () {
    const script = document.currentScript;
    const wsPath = script.dataset.wsPath;
    const countUrl = script.dataset.countUrl;
    let failures = 0, socket = null, pollTimer = null, retry = 1000, lastToastId = null;

    function setBadge(n) {
        document.querySelectorAll('[data-notif-unread]').forEach((el) => {
            el.classList.toggle('hidden', !n);
            const c = el.querySelector('[data-notif-unread-count]');
            if (c) c.textContent = n > 99 ? '99+' : n;
        });
    }

    async function refresh(announce) {
        try {
            const res = await fetch(countUrl, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
            if (!res.ok) return;
            const data = await res.json();
            setBadge(data.unread);
            document.dispatchEvent(new CustomEvent('notifications:updated', { detail: data }));
            if (announce && data.latest && data.latest.id !== lastToastId && window.acToast) {
                window.acToast(data.latest.title, 'info', { href: data.latest.href, sticky: true });
            }
            if (data.latest) lastToastId = data.latest.id;
        } catch (e) { /* sin red: se reintenta */ }
    }

    function startPolling() {
        if (pollTimer) return;
        pollTimer = setInterval(() => refresh(true), 30000);
    }

    function connect() {
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        try { socket = new WebSocket(`${proto}://${location.host}${wsPath}`); } catch (e) { failures++; return startPolling(); }
        socket.onopen = () => { failures = 0; retry = 1000; if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } refresh(false); };
        socket.onmessage = () => refresh(true);
        socket.onclose = (ev) => {
            if (ev.code === 4401 || ev.code === 4403) return startPolling();
            failures++;
            if (failures >= 3) startPolling();
            setTimeout(connect, retry);
            retry = Math.min(retry * 2, 30000);
        };
    }

    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(false); });
    connect();

    // --- Web Push ---------------------------------------------------------
    function b64ToBytes(b64) {
        const pad = '='.repeat((4 - (b64.length % 4)) % 4);
        const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
        return Uint8Array.from(raw, (c) => c.charCodeAt(0));
    }
    function csrf() {
        const m = document.cookie.match(/csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }
    async function post(url, body) {
        return fetch(url, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
            body: JSON.stringify(body),
        });
    }

    window.acPush = {
        supported: () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window,
        async current() {
            if (!this.supported()) return null;
            const reg = await navigator.serviceWorker.getRegistration('/');
            return reg ? reg.pushManager.getSubscription() : null;
        },
        async enable(publicKey, subscribeUrl) {
            const permission = await Notification.requestPermission();
            if (permission !== 'granted') throw new Error('denied');
            const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
            await navigator.serviceWorker.ready;
            const sub = (await reg.pushManager.getSubscription()) || await reg.pushManager.subscribe({
                userVisibleOnly: true, applicationServerKey: b64ToBytes(publicKey),
            });
            const res = await post(subscribeUrl, sub.toJSON());
            if (!res.ok) throw new Error('server');
        },
        async disable(unsubscribeUrl) {
            const sub = await this.current();
            if (!sub) return;
            await post(unsubscribeUrl, { endpoint: sub.endpoint });
            await sub.unsubscribe();
        },
    };
})();

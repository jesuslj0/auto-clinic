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

    // Mismo trazo y color por categoría que `notifications/templatetags/notification_tags.py`
    // (listado y campana): si se añade o cambia una categoría ahí, hay que tocar esto también.
    const ICONS = {
        appointment_pending: ['M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z', 'text-info'],
        appointment_rescheduled: ['M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99', 'text-warning'],
        appointment_cancelled: ['M9.75 9.75l4.5 4.5m0-4.5l-4.5 4.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z', 'text-danger'],
        chat_new: ['M8.625 9.75a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H8.25m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H12m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 01-2.555-.337A5.972 5.972 0 015.41 20.97a5.969 5.969 0 01-.474-.065 4.48 4.48 0 00.978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25z', 'text-brand-fg'],
        chat_message: ['M8.625 9.75a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H8.25m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H12m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 01-2.555-.337A5.972 5.972 0 015.41 20.97a5.969 5.969 0 01-.474-.065 4.48 4.48 0 00.978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25z', 'text-brand-fg'],
        message_failed: ['M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z', 'text-danger'],
        patient_form_completed: ['M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z', 'text-success'],
        template_status: ['M10.34 15.84c-.688-.06-1.386-.09-2.09-.09H7.5a4.5 4.5 0 110-9h.75c.704 0 1.402-.03 2.09-.09m0 9.18c.253.962.584 1.892.985 2.783.447 1 1.77 1.267 2.47.473C14.08 18.544 15 16.313 15 13.5c0-2.813-.92-5.044-2.206-6.405-.701-.794-2.023-.527-2.47.473-.401.891-.732 1.821-.985 2.783m0 9.18a24.249 24.249 0 01-3.75 0', 'text-info'],
    };
    function iconFor(kind) {
        const [path, fg] = ICONS[kind] || ICONS.chat_message;
        return { path, fg };
    }

    // Mismas reglas que `short_timesince` del lado de Django: «hace 1 h», «hace 9 min»…
    function shortTimeSince(iso) {
        if (!iso) return '';
        const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
        if (seconds < 60) return 'ahora';
        const minutes = seconds / 60;
        if (minutes < 60) return `hace ${Math.floor(minutes)} min`;
        const hours = minutes / 60;
        if (hours < 24) return `hace ${Math.floor(hours)} h`;
        const days = hours / 24;
        if (days < 7) return `hace ${Math.floor(days)} d`;
        const weeks = days / 7;
        if (weeks < 5) return `hace ${Math.floor(weeks)} sem`;
        return new Date(iso).toLocaleDateString('es-ES', { day: '2-digit', month: '2-digit' });
    }

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
                window.acToast(data.latest.detail || data.latest.title, 'info', {
                    href: data.latest.href, sticky: true, icon: iconFor(data.latest.kind),
                    meta: shortTimeSince(data.latest.at),
                });
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

    // --- Instalar como app ------------------------------------------------
    // Chrome lanza `beforeinstallprompt` una sola vez y puede hacerlo antes de que Alpine
    // pinte la página: se guarda aquí y el botón de «Preferencias» lo usa cuando quiera.
    let installEvent = null;
    const standalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
    window.addEventListener('beforeinstallprompt', (e) => {
        e.preventDefault();
        installEvent = e;
        document.dispatchEvent(new CustomEvent('ac:install-changed'));
    });
    window.addEventListener('appinstalled', () => {
        installEvent = null;
        document.dispatchEvent(new CustomEvent('ac:install-changed'));
    });
    window.acInstall = {
        installed: standalone,
        available: () => !!installEvent && !standalone(),
        ios: () => /iphone|ipad|ipod/i.test(navigator.userAgent) && !standalone(),
        async prompt() {
            if (!installEvent) return false;
            installEvent.prompt();
            const choice = await installEvent.userChoice;
            installEvent = null;
            document.dispatchEvent(new CustomEvent('ac:install-changed'));
            return choice.outcome === 'accepted';
        },
    };

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

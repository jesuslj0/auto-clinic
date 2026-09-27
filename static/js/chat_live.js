/*
 * Conexión en vivo con los chats de la clínica: un único WebSocket por página.
 *
 * Se carga en `base.html` para todo usuario con clínica, así que funciona en
 * cualquier sección: mantiene al día el contador de no leídos del menú (y el
 * título de la pestaña) y reparte los avisos a quien se suscriba, que hoy es
 * la bandeja (`chat_inbox.js`).
 *
 * El socket solo trae avisos («algo ha cambiado en tal hilo»), nunca contenido:
 * quien los recibe pide el HTML a Django. Por eso perder un aviso no es grave;
 * lo importante es enterarse de que se han podido perder, y para eso está el
 * evento `resync`.
 *
 * Eventos que reciben los suscriptores (`acChats.subscribe(fn)`):
 *   - los del servidor: `message`, `session`, `clinic` (ver agent/realtime.py);
 *   - `resync`: se ha podido perder algo (reconexión, pestaña que vuelve a
 *     primer plano, tick del modo polling). Hay que pedir lo pendiente.
 *
 * Resistencia a cortes:
 *   - Reconexión con espera exponencial (1 s, 2 s, 4 s… hasta 30 s), sin fin.
 *   - Tras `POLL_AFTER_FAILURES` fallos seguidos se activa además el polling
 *     cada 15 s: un proxy o una red móvil que corten WebSockets no pueden dejar
 *     a la clínica sin mensajes. Se sigue intentando reconectar por detrás y el
 *     polling se apaga en cuanto el socket vuelve.
 *   - Los cierres 4401 (sin sesión) y 4403 (usuario sin clínica) son
 *     definitivos: reintentar no los arregla.
 */
(function () {
    'use strict';

    const script = document.currentScript;
    const WS_PATH = script.dataset.wsPath;
    const UNREAD_URL = script.dataset.unreadUrl;

    const BACKOFF_START_MS = 1000;
    const BACKOFF_MAX_MS = 30000;
    const POLL_AFTER_FAILURES = 3;
    const POLL_INTERVAL_MS = 15000;
    const FATAL_CLOSE_CODES = [4401, 4403];

    const subscribers = new Set();
    let socket = null;
    let failures = 0;
    let everConnected = false;
    let stopped = false;
    let reconnectTimer = null;
    let pollTimer = null;
    let state = 'connecting'; // connecting | live | polling | stopped
    const baseTitle = document.title.replace(/^\(\d+\+?\) /, '');

    function setState(next) {
        if (state === next) return;
        state = next;
        document.dispatchEvent(new CustomEvent('ac-chats:state', { detail: { state } }));
    }

    function dispatch(event) {
        if (typeof event.total_unread === 'number') renderUnread(event.total_unread);
        subscribers.forEach((fn) => {
            try {
                fn(event);
            } catch (error) {
                console.error('[chats] suscriptor con error', error);
            }
        });
    }

    function renderUnread(total) {
        const label = total > 99 ? '99+' : String(total);
        document.querySelectorAll('[data-chat-unread]').forEach((badge) => {
            badge.classList.toggle('hidden', total === 0);
            const count = badge.querySelector('[data-chat-unread-count]');
            if (count) count.textContent = label;
        });
        document.title = total ? `(${label}) ${baseTitle}` : baseTitle;
    }

    // Sin socket no llegan totales: se piden a la cabecera del fragmento de la
    // lista (HEAD, sin cuerpo).
    function refreshUnread() {
        if (!UNREAD_URL) return;
        fetch(UNREAD_URL, { method: 'HEAD', credentials: 'same-origin', cache: 'no-store' })
            .then((response) => {
                const total = parseInt(response.headers.get('X-Total-Unread'), 10);
                if (response.ok && !Number.isNaN(total)) renderUnread(total);
            })
            .catch(() => {});
    }

    function resync() {
        refreshUnread();
        dispatch({ type: 'resync' });
    }

    function startPolling() {
        if (pollTimer) return;
        setState('polling');
        resync();
        pollTimer = setInterval(resync, POLL_INTERVAL_MS);
    }

    function stopPolling() {
        clearInterval(pollTimer);
        pollTimer = null;
    }

    function scheduleReconnect() {
        if (stopped) return;
        failures += 1;
        if (failures >= POLL_AFTER_FAILURES) startPolling();
        const delay = Math.min(BACKOFF_START_MS * 2 ** (failures - 1), BACKOFF_MAX_MS);
        clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(connect, delay);
    }

    function connect() {
        if (stopped) return;
        const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        try {
            socket = new WebSocket(`${scheme}//${window.location.host}${WS_PATH}`);
        } catch (error) {
            scheduleReconnect();
            return;
        }

        socket.addEventListener('open', () => {
            const wasReconnect = everConnected || failures > 0;
            everConnected = true;
            failures = 0;
            stopPolling();
            setState('live');
            // Lo que pasó mientras no había socket no va a llegar por él.
            if (wasReconnect) resync();
        });

        socket.addEventListener('message', (message) => {
            let event;
            try {
                event = JSON.parse(message.data);
            } catch (error) {
                return;
            }
            dispatch(event);
        });

        socket.addEventListener('close', (event) => {
            socket = null;
            if (FATAL_CLOSE_CODES.includes(event.code)) {
                stopped = true;
                stopPolling();
                setState('stopped');
                return;
            }
            setState(pollTimer ? 'polling' : 'connecting');
            scheduleReconnect();
        });
    }

    // Un móvil suspendido o una pestaña en segundo plano pueden haber perdido
    // avisos sin que el socket llegue a cerrarse: al volver, se pide lo pendiente.
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible' || stopped) return;
        resync();
    });

    window.acChats = {
        subscribe(fn) {
            subscribers.add(fn);
            return () => subscribers.delete(fn);
        },
        get state() {
            return state;
        },
    };

    // El número de partida lo pinta el servidor en el menú; el título de la
    // pestaña se pone a juego para que se vea también con ella en segundo plano.
    const initial = document.querySelector('[data-chat-unread-count]');
    if (initial) renderUnread(parseInt(initial.textContent, 10) || 0);

    if (WS_PATH && 'WebSocket' in window) {
        connect();
    } else {
        startPolling();
    }
})();

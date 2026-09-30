/*
 * Bandeja de chats en tiempo real (componente Alpine `chatInbox`).
 *
 * Escucha los avisos de `chat_live.js` y pide a Django el HTML ya renderizado:
 *   - burbujas nuevas del hilo abierto: `mensajes/?after=<último id del DOM>`;
 *   - la lista de conversaciones: `lista/` con los filtros aplicados.
 *
 * Siempre se pide «lo que hay después del último mensaje que tengo», así que un
 * aviso perdido se recupera solo en la siguiente petición. Un mismo mensaje no
 * se pinta dos veces: se descarta si su `data-message-id` ya está en el DOM.
 *
 * Lo que cambia la estructura del hilo (modo del agente, interruptor general,
 * ventana de 24 h que se reabre) no se parchea: se recarga la página, pero
 * nunca por sorpresa si hay un mensaje a medio escribir.
 */
document.addEventListener('alpine:init', () => {
    const LIST_DEBOUNCE_MS = 400;
    const BOTTOM_THRESHOLD_PX = 80;

    Alpine.data('chatInbox', (config) => ({
        sessionId: config.sessionId || null,
        agentPaused: config.agentPaused,
        agentEnabled: config.agentEnabled,
        connection: window.acChats ? window.acChats.state : 'stopped',
        unseen: 0,
        stale: false,
        fetching: false,
        fetchAgain: false,
        listTimer: null,
        viewer: { open: false, src: '', who: '', when: '', zoom: 1, loading: false, error: false },
        viewerBaseWidth: 0,
        viewerOpener: null,
        lastTypingAt: 0,
        expanded: false,

        init() {
            this.scrollToBottom();
            if (!window.acChats) return;

            this.unsubscribe = window.acChats.subscribe((event) => this.onEvent(event));
            this.onState = (event) => { this.connection = event.detail.state; };
            document.addEventListener('ac-chats:state', this.onState);
        },

        destroy() {
            if (this.unsubscribe) this.unsubscribe();
            document.removeEventListener('ac-chats:state', this.onState);
        },

        onEvent(event) {
            const isActive = this.sessionId && event.session_id === this.sessionId;

            switch (event.type) {
                case 'message':
                    if (isActive) {
                        // Un mensaje que ya está pintado ha cambiado (le ha llegado
                        // la foto, o su estado): se repinta solo esa burbuja.
                        if (this.findBubble(event.message_id)) this.refreshBubble(event.message_id);
                        else this.fetchNewMessages();
                    }
                    this.scheduleListRefresh();
                    break;
                case 'session':
                    if (isActive && event.agent_paused !== this.agentPaused) this.markStale();
                    this.scheduleListRefresh();
                    break;
                case 'clinic':
                    if (event.agent_enabled !== this.agentEnabled) this.markStale();
                    break;
                case 'resync':
                    this.fetchNewMessages();
                    this.scheduleListRefresh();
                    break;
            }
        },

        // --- Pantalla completa (móvil) --------------------------------------

        // El hilo pasa a ocupar toda la pantalla, encima de las barras de la app.
        // La página de debajo no se desplaza mientras tanto, y Escape lo cierra.
        toggleExpanded(force) {
            this.expanded = typeof force === 'boolean' ? force : !this.expanded;
            document.documentElement.style.overflow = this.expanded ? 'hidden' : '';
            if (this.expanded && !this.onExpandedKey) {
                this.onExpandedKey = (event) => {
                    if (event.key === 'Escape' && !this.viewer.open) this.toggleExpanded(false);
                };
                document.addEventListener('keydown', this.onExpandedKey);
            } else if (!this.expanded && this.onExpandedKey) {
                document.removeEventListener('keydown', this.onExpandedKey);
                this.onExpandedKey = null;
            }
            this.$nextTick(() => this.scrollToBottom());
        },

        // --- «Escribiendo…» en el WhatsApp del paciente -------------------------

        // Mientras el staff teclea, Django le pide a Meta el indicador (dura 25 s).
        // Se avisa como mucho cada 20 s; Django tiene su propio límite por hilo.
        // Nunca molesta: si falla, se ignora.
        onComposerInput(event) {
            const field = event.target;
            if (!field.value.trim()) return;
            const now = Date.now();
            if (now - this.lastTypingAt < 20000) return;
            this.lastTypingAt = now;
            const token = field.form && field.form.querySelector('[name=csrfmiddlewaretoken]');
            fetch(field.dataset.typingUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'X-CSRFToken': token ? token.value : '' },
            }).catch(() => {});
        },

        // --- Hilo -----------------------------------------------------------

        get thread() {
            return this.$refs.thread;
        },

        lastMessageId() {
            const bubbles = this.thread ? this.thread.querySelectorAll('[data-message-id]') : [];
            return bubbles.length ? bubbles[bubbles.length - 1].dataset.messageId : null;
        },

        async fetchNewMessages() {
            if (!this.sessionId || !config.messagesUrl) return;
            // Una sola petición a la vez: si llega otro aviso mientras tanto, se
            // repite al terminar con el último id ya actualizado.
            if (this.fetching) {
                this.fetchAgain = true;
                return;
            }
            this.fetching = true;
            try {
                let hasMore = true;
                while (hasMore) {
                    const url = new URL(config.messagesUrl, window.location.origin);
                    const lastId = this.lastMessageId();
                    if (lastId) url.searchParams.set('after', lastId);

                    const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
                    if (response.status === 400) {
                        // El último mensaje que tenemos ya no sirve de referencia.
                        this.markStale();
                        return;
                    }
                    if (!response.ok) return;

                    this.appendMessages(await response.text());
                    hasMore = response.headers.get('X-Has-More') === '1';
                }
            } catch (error) {
                // Red caída: lo recuperará el siguiente `resync`.
            } finally {
                this.fetching = false;
                if (this.fetchAgain) {
                    this.fetchAgain = false;
                    this.fetchNewMessages();
                }
            }
        },

        appendMessages(html) {
            const thread = this.thread;
            if (!thread) return;
            const wasAtBottom = this.isAtBottom();

            const template = document.createElement('template');
            template.innerHTML = html;
            let added = 0;
            let reopensWindow = false;

            [...template.content.children].forEach((node) => {
                const messageId = node.dataset.messageId;
                const day = node.dataset.day;
                if (messageId && thread.querySelector(`[data-message-id="${CSS.escape(messageId)}"]`)) return;
                if (day && thread.querySelector(`[data-day="${CSS.escape(day)}"]`)) return;

                thread.appendChild(node);
                if (messageId) {
                    added += 1;
                    if (node.dataset.direction === 'inbound') reopensWindow = true;
                }
            });

            if (!added) return;
            thread.querySelectorAll('[data-thread-empty]').forEach((node) => node.remove());

            // Un mensaje del paciente con el compositor cerrado reabre la ventana
            // de 24 h: hay que pintar el compositor, y eso es de la página.
            if (reopensWindow && this.$root.querySelector('[data-composer-closed]')) this.markStale();

            if (wasAtBottom) {
                this.scrollToBottom();
            } else {
                this.unseen += added;
            }
        },

        findBubble(messageId) {
            if (!this.thread || !messageId) return null;
            return this.thread.querySelector(`[data-message-id="${CSS.escape(messageId)}"]`);
        },

        async refreshBubble(messageId) {
            const url = new URL(config.messagesUrl, window.location.origin);
            url.searchParams.set('only', messageId);
            try {
                const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
                if (!response.ok) return;
                const template = document.createElement('template');
                template.innerHTML = (await response.text()).trim();
                const fresh = template.content.querySelector('[data-message-id]');
                const current = this.findBubble(messageId);
                if (fresh && current) current.replaceWith(fresh);
            } catch (error) {
                // Se queda la versión anterior; la próxima recarga la corrige.
            }
        },

        isAtBottom() {
            const thread = this.thread;
            if (!thread) return true;
            return thread.scrollHeight - thread.scrollTop - thread.clientHeight < BOTTOM_THRESHOLD_PX;
        },

        scrollToBottom() {
            const thread = this.thread;
            if (thread) thread.scrollTop = thread.scrollHeight;
            this.unseen = 0;
        },

        onThreadScroll() {
            if (this.unseen && this.isAtBottom()) this.unseen = 0;
        },

        // --- Lista ----------------------------------------------------------

        scheduleListRefresh() {
            // Un mensaje trae dos avisos (mensaje y sesión) y abrir el hilo un
            // tercero: se agrupan en una sola petición.
            clearTimeout(this.listTimer);
            this.listTimer = setTimeout(() => this.refreshList(), LIST_DEBOUNCE_MS);
        },

        async refreshList() {
            const current = this.$root.querySelector('[data-session-list]');
            if (!current) return;

            // Los filtros aplicados (los de la URL), no lo que esté a medio
            // escribir en el buscador.
            const url = new URL(config.listUrl, window.location.origin);
            const params = new URLSearchParams(window.location.search);
            ['q', 'unread'].forEach((key) => {
                if (params.get(key)) url.searchParams.set(key, params.get(key));
            });
            if (this.sessionId) url.searchParams.set('active', this.sessionId);

            try {
                const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
                if (!response.ok) return;
                const template = document.createElement('template');
                template.innerHTML = (await response.text()).trim();
                const fresh = template.content.querySelector('[data-session-list]');
                if (!fresh) return;
                const scroll = current.scrollTop;
                current.replaceWith(fresh);
                fresh.scrollTop = scroll;
            } catch (error) {
                // Se reintenta con el siguiente aviso o `resync`.
            }
        },

        // --- Visor de fotos -------------------------------------------------

        onThreadClick(event) {
            // Delegado: las burbujas que llegan por el socket no existían al
            // arrancar el componente.
            const trigger = event.target.closest('[data-media-open]');
            if (!trigger) return;
            this.openViewer(trigger);
        },

        openViewer(trigger) {
            this.viewerOpener = trigger;
            this.viewerBaseWidth = 0;
            this.viewer = {
                open: true,
                src: trigger.dataset.src,
                who: trigger.dataset.who,
                when: trigger.dataset.when,
                zoom: 1,
                loading: true,
                error: false,
            };
            document.body.style.overflow = 'hidden';
            this.$nextTick(() => this.$refs.viewerClose && this.$refs.viewerClose.focus());
        },

        closeViewer() {
            // Se suelta la imagen: no se queda en memoria ni en el DOM.
            this.viewer = { open: false, src: '', who: '', when: '', zoom: 1, loading: false, error: false };
            document.body.style.overflow = '';
            if (this.viewerOpener) this.viewerOpener.focus();
            this.viewerOpener = null;
        },

        retryViewer() {
            // Cada intento pide una URL firmada nueva (y deja su AccessLog).
            const base = this.viewer.src.split('?')[0];
            this.viewer.error = false;
            this.viewer.loading = true;
            this.viewer.src = `${base}?r=${Date.now()}`;
        },

        onViewerLoad() {
            this.viewer.loading = false;
            if (this.viewer.zoom === 1 && this.$refs.viewerImg) {
                this.viewerBaseWidth = this.$refs.viewerImg.clientWidth;
            }
        },

        viewerImageStyle() {
            if (this.viewer.zoom <= 1 || !this.viewerBaseWidth) {
                // Contra la ventana y no contra el contenedor: el escenario es
                // `w-max` (para poder desplazarse ampliada) y un 100 % crecería
                // con la propia foto. 2rem = el relleno; 6rem = cabecera + relleno.
                return 'max-width: calc(100vw - 2rem); max-height: calc(100vh - 6rem);';
            }
            return `width: ${Math.round(this.viewerBaseWidth * this.viewer.zoom)}px; max-width: none; max-height: none;`;
        },

        zoomBy(delta) {
            this.setZoom(this.viewer.zoom + delta);
        },

        toggleZoom(event) {
            if (this.viewer.zoom > 1) {
                this.setZoom(1);
                return;
            }
            // Amplía hacia el punto pulsado, no hacia la esquina.
            const rect = event.target.getBoundingClientRect();
            const fx = (event.clientX - rect.left) / rect.width;
            const fy = (event.clientY - rect.top) / rect.height;
            this.setZoom(2.5, fx, fy);
        },

        setZoom(zoom, fx = 0.5, fy = 0.5) {
            if (!this.viewerBaseWidth && this.$refs.viewerImg) {
                this.viewerBaseWidth = this.$refs.viewerImg.clientWidth;
            }
            this.viewer.zoom = Math.min(4, Math.max(1, zoom));
            this.$nextTick(() => {
                const stage = this.$refs.viewerStage;
                const image = this.$refs.viewerImg;
                if (!stage || !image) return;
                stage.scrollLeft = image.offsetLeft + image.clientWidth * fx - stage.clientWidth / 2;
                stage.scrollTop = image.offsetTop + image.clientHeight * fy - stage.clientHeight / 2;
            });
        },

        // --- Cambios de estructura -----------------------------------------

        markStale() {
            const composer = this.$root.querySelector('#chat-body');
            const writing = composer && composer.value.trim() !== '';
            if (writing) {
                // No se tira lo que está escribiendo: se avisa y decide ella.
                this.stale = true;
            } else {
                window.location.reload();
            }
        },
    }));
});

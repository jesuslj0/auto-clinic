/**
 * Desplegables de paciente y servicio del formulario de cita.
 *
 * Filtran en el navegador sobre la lista que el servidor precarga en un
 * `json_script`, y solo consultan al endpoint de búsqueda si esa lista venía
 * truncada (clínicas con más opciones que el tope de precarga).
 *
 * ¿Por qué un fichero estático y no un `<script>` dentro del parcial?
 *
 * 1. **Orden de evaluación.** El formulario llega por htmx al panel de la agenda.
 *    Si la factoría se definiera en el propio fragmento, Alpine podría procesar
 *    los nodos recién insertados antes de que ese `<script>` se hubiera evaluado:
 *    el `x-data` fallaría y TODAS las expresiones del subárbol saldrían por
 *    consola como «no definidas» (`abierto`, `cargando`, `cerrar`…). Cargado en el
 *    `<head>` de la página, existe mucho antes del primer intercambio.
 * 2. **Los formateadores no lo tocan.** Un `<script>` largo dentro de una
 *    plantilla es carne de formateador de HTML/JSX; basta un comentario envuelto
 *    en llaves al estilo JSX, o un paréntesis de cierre de más, para que el
 *    fichero deje de evaluarse ENTERO y el desplegable se caiga sin decir por
 *    qué. En un `.js` eso no pasa.
 *
 * A cambio, aquí no puede haber nada del contexto de Django: la configuración
 * (URLs, si la lista está truncada, la selección inicial) la pasa la plantilla al
 * llamar a la factoría.
 */

/**
 * Lee las opciones precargadas de su `<script type="application/json">`.
 * Si no está —por ejemplo si alguien usa el parcial del formulario sin el
 * `json_script`—, el desplegable se queda vacío pero NO revienta: reventar aquí
 * tumbaría el `x-data` entero y con él todo el formulario.
 */
function leerOpciones(id) {
    var nodo = document.getElementById(id);
    if (!nodo) return [];
    try {
        return JSON.parse(nodo.textContent) || [];
    } catch (error) {
        return [];
    }
}

window.appointmentPicker = function (config) {
    return {
        opciones: leerOpciones(config.opcionesId),
        truncada: Boolean(config.truncada),
        busquedaUrl: config.busquedaUrl,
        //: Cómo se convierte un resultado del endpoint en una opción.
        mapaRemoto: config.mapaRemoto,
        parametrosExtra: config.parametrosExtra || {},

        query: config.etiquetaInicial || '',
        seleccionadoId: config.idInicial || '',
        seleccionadoLabel: config.etiquetaInicial || '',
        abierto: false,
        resaltado: -1,
        cargando: false,
        remotas: [],
        _debounce: null,
        _peticion: 0,

        /** Sin acentos y en minúsculas, igual que el `haystack` del servidor. */
        normalizar: function (texto) {
            return String(texto).normalize('NFKD').replace(/\p{Diacritic}/gu, '').toLowerCase().trim();
        },

        /**
         * Lo que se pinta: la lista completa, o el filtro si se ha escrito algo.
         *
         * Con una opción ya elegida, el input muestra su etiqueta; ese texto NO
         * filtra, porque entonces abrir con la flecha dejaría a la vista solo la
         * opción que ya estaba seleccionada. Filtra únicamente lo que se teclea
         * (al teclear, `alEscribir` vacía la selección).
         */
        get visibles() {
            var filtrando = Boolean(this.query) && this.query !== this.seleccionadoLabel;
            var termino = filtrando ? this.normalizar(this.query) : '';
            var locales = termino
                ? this.opciones.filter(function (opcion) { return opcion.haystack.includes(termino); })
                : this.opciones;
            if (!this.remotas.length) return locales;
            var vistos = new Set(locales.map(function (opcion) { return String(opcion.id); }));
            return locales.concat(this.remotas.filter(function (opcion) {
                return !vistos.has(String(opcion.id));
            }));
        },

        get sinResultados() {
            return !this.cargando && this.visibles.length === 0;
        },

        /** Abre la lista y parte de la opción elegida, si hay alguna. */
        abrir: function () {
            this.abierto = true;
            var seleccionado = this.seleccionadoId;
            this.resaltado = seleccionado
                ? this.visibles.findIndex(function (opcion) {
                    return String(opcion.id) === seleccionado;
                })
                : -1;
        },

        /**
         * Al cerrar sin haber elegido, se repone la etiqueta de lo que está
         * seleccionado: un input con texto que no corresponde al id que se va a
         * enviar es la peor forma posible de equivocarse aquí.
         */
        cerrar: function () {
            this.abierto = false;
            this.query = this.seleccionadoLabel;
        },

        alEscribir: function () {
            // Escribir invalida la selección: lo que valga el campo se decide
            // eligiendo de la lista, no tecleando.
            this.seleccionadoId = '';
            this.seleccionadoLabel = '';
            this.abierto = true;
            this.resaltado = -1;
            this.remotas = [];
            if (!this.truncada) return;

            clearTimeout(this._debounce);
            var termino = this.query.trim();
            if (termino.length < 2) return;
            var self = this;
            this._debounce = setTimeout(function () { self.buscarEnServidor(termino); }, 250);
        },

        buscarEnServidor: function (termino) {
            var peticion = ++this._peticion;
            var self = this;
            this.cargando = true;

            var parametros = new URLSearchParams(
                Object.assign({ search: termino }, this.parametrosExtra)
            );
            fetch(this.busquedaUrl + '?' + parametros.toString(), {
                headers: { 'Accept': 'application/json' },
                credentials: 'same-origin',
            })
                .then(function (respuesta) {
                    return respuesta.ok ? respuesta.json() : { results: [] };
                })
                .then(function (datos) {
                    // Una respuesta que llega tarde no puede pisar a la última.
                    if (peticion !== self._peticion) return;
                    self.remotas = (datos.results || []).map(self.mapaRemoto);
                })
                .catch(function () {
                    if (peticion === self._peticion) self.remotas = [];
                })
                .finally(function () {
                    if (peticion === self._peticion) self.cargando = false;
                });
        },

        elegir: function (opcion) {
            this.seleccionadoId = String(opcion.id);
            this.seleccionadoLabel = opcion.label;
            this.query = opcion.label;
            this.abierto = false;
            this.remotas = [];
        },

        limpiar: function () {
            this.seleccionadoId = '';
            this.seleccionadoLabel = '';
            this.query = '';
            this.remotas = [];
            this.abierto = false;
        },

        alPulsarTecla: function (evento) {
            if (evento.key === 'Escape') {
                this.cerrar();
                return;
            }
            if (evento.key === 'ArrowDown' || evento.key === 'ArrowUp') {
                evento.preventDefault();
                if (!this.abierto) this.abrir();
                var total = this.visibles.length;
                if (!total) return;
                var paso = evento.key === 'ArrowDown' ? 1 : -1;
                this.resaltado = (this.resaltado + paso + total) % total;
                return;
            }
            if (evento.key === 'Enter' && this.abierto && this.resaltado >= 0) {
                evento.preventDefault();
                this.elegir(this.visibles[this.resaltado]);
            }
        },
    };
};

/**
 * Pacientes. `config`: { truncada, busquedaUrl, idInicial, etiquetaInicial }.
 * El `haystack` de las remotas va vacío porque el filtro local ya se aplicó
 * antes de pedirlas: se añaden tal cual al final de la lista.
 */
window.appointmentPatientPicker = function (config) {
    return window.appointmentPicker(Object.assign({}, config, {
        opcionesId: 'appointment-patient-options',
        mapaRemoto: function (paciente) {
            var nombre = (paciente.first_name + ' ' + paciente.last_name).trim();
            return {
                id: paciente.id,
                label: nombre,
                hint: paciente.phone || paciente.email || '',
                haystack: '',
            };
        },
    }));
};

/** Servicios. Mismo `config`; solo se buscan los activos. */
window.appointmentServicePicker = function (config) {
    return window.appointmentPicker(Object.assign({}, config, {
        opcionesId: 'appointment-service-options',
        parametrosExtra: { is_active: 'true' },
        mapaRemoto: function (servicio) {
            return {
                id: servicio.id,
                label: servicio.name,
                hint: servicio.duration_display + ' · ' + servicio.price_display,
                haystack: '',
            };
        },
    }));
};

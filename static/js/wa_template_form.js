/*
 * Formulario de plantillas de WhatsApp (componente Alpine `waTemplateForm`).
 *
 * Detecta las variables {{n}} del mensaje, pide un ejemplo para cada una y
 * pinta la vista previa tal como la verá el paciente. Las reglas de verdad las
 * valida Django; esto solo ahorra ir y volver.
 */
document.addEventListener('alpine:init', () => {
    const VARIABLE = /\{\{\s*(\d+)\s*\}\}/g;

    Alpine.data('waTemplateForm', (initial) => ({
        header: initial.header || '',
        body: initial.body || '',
        footer: initial.footer || '',
        examples: initial.examples || [],
        name: initial.name || '',
        dirty: !!initial.dirty,

        // Números de variable en el mensaje, sin repetir y ordenados.
        get variables() {
            const found = [...this.body.matchAll(VARIABLE)].map((m) => Number(m[1]));
            return [...new Set(found)].sort((a, b) => a - b);
        },

        // Mensaje con los ejemplos puestos; la variable sin ejemplo se queda como {{n}}.
        get previewBody() {
            return this.body.replace(VARIABLE, (whole, n) => this.examples[Number(n) - 1] || whole);
        },

        // «Recordatorio de cita» → recordatorio_de_cita, como lo normaliza Django.
        normalizeName() {
            this.name = this.name
                .normalize('NFD').replace(/[̀-ͯ]/g, '')
                .toLowerCase().replace(/[^a-z0-9_\s-]/g, '').trim().replace(/[\s-]+/g, '_');
        },

        // Añade la siguiente variable en la posición del cursor.
        addVariable() {
            const field = this.$refs.body;
            const token = `{{${this.variables.length + 1}}}`;
            const start = field.selectionStart ?? this.body.length;
            const end = field.selectionEnd ?? start;
            this.body = this.body.slice(0, start) + token + this.body.slice(end);
            this.dirty = true;
            this.$nextTick(() => {
                field.focus();
                field.setSelectionRange(start + token.length, start + token.length);
            });
        },
    }));
});

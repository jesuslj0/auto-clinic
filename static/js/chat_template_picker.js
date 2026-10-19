/*
 * Selector de plantilla del compositor (componente Alpine `chatTemplatePicker`).
 *
 * Con la ventana de 24 h cerrada solo se puede enviar una plantilla aprobada.
 * Aquí se elige, se rellena un valor por cada variable {{n}} y se ve el mensaje
 * tal como lo recibirá el paciente. Django vuelve a validar todo al enviar.
 */
document.addEventListener('alpine:init', () => {
    Alpine.data('chatTemplatePicker', (templates) => ({
        templates,
        selectedId: '',
        values: [],

        get selected() {
            return this.templates.find((t) => String(t.id) === String(this.selectedId)) || null;
        },

        // Al cambiar de plantilla se vacían los campos: cada una tiene los suyos.
        choose() {
            this.values = this.selected ? this.selected.variables.map(() => '') : [];
        },

        // El mensaje con lo escrito hasta ahora; la variable vacía se queda como {{n}}.
        get preview() {
            const t = this.selected;
            if (!t) return '';
            const body = t.body.replace(/\{\{\s*(\d+)\s*\}\}/g, (whole, n) => (this.values[Number(n) - 1] || '').trim() || whole);
            return [t.header, body, t.footer].filter(Boolean).join('\n\n');
        },

        get ready() {
            return !!this.selected && this.values.length === this.selected.variables.length
                && this.values.every((v) => (v || '').trim() !== '');
        },
    }));
});

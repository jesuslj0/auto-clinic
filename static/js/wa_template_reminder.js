/*
 * Configuración del recordatorio (componente Alpine `waReminderMapping`).
 *
 * Una fila por variable {{n}} de la plantilla, con el dato de la cita que irá en
 * ella, y la vista previa del mensaje con ejemplos. Django valida la elección.
 */
document.addEventListener('alpine:init', () => {
    const VARIABLE = /\{\{\s*(\d+)\s*\}\}/g;

    Alpine.data('waReminderMapping', (initial) => ({
        chosen: { ...initial.chosen },
        fields: initial.fields,

        example(key) {
            const field = this.fields.find((f) => f.key === key);
            return field ? field.example : '';
        },

        // El mensaje con el dato elegido en cada variable; sin elegir, se queda como {{n}}.
        get preview() {
            const body = initial.body.replace(VARIABLE, (whole, n) => this.example(this.chosen[n]) || whole);
            return [initial.header, body, initial.footer].filter(Boolean).join('\n\n');
        },

        get ready() {
            return initial.variables.every((n) => this.chosen[n]);
        },
    }));
});

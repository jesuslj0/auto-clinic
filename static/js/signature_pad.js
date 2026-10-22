/*
 * Lienzo de firma táctil (componente Alpine `signaturePad`).
 *
 * Pensado para tablet y móvil: eventos de puntero (ratón, dedo y lápiz con el
 * mismo código) y `touch-action: none` en el lienzo para que dibujar no haga
 * scroll. La firma se exporta como PNG sobre fondo blanco en el campo oculto
 * `signature_data` justo al enviar el formulario.
 *
 * Esto es una comodidad, no un control: el navegador no es de fiar. El servidor
 * decodifica la imagen y comprueba que lleva tinta (`clinical/signature.py`).
 *
 * Si el formulario vuelve con errores de otro campo, el campo oculto trae la firma
 * anterior y se repinta, para que el paciente no tenga que firmar dos veces.
 */
document.addEventListener('alpine:init', () => {
    const INK = '#0f172a';
    const PAPER = '#ffffff';
    const LINE_WIDTH = 2.5;
    // Con pantallas de más densidad el PNG crece sin ganar nada legible.
    const MAX_RATIO = 2;

    Alpine.data('signaturePad', () => ({
        hasInk: false,
        error: '',
        drawing: false,
        ctx: null,

        init() {
            const canvas = this.$refs.canvas;
            this.ctx = canvas.getContext('2d');
            this.fit();
            const previous = this.$refs.data.value;
            if (previous) this.restore(previous);
            // Girar el dispositivo cambia el ancho disponible.
            window.addEventListener('resize', () => this.fit(true));
        },

        ratio() {
            return Math.min(window.devicePixelRatio || 1, MAX_RATIO);
        },

        // Ajusta la resolución del lienzo a su tamaño en pantalla.
        fit(keep = false) {
            const canvas = this.$refs.canvas;
            const saved = keep && this.hasInk ? this.exportPng() : null;
            const ratio = this.ratio();
            const width = canvas.clientWidth;
            const height = canvas.clientHeight;
            if (!width || (canvas.width === Math.round(width * ratio) && canvas.height === Math.round(height * ratio))) return;
            canvas.width = Math.round(width * ratio);
            canvas.height = Math.round(height * ratio);
            this.paper();
            if (saved) this.restore(saved);
        },

        paper() {
            const { width, height } = this.$refs.canvas;
            this.ctx.fillStyle = PAPER;
            this.ctx.fillRect(0, 0, width, height);
            this.ctx.strokeStyle = INK;
            this.ctx.fillStyle = INK;
            this.ctx.lineCap = 'round';
            this.ctx.lineJoin = 'round';
            this.ctx.lineWidth = LINE_WIDTH * this.ratio();
        },

        restore(dataUrl) {
            const image = new Image();
            image.onload = () => {
                const { width, height } = this.$refs.canvas;
                this.ctx.drawImage(image, 0, 0, width, height);
                this.hasInk = true;
            };
            image.src = dataUrl;
        },

        point(event) {
            const rect = this.$refs.canvas.getBoundingClientRect();
            const ratio = this.ratio();
            return [(event.clientX - rect.left) * ratio, (event.clientY - rect.top) * ratio];
        },

        start(event) {
            event.preventDefault();
            this.$refs.canvas.setPointerCapture(event.pointerId);
            this.drawing = true;
            this.error = '';
            const [x, y] = this.point(event);
            this.ctx.beginPath();
            this.ctx.moveTo(x, y);
            // Un toque suelto también deja marca.
            this.ctx.arc(x, y, this.ctx.lineWidth / 2, 0, Math.PI * 2);
            this.ctx.fill();
            this.ctx.beginPath();
            this.ctx.moveTo(x, y);
            this.hasInk = true;
        },

        move(event) {
            if (!this.drawing) return;
            event.preventDefault();
            const [x, y] = this.point(event);
            this.ctx.lineTo(x, y);
            this.ctx.stroke();
            this.ctx.beginPath();
            this.ctx.moveTo(x, y);
        },

        end() {
            this.drawing = false;
        },

        clear() {
            this.paper();
            this.hasInk = false;
            this.$refs.data.value = '';
        },

        // PNG sobre fondo blanco: el lienzo ya se pinta sobre blanco.
        exportPng() {
            return this.$refs.canvas.toDataURL('image/png');
        },

        // Antes de enviar: sin tinta no se envía; con ella, la firma viaja en el campo oculto.
        prepare(event) {
            if (!this.hasInk) {
                event.preventDefault();
                this.error = 'Falta la firma del paciente: dibújala en el recuadro.';
                this.$refs.canvas.focus();
                return;
            }
            this.$refs.data.value = this.exportPng();
        },
    }));
});

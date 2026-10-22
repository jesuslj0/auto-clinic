/*
 * Ficha pública del paciente (`templates/intake/fill.html`).
 *
 * JavaScript sin dependencias: la página no carga librerías de terceros. Hace tres
 * cosas, todas comodidades —el servidor valida y decide todo—:
 *
 *   1. Muestra los datos del representante solo cuando hacen falta.
 *   2. Lienzo de firma táctil (mismo criterio que `signature_pad.js`, sin Alpine):
 *      PNG sobre fondo blanco que viaja en un campo oculto al enviar.
 *   3. Evita el doble envío: el enlace es de un solo uso.
 */
(function () {
    'use strict';

    var form = document.querySelector('[data-intake-form]');
    if (!form) return;

    // --- Representante -----------------------------------------------------
    var behalf = form.querySelector('[data-behalf]');
    var toggle = form.querySelector('input[type="checkbox"][name$="on_behalf"]');
    if (behalf && toggle && !behalf.hasAttribute('data-always')) {
        var sync = function () { behalf.hidden = !toggle.checked; };
        toggle.addEventListener('change', sync);
        sync();
    }

    // --- Firma -------------------------------------------------------------
    var INK = '#0f172a';
    var PAPER = '#ffffff';
    var LINE_WIDTH = 2.5;
    var MAX_RATIO = 2;

    var box = form.querySelector('[data-signature]');
    var pad = null;
    if (box) {
        var canvas = box.querySelector('[data-signature-canvas]');
        var data = box.querySelector('input[type="hidden"]');
        var clearButton = box.querySelector('[data-signature-clear]');
        var errorBox = box.querySelector('[data-signature-error]');
        var ctx = canvas.getContext('2d');
        var hasInk = false;
        var drawing = false;

        var ratio = function () { return Math.min(window.devicePixelRatio || 1, MAX_RATIO); };

        var paper = function () {
            ctx.fillStyle = PAPER;
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.strokeStyle = INK;
            ctx.fillStyle = INK;
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.lineWidth = LINE_WIDTH * ratio();
        };

        var showInk = function (value) {
            hasInk = value;
            if (clearButton) clearButton.hidden = !value;
        };

        var restore = function (url) {
            var image = new Image();
            image.onload = function () {
                ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
                showInk(true);
            };
            image.src = url;
        };

        var fit = function (keep) {
            var width = canvas.clientWidth;
            var height = canvas.clientHeight;
            var r = ratio();
            if (!width || (canvas.width === Math.round(width * r) && canvas.height === Math.round(height * r))) return;
            var saved = keep && hasInk ? canvas.toDataURL('image/png') : null;
            canvas.width = Math.round(width * r);
            canvas.height = Math.round(height * r);
            paper();
            if (saved) restore(saved);
        };

        var point = function (event) {
            var rect = canvas.getBoundingClientRect();
            var r = ratio();
            return [(event.clientX - rect.left) * r, (event.clientY - rect.top) * r];
        };

        var setError = function (message) {
            if (!errorBox) return;
            errorBox.textContent = message || '';
            errorBox.hidden = !message;
        };

        canvas.addEventListener('pointerdown', function (event) {
            event.preventDefault();
            canvas.setPointerCapture(event.pointerId);
            drawing = true;
            setError('');
            var p = point(event);
            ctx.beginPath();
            ctx.moveTo(p[0], p[1]);
            // Un toque suelto también deja marca.
            ctx.arc(p[0], p[1], ctx.lineWidth / 2, 0, Math.PI * 2);
            ctx.fill();
            ctx.beginPath();
            ctx.moveTo(p[0], p[1]);
            showInk(true);
        });
        canvas.addEventListener('pointermove', function (event) {
            if (!drawing) return;
            event.preventDefault();
            var p = point(event);
            ctx.lineTo(p[0], p[1]);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(p[0], p[1]);
        });
        ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (name) {
            canvas.addEventListener(name, function () { drawing = false; });
        });
        if (clearButton) {
            clearButton.addEventListener('click', function () {
                paper();
                showInk(false);
                data.value = '';
            });
        }
        window.addEventListener('resize', function () { fit(true); });

        fit(false);
        // Si el formulario vuelve con errores de otro campo, se repinta la firma.
        if (data.value) restore(data.value);

        pad = {
            ready: function () {
                if (!hasInk) {
                    setError('Falta tu firma: dibújala en el recuadro.');
                    canvas.focus();
                    return false;
                }
                data.value = canvas.toDataURL('image/png');
                return true;
            }
        };
    }

    // --- Envío -------------------------------------------------------------
    var sending = false;
    form.addEventListener('submit', function (event) {
        if (sending) { event.preventDefault(); return; }
        if (pad && !pad.ready()) { event.preventDefault(); return; }
        sending = true;
        var button = form.querySelector('[data-submit]');
        if (button) button.disabled = true;
    });
})();

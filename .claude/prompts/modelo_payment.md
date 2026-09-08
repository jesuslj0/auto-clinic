Contexto: Trabajo en Autoclinic (Django + HTMX + Alpine), un CRM clínico. Voy a añadir el registro de cobros sobre el modelo PatientInvoice existente. Te paso mi modelo de factura al final para que copies sus patrones exactamente — no inventes un estilo nuevo.

Fase 1 — Explora antes de escribir nada. Lee y resúmeme, con rutas de archivo y fragmentos relevantes:

next_invoice_number(clinic) — el patrón de numeración correlativa (transacción, bloqueo, unicidad por clínica). Reutilizaré su forma para la numeración de recibos.
SoftDeleteModel, TimeStampedModel, PatientInvoiceManager, AllObjectsManager, y el mecanismo de snapshot _frozen_state / FROZEN_FIELDS.
El sistema de auditoría (AccessLog, ChangeLog) y cómo se rellenan created_by/created_at.
Las vistas HTMX existentes (URLs por pestaña, hx-push-url, cómo devuelves fragmentos), para que la UI de pagos siga las mismas convenciones.

No propongas código hasta que confirme lo que hay.

Fase 2 — Construye el modelo Payment (tras mi visto bueno). Replica PatientInvoice exactamente. Diseño objetivo:

Payment(SoftDeleteModel, TimeStampedModel) — una fila por evento de cobro. Una PatientInvoice puede tener varios Payment (cobros parciales).
FKs: invoice → PatientInvoice y clinic → Clinic, ambos on_delete=DO_NOTHING, db_constraint=False, con el mismo razonamiento que la factura: un recibo es un documento fiscal que debe sobrevivir a los registros a los que apunta.
Snapshot congelado (FROZEN_FIELDS, mismo guardián de inmutabilidad from_db/save que la factura): una vez confirmado el pago, no se reescribe jamás. Congela clinic_id, invoice_id, frozen_patient_name, frozen_invoice_number, receipt_number, paid_at, amount, method. El recibo sigue siendo legible aunque luego se borre el paciente o la factura.
Campos:
amount — DecimalField(max_digits=10, decimal_places=2), MinValueValidator > 0 (un recibo de cero euros no tiene sentido).
method — TextChoices: CARD (Tarjeta), TRANSFER (Transferencia), BIZUM (Bizum), CASH (Efectivo).
paid_at — DateTimeField, cuándo entró el dinero.
receipt_number — serie correlativa propia (p. ej. R-2026-00001), separada de la serie de facturas, asignada al confirmar, nunca en un estado tipo borrador.
frozen_patient_name y frozen_invoice_number — copiados al confirmar para que el recibo se lea de forma autónoma.
Reglas de negocio (aplícalas a nivel de modelo, no solo en formularios):
Un pago solo se puede registrar contra una factura emitida (issued) — nunca borrador, nunca anulada. Lanza una excepción de dominio análoga a InvoiceNotIssued.
La suma de los pagos nunca puede superar el total de la factura. Rechaza el sobrepago.
receipt_number asignado bajo transaction.atomic con bloqueo de fila, replicando next_invoice_number. Añade next_receipt_number(clinic) junto a él.
Restricciones de BD (segundo nivel, como la factura):
CheckConstraint amount > 0.
unique_together = ('clinic', 'receipt_number').
Índice sobre ('invoice',) para la agregación en el listado.
Estado de pago derivado en la factura — NO lo almacenes. Añade un método de manager/queryset que anote cada factura con amount_collected (Sum de sus pagos no borrados) y derive el estado: 0 → impagada, 0 < x < total → parcial, >= total → pagada. Una sola query para el listado, agregando sobre importes reales de pago.
Borrado: un pago es un recibo fiscal que consumió un número de la serie — misma regla que una factura emitida, no se puede hard-delete. Decide si el void/reembolso entra ahora o se aplaza, y avísame en vez de permitir el borrado silenciosamente.
Migración para el nuevo modelo.

Reglas de trabajo:

Reutiliza los patrones existentes; si algo no encaja con el enfoque de PatientInvoice, dímelo antes de desviarte.
Cambios en incrementos pequeños y revisables; muéstrame el diff antes de aplicar.
NO modifiques PatientInvoice, la capa de auditoría ni las migraciones existentes sin avisar explícitamente de qué cambias y por qué. (Añadir el método de anotación al queryset de la factura está bien, pero muéstramelo.)
Solo euros, un solo idioma — sin campo de moneda, sin conciliación bancaria, sin campos de pasarela.


Empieza por la Fase 1.
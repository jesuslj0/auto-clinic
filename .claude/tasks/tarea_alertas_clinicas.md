Implement clinical alerts for Autoclinic (Django + HTMX + Alpine.js) — model and manual creation only. One model:

ClinicalAlert: FK to patient, alert_type (enum for the podiatry-critical ones: diabetes, peripheral_vascular_disease, neuropathy, anticoagulants, allergy_latex, allergy_local_anesthetics, plus a generic other), severity (enum: critical, warning, info), source (enum: manual, derived), free-text note, is_active flag, created_at, optional FK to the professional who created it (created_by, nullable), and an optional FK to the QuestionnaireResponse it was derived from (source_response, nullable — used later by the derivation engine, null for manual alerts).

Requirements:

This task covers the model, migrations, admin, and manual create/deactivate flow only. Do NOT build the derivation engine yet — but the schema must already support it via source and source_response.
Manual alerts: a professional can add and deactivate alerts on a patient. Deactivating sets is_active=False rather than deleting (keep the history).
Add a helper/manager method active_critical_for(patient) returning active alerts with severity=critical, ordered for display. This is what the patient record will render as a non-dismissible block.
The "non-ignorable" behavior is presentation, not model: don't enforce it here. Just expose the query cleanly.
Tests: (1) a manual alert defaults to source=manual with null source_response, (2) deactivating preserves the row, (3) active_critical_for returns only active critical alerts for the given patient.

Don't implement the derivation engine, the anamnesis-to-alert rules, or the patient-record UI yet. Model, admin, manual flow, and tests only.
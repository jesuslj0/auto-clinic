Implement the anamnesis-to-alert derivation engine for Autoclinic (Django). Builds on the existing ClinicalAlert model and the versioned anamnesis (QuestionnaireResponse with its frozen snapshot).

Core function: derive_alerts(response: QuestionnaireResponse). It reads the response's snapshot (literal questions + answers), applies a rule set, and creates/updates ClinicalAlert rows with source=derived and source_response pointing to that response.

Rule engine design:

Define rules as data, not hardcoded if branches. Each rule maps a condition on the snapshot to an alert spec (alert_type, severity, note template). Store them as a list/registry of rule objects so new rules are added without touching engine logic.
Matching a snapshot question shouldn't depend on question order or on free text. Rules should key off a stable identifier — add a code field to Question (stable slug like has_diabetes, takes_anticoagulants, allergy_latex) that gets copied into the snapshot, and match on that code + answer value. If Question.code doesn't exist yet, add it (nullable, plus migration) and include it in the snapshot copy.
Initial podiatry rules: diabetes → diabetes/critical; peripheral vascular disease → peripheral_vascular_disease/critical; neuropathy → neuropathy/critical; anticoagulant use → anticoagulants/critical; latex allergy → allergy_latex/critical; local-anesthetic allergy → allergy_local_anesthetics/critical.

Idempotency & corrections (the important part):

Running derive_alerts twice on the same response must NOT create duplicates. Key derived alerts by (patient, alert_type, source_response).
If the anamnesis is re-filled (a NEW QuestionnaireResponse), derive from the new one. Provide a strategy for superseding alerts derived from a prior response for the same patient+template: deactivate derived alerts whose source_response is an older response of the same template when a newer response no longer supports them. Don't touch manual alerts (source=manual) ever.
If a correction means a condition is now absent, the previously derived alert for that alert_type+source_response should be deactivated (is_active=False), not deleted.

Integration:

Trigger derivation when a QuestionnaireResponse is created (signal or explicit call in the save flow — prefer an explicit service call over a signal so the WhatsApp/n8n API path can invoke the same function).
Keep the engine pure and unit-testable: derive_alerts takes a response and does the DB work; the rule evaluation (snapshot → list of alert specs) is a separate pure function with no DB access.

Tests: (1) a snapshot with diabetes=yes creates one critical diabetes derived alert; (2) running twice creates no duplicate; (3) a newer response without diabetes deactivates the previously derived diabetes alert but leaves a manual diabetes alert untouched; (4) the pure rule-evaluation function returns the right specs for a given snapshot with no DB involved; (5) multiple conditions in one snapshot produce multiple alerts.

Don't build the patient-record UI. Engine, rules registry, Question.code migration, integration hook, and tests only.
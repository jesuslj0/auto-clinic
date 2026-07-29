Implement versioned anamnesis (medical history questionnaire) for Autoclinic (Django + HTMX + Alpine.js). Four models:

QuestionnaireTemplate: the logical questionnaire (name, specialty/type, active flag). Groups versions together.
TemplateVersion: FK to QuestionnaireTemplate, version number, published-at date, is_current flag. Only one current version per template. Immutable once published.
Question: FK to TemplateVersion, text, answer type (boolean, free text, single choice, multiple choice, number), order, required flag, options (JSON, used for choice types). Questions belong to a specific version and cannot be edited after the version is published.
QuestionnaireResponse: FK to the TemplateVersion used, FK to patient and to episode, timestamp, a source field (enum: professional, patient_web, patient_whatsapp), an optional FK to the professional (created_by, nullable — a WhatsApp/web response has no logged-in professional), and a JSON snapshot field storing the full list of literal questions + given answers at the moment of filling. Not just references to Question: each question's text and its answer are frozen in the snapshot.

Requirements:
  
Immutable snapshot pattern: when saving a QuestionnaireResponse, copy the literal state of that version's questions; editing or unpublishing the version afterwards must not alter already-saved responses.
Publishing a new TemplateVersion must not touch historical responses.
Validation: disallow editing Question or TemplateVersion once the version is published (use a flag and block in save()/admin).
created_by is nullable so responses coming from a patient form or the WhatsApp agent (via n8n) don't require an authenticated professional. source records the channel.
Include migrations, admin registration, and tests verifying: (1) the snapshot doesn't change when the version is modified, (2) only one current version per template, (3) responses keep the literal question text, (4) a response can be created with source=patient_whatsapp and no created_by.

Don't implement the filling UI, the web form, the n8n integration, or the alerts engine yet. Models, admin, and tests only.
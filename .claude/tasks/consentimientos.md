Implement informed consent for Autoclinic (Django) — models, admin, tests only. No UI. Three models:

PlantillaConsentimiento: the logical consent document (name, specialty/type, active flag). Groups versions.
VersionConsentimiento: FK to PlantillaConsentimiento, version number, full consent text, published-at date, is_current flag (only one current per template), immutable once published.
ConsentimientoFirmado: FK to the VersionConsentimiento signed, FK to patient and to episode, signature_image (FileField using the private clinical_media R2 storage — a signature is health-adjacent personal data, serve it protected like lesion photos), signed_at timestamp, and text_copy (a full copy of the consent text at signing time — NOT just the FK to the version).

Requirements:

Deliberate redundancy: text_copy stores the literal consent text signed, independent of the version FK. Even if the version is later changed or the template restructured, the signed record proves exactly what the patient agreed to.
Same immutability rules as anamnesis: a published VersionConsentimiento cannot be edited; publishing a new version doesn't touch signed records.
The signature image uses the same private storage + signed-URL + permission-checked serving as lesion attachments (reuse the pattern from C2).

Tests: (1) signing copies the version text into text_copy; (2) editing/replacing the version afterwards doesn't change any ConsentimientoFirmado.text_copy; (3) only one current version per template; (4) the signature image is stored privately and served only via a permission-checked signed URL.

No UI. Models, admin, immutability logic, protected signature serving, and tests only.
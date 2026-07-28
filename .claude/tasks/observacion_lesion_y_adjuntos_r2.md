Implement lesion follow-up observations with clinical photo attachments for Autoclinic (Django) — models, private R2 storage, protected serving, and tests only. No UI/templates/JS.

Storage setup (Cloudflare R2, S3-compatible via django-storages):

Add a dedicated private clinical_media storage backend in STORAGES using storages.backends.s3.S3Storage, configured from env vars: R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_ENDPOINT_URL.
Options: region_name="auto", signature_version="s3v4", default_acl=None (private, never public-read), querystring_auth=True, querystring_expire=600 (10-min signed URLs), file_overwrite=False, addressing_style="virtual".
Keep this storage separate from static files. Clinical media must never be served from a public MEDIA_URL.
Install/require django-storages and boto3.

Models:

ObservacionLesion: FK to Lesion, FK to visit, observed_at (date), measurements (structured — e.g. length_mm, width_mm, depth_mm as nullable fields, or a small JSON), description (text), created_at, optional created_by FK (nullable). One lesion has many observations over time.
LesionAttachment: FK to ObservacionLesion, file (FileField using the clinical_media storage, upload_to a UUID-based path so keys are non-guessable, never patient_x_foot.jpg), mime_type, size_bytes, optional checksum, source (enum: professional, patient_whatsapp, patient_web), uploaded_at. Store the bucket key/path only — never persist a URL; signed URLs are generated on demand.

File validation (applies to all uploads, stricter for external ones):

Validate real MIME type (not just extension), enforce an image allow-list (jpeg, png, webp), and a max size. Reject anything else.

Protected serving:

Provide a view/helper that returns a lesion attachment's signed URL ONLY after checking the requesting user has permission to view that attachment's patient (attachment → observation → lesion → episode → patient). The signed URL is generated at request time (via the storage's .url), never stored.
Do not expose attachments through any public/unauthenticated path.

Tests: (1) an attachment file is stored via the clinical_media backend with a UUID key, not a readable name; (2) .url produces a signed, expiring URL (querystring auth present); (3) a non-permitted user is denied the signed URL; (4) a non-image or oversized file is rejected by validation; (5) one lesion aggregates multiple observations ordered by date.

Do NOT implement WhatsApp/n8n intake, the SVG map UI, or the evolution/comparison view yet. Models, storage config, validation, protected serving helper, admin, and tests only.
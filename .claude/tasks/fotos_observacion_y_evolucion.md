Implement clinical photo upload and the lesion evolution view for Autoclinic (Django + HTMX + Alpine.js). Builds on the lesion detail panel with observations. The LesionAttachment model, the private clinical_media R2 storage, file validation, and the permission-checked signed-URL serving helper already exist from an earlier task.

Build:

Photo upload within the new-observation form: attach one or more images to an observation. On upload, files go to the private R2 storage with UUID keys, validated (real MIME, image allow-list, max size) — reuse the existing validation/serving. Store the bucket key, never a URL.
In the observation list, render each observation's photos as thumbnails using on-demand signed URLs (generated at render time via the permission-checked helper, expiring — never persisted).
An evolution view for a lesion: its observations laid out chronologically with their photos, so the professional can see the lesion's progression over time. Include a side-by-side compare mode (pick two observations, show their photos next to each other). This is the product's differential feature — make it clean.

Rules:

Every image URL is a short-lived signed URL generated only after the permission check. No public paths, no persisted URLs.
Upload goes through the existing validation (reject non-images / oversized).
Alpine for compare-mode UI state (which two observations are selected); HTMX/server for fetching signed URLs and observation data.
Handle the empty state (lesion with no photos yet) and loading gracefully.

Do NOT implement WhatsApp/n8n photo intake here — that's a separate task. Professional-uploaded photos + evolution/compare view only.

Note: signed URLs expire (~10 min). For the evolution view where the user may linger, ensure thumbnails still resolve — either lengthen expiry for this view or handle re-fetch. Flag your approach.
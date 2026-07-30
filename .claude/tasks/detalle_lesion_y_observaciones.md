Implement the lesion detail panel and observation creation for Autoclinic (Django + HTMX + Alpine.js). Builds on the map with clickable markers. The Lesion and ObservacionLesion models already exist (observation: FK lesion, FK visit, observed_at, measurements, description, created_by). Photos/attachments are NOT part of this prompt.

Build:

Clicking an existing marker loads that lesion's detail panel via HTMX into a side panel (or bottom sheet on mobile). Panel shows: lesion data (type, zone, laterality/view, status, detected_at, resolved_at) and its list of observations ordered by date (most recent first).
A "mark resolved" action on an active lesion: sets status=resolved and requires resolved_at (reuse model validation). HTMX updates the panel and the marker color.
A "new observation" form within the panel: observed_at, measurements (length/width/depth), description, linked visit. On submit, HTMX creates the ObservacionLesion and re-renders the observation list.

Rules:

Panel open/close and which lesion is selected: ephemeral, can live in Alpine, but the panel CONTENT is fetched via HTMX (it holds clinical data — don't preload all lesions' details into the page).
Permission check on both the detail view and the observation-create view (HTMX partials are directly-callable URLs).
No photo upload in this prompt — the observation form has everything EXCEPT attachments.

Do NOT implement: photo/attachment upload, signed-URL serving, or the evolution/comparison view. Detail panel + observations (text/measurements only) here.
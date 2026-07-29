Implement the foot lesion map in READ-ONLY mode for Autoclinic (Django + HTMX + Alpine.js). Render the SVG and paint existing lesion markers only — NO click capture, NO create form, NO detail panel yet.

Context: the Lesion model already exists (FK to episode, laterality [left/right], view [dorsal/plantar/medial/lateral], anatomical_zone, normalized x/y floats 0–1, lesion_type, status [active/resolved]). A manager helper for_view(episode, laterality, view) already returns the matching lesions.

Build:

A lesions partial loaded into the patient record's content panel via HTMX (this is the "lesiones" tab). It must respond partial-or-full based on HX-Request (django-htmx request.htmx): partial for HTMX, full patient record for direct load.
Inside the partial: view tabs (dorsal / plantar / medial / lateral) and a left/right laterality selector. Active view + laterality is ephemeral UI state held in Alpine, not the server.
A placeholder foot SVG with a viewBox and a handful of labeled anatomical zones. Keep it simple; real artwork gets swapped later since zones are coded.
Render markers for the lesions matching the current view + laterality at position (x · svgWidth, y · svgHeight), computed from the SVG's intrinsic coordinate system (use the viewBox units so it scales at any display size — do NOT hardcode pixel dimensions). Markers: distinct color for active vs resolved. Markers are visual only for now (no click behavior).
Switching view or laterality re-filters which markers show. Two options — pick the cleaner: either (a) all lesions passed once and Alpine shows/hides by matching view+laterality, or (b) HTMX re-fetches the marker set per view. Given the low volume of lesions per foot, option (a) in Alpine is likely simpler and avoids round-trips; justify briefly whichever you choose.

Rules:

Alpine only for ephemeral UI state (active view, active laterality). No data logic in Alpine.
Coordinates are normalized 0–1 fractions of the SVG viewBox; markers must position correctly regardless of rendered size. This is the key thing to get right.
Permission check on the view: the requesting user must be allowed to see this patient. Check it even though it's a partial (an HTMX partial is a directly-callable URL).
Use existing seed data (or extend it) so the map shows a patient with several lesions across different views/lateralities to verify filtering and positioning.

Do NOT implement: click-to-capture coordinates, the add-lesion form, the lesion detail panel, observations, photos, or the evolution view. Read-only rendering and view/laterality switching only.

Deliver the view, the partial template, the placeholder SVG, and note any seed additions.
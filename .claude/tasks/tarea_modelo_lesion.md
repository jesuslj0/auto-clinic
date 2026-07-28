Implement the Lesion model for Autoclinic (Django) — model, validation, admin, and tests only. No UI, no views, no templates, no JavaScript.

Lesion: FK to episode, laterality (enum: left, right), view (enum: dorsal, plantar, medial, lateral), anatomical_zone (CharField with choices — stable coded slugs like hallux, first_metatarsal, heel; NOT free text), normalized coordinates x and y (FloatField, 0.0–1.0), lesion_type (enum: ulcer, hyperkeratosis, wound, blister, other), status (enum: active, resolved, default active), detected_at (date), resolved_at (nullable date), created_at (auto), optional created_by FK (nullable).

Requirements:

x and y are normalized fractions of the SVG dimensions (0–1), never pixels. Add validators enforcing the 0.0–1.0 range.
anatomical_zone is the stable clinical identifier that survives an SVG redesign; coordinates are only for rendering. Both are stored independently.
Validate that when status=resolved, resolved_at is required; when status=active, resolved_at must be null. Enforce this in clean().
A Lesion belongs to one episode and is fixed in location once created.
Provide a manager/queryset helper for_view(episode, laterality, view) returning the lesions matching that episode + laterality + view, for later rendering.

Include migrations, admin registration, and tests: (1) x or y outside 0–1 is rejected; (2) status=resolved without resolved_at fails validation; (3) status=active with a resolved_at set fails validation; (4) for_view returns only lesions matching episode+laterality+view; (5) anatomical_zone is stored and queryable independently of coordinates.

Don't implement ObservacionLesion, photos/attachments, any view/template/UI, or WhatsApp intake. Model, validation, manager helper, admin, and tests only.
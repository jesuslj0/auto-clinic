Implement performed procedures for Autoclinic (Django) — model, admin, tests only. No UI.

ProcedimientoRealizado: FK to visit, FK to Servicio (existing catalog), frozen_price (DecimalField — copied from the Servicio at creation time in save(), NEVER read live from the catalog afterwards), frozen_service_name (CharField — snapshot of the service name at the time, for the same immutability reason), affected_zone_or_piece (CharField/coded), performed_at, created_at, optional created_by FK (nullable).

Requirements:

Frozen-price snapshot pattern (same as anamnesis/consent): on first save, copy price and name from the linked Servicio; later changes to the catalog must not alter existing procedures. Never re-read from the catalog on subsequent saves.
This links the clinical layer to billing without duplicating the service catalog.

Tests: (1) creating a procedure copies the current Servicio price into frozen_price; (2) changing the Servicio price afterwards does NOT change the procedure's frozen_price; (3) frozen_service_name is likewise snapshotted.

No UI. Model, admin, snapshot logic, and tests only.
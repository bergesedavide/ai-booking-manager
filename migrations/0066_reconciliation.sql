-- Additive migration: existing rows remain legacy (version 1).
BEGIN;
ALTER TABLE public.booking_operations
    ADD COLUMN calendar_id text,
    ADD COLUMN owner_ref text,
    ADD COLUMN execution_version integer NOT NULL DEFAULT 1,
    ADD COLUMN google_write_started_at timestamptz,
    ADD COLUMN last_reconciled_at timestamptz,
    ADD COLUMN last_reconciliation_outcome text;
ALTER TABLE public.booking_operations ADD CONSTRAINT booking_operations_v2_context
    CHECK (execution_version < 2 OR
        (calendar_id IS NOT NULL AND btrim(calendar_id) <> '' AND calendar_id <> 'primary'
         AND owner_ref IS NOT NULL AND btrim(owner_ref) <> '' AND google_event_id IS NOT NULL));
COMMIT;

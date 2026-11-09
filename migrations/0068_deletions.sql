-- Apply once with bot stopped. Adds only the AI deletion journal.
BEGIN;
CREATE TABLE public.booking_deletions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    booking_operation_id bigint NOT NULL REFERENCES public.booking_operations(id),
    request_key text NOT NULL,
    owner_ref text NOT NULL CHECK (btrim(owner_ref) <> ''),
    calendar_id text NOT NULL CHECK (btrim(calendar_id) <> '' AND calendar_id <> 'primary'),
    google_event_id text NOT NULL,
    expected_etag text NOT NULL CHECK (btrim(expected_etag) <> ''),
    original_start timestamptz NOT NULL,
    original_end timestamptz NOT NULL CHECK (original_end > original_start),
    status text NOT NULL CHECK (status IN ('PENDING','DELETED','FAILED','UNKNOWN')),
    google_write_started_at timestamptz,
    outcome text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_ref, calendar_id, request_key)
);
CREATE UNIQUE INDEX booking_deletions_blocked_event
ON public.booking_deletions (owner_ref, calendar_id, google_event_id)
WHERE status IN ('PENDING','UNKNOWN','DELETED');
COMMIT;

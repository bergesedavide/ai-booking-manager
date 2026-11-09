-- Apply once, with the bot stopped. No historical event attribution.
BEGIN;
CREATE TABLE public.booking_reschedules (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    booking_operation_id bigint NOT NULL REFERENCES public.booking_operations(id),
    request_key text NOT NULL,
    owner_ref text NOT NULL CHECK (btrim(owner_ref) <> ''),
    calendar_id text NOT NULL CHECK (btrim(calendar_id) <> '' AND calendar_id <> 'primary'),
    google_event_id text NOT NULL,
    expected_etag text NOT NULL,
    original_start timestamptz NOT NULL,
    original_end timestamptz NOT NULL,
    requested_start timestamptz NOT NULL,
    requested_end timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('PENDING','UPDATED','FAILED','UNKNOWN')),
    google_write_started_at timestamptz,
    outcome text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_ref, calendar_id, request_key),
    CHECK (original_end > original_start AND requested_end > requested_start),
    CHECK (original_end - original_start = requested_end - requested_start)
);
-- An uncertain write blocks further AI moves of the same event.
CREATE UNIQUE INDEX booking_reschedules_unresolved_event
ON public.booking_reschedules (owner_ref, calendar_id, google_event_id)
WHERE status IN ('PENDING','UNKNOWN');
COMMIT;

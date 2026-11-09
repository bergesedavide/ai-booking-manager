-- Bootstrap esclusivamente per database nuovi.
-- Applicare prima di 0066, 0067 e 0068.
-- Non applicare al database locale già inizializzato.
BEGIN;

CREATE TABLE public.booking_operations (
    id bigint GENERATED ALWAYS AS IDENTITY (
        SEQUENCE NAME public.booking_operations_id_seq
        START WITH 1
        INCREMENT BY 1
        NO MINVALUE
        NO MAXVALUE
        CACHE 1
    ) NOT NULL,

    request_key text NOT NULL,
    google_event_id text,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT booking_operations_pkey
        PRIMARY KEY (id),

    CONSTRAINT booking_operations_request_key_key
        UNIQUE (request_key),

    CONSTRAINT booking_operations_google_event_id_key
        UNIQUE (google_event_id),

    CONSTRAINT booking_operations_status_check
        CHECK (status IN ('PENDING', 'CREATED', 'FAILED', 'UNKNOWN'))
);

COMMIT;

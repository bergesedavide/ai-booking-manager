-- M08.3.1: apply once after 0081 and separate HQ approval, with all client
-- writers stopped/upgraded. No historical receipts are inferred or inserted.
BEGIN;

CREATE TABLE public.client_operations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trainer_id bigint NOT NULL REFERENCES public.trainers(id),
    request_key text NOT NULL CHECK (btrim(request_key) <> ''),
    first_name_key text NOT NULL CHECK (btrim(first_name_key) <> ''),
    last_name_key text NOT NULL CHECK (btrim(last_name_key) <> ''),
    status text NOT NULL CHECK (status IN ('PENDING', 'CREATED')),
    client_id bigint REFERENCES public.clients(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT client_operations_trainer_request_key UNIQUE (trainer_id, request_key),
    CONSTRAINT client_operations_result_check CHECK (
        (status = 'CREATED' AND client_id IS NOT NULL)
        OR (status = 'PENDING' AND client_id IS NULL)
    )
);

CREATE UNIQUE INDEX client_operations_unresolved_name_idx
ON public.client_operations (trainer_id, first_name_key, last_name_key)
WHERE status = 'PENDING';

COMMIT;

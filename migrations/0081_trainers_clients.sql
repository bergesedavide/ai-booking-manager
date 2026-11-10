-- M08.1: apply once after HQ approval. Adds only trainers and clients.
-- Normalized name keys must be supplied consistently by the future client service;
-- they are deliberately not UNIQUE: clients with identical names are allowed.
-- trainer_id scopes client access; application queries must always filter by it.
-- updated_at is maintained by the application, not by a database trigger.
BEGIN;

CREATE TABLE public.trainers (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    telegram_user_id bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT trainers_telegram_user_id_key UNIQUE (telegram_user_id)
);

CREATE TABLE public.clients (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trainer_id bigint NOT NULL,
    first_name varchar(100) NOT NULL,
    last_name varchar(100) NOT NULL,
    phone varchar(30),
    email varchar(254),
    notes varchar(2000),
    first_name_key text NOT NULL,
    last_name_key text NOT NULL,
    request_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT clients_trainer_id_fkey
        FOREIGN KEY (trainer_id) REFERENCES public.trainers(id),
    CONSTRAINT clients_first_name_check CHECK (btrim(first_name) <> ''),
    CONSTRAINT clients_last_name_check CHECK (btrim(last_name) <> ''),
    CONSTRAINT clients_first_name_key_check CHECK (btrim(first_name_key) <> ''),
    CONSTRAINT clients_last_name_key_check CHECK (btrim(last_name_key) <> ''),
    CONSTRAINT clients_request_key_check CHECK (btrim(request_key) <> ''),
    CONSTRAINT clients_trainer_request_key UNIQUE (trainer_id, request_key)
);

CREATE INDEX clients_trainer_name_idx
ON public.clients (trainer_id, first_name_key, last_name_key);

COMMIT;

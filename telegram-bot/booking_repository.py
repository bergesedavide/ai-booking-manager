"""Accesso PostgreSQL per il registro delle prenotazioni."""

from enum import Enum

import psycopg


class RegistrationResult(str, Enum):
    CREATED = "CREATED"
    ALREADY_EXISTS = "ALREADY_EXISTS"


def register_booking(
    connection: psycopg.Connection,
    request_key: str,
) -> RegistrationResult:
    """Registra una richiesta, evitando duplicati."""

    if not request_key or not request_key.strip():
        raise ValueError("request_key non può essere vuota")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO booking_operations (
                request_key,
                status
            )
            VALUES (%s, 'PENDING')
            ON CONFLICT (request_key) DO NOTHING
            RETURNING id
            """,
            (request_key,),
        )

        result = cursor.fetchone()

    if result is None:
        return RegistrationResult.ALREADY_EXISTS

    return RegistrationResult.CREATED


def mark_booking_created(
    connection: psycopg.Connection,
    request_key: str,
    google_event_id: str,
) -> bool:
    """Segna una prenotazione come creata su Google Calendar."""

    if not request_key or not request_key.strip():
        raise ValueError("request_key non può essere vuota")

    if not google_event_id or not google_event_id.strip():
        raise ValueError("google_event_id non può essere vuoto")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE booking_operations
            SET
                status = 'CREATED',
                updated_at = NOW()
            WHERE request_key = %s
              AND status = 'PENDING'
              AND google_event_id = %s
            RETURNING id
            """,
            (request_key, google_event_id),
        )

        return cursor.fetchone() is not None


def assign_google_event_id(
    connection: psycopg.Connection,
    request_key: str,
    google_event_id: str,
) -> bool:
    """Associa un ID Google Calendar a una richiesta PENDING."""

    if not request_key or not request_key.strip():
        raise ValueError("request_key non può essere vuota")

    if not google_event_id or not google_event_id.strip():
        raise ValueError("google_event_id non può essere vuoto")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE booking_operations
            SET
                google_event_id = %s,
                updated_at = NOW()
            WHERE request_key = %s
              AND status = 'PENDING'
              AND google_event_id IS NULL
            RETURNING id
            """,
            (google_event_id, request_key),
        )

        return cursor.fetchone() is not None


def get_booking_operation(
    connection: psycopg.Connection,
    request_key: str,
) -> tuple[str, str | None] | None:
    """Legge lo stato e l'ID Google di una richiesta già registrata."""

    if not request_key or not request_key.strip():
        raise ValueError("request_key non può essere vuota")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, google_event_id
            FROM booking_operations
            WHERE request_key = %s
            """,
            (request_key,),
        )

        row = cursor.fetchone()

    if row is None:
        return None

    return row[0], row[1]
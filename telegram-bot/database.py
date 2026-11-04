"""Connessione PostgreSQL per AI Booking Manager."""

import psycopg


def get_connection(password: str) -> psycopg.Connection:
    """Apre una connessione al database PostgreSQL del prototipo."""

    if not password:
        raise ValueError("Password PostgreSQL non configurata")

    return psycopg.connect(
        host="127.0.0.1",
        port=5433,
        dbname="ai_booking_manager",
        user="ai_booking",
        password=password,
        connect_timeout=5,
    )
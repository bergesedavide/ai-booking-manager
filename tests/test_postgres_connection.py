import os
from pathlib import Path

import psycopg


def load_password() -> str:
    import json
    import subprocess

    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        capture_output=True,
        text=True,
        check=True,
    )

    config = json.loads(result.stdout)

    return config["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"]


def main() -> None:
    password = load_password()

    with psycopg.connect(
        host="127.0.0.1",
        port=5433,
        dbname="ai_booking_manager",
        user="ai_booking",
        password=password,
        connect_timeout=5,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_user")
            result = cursor.fetchone()

            print("Connessione PostgreSQL riuscita!")
            print("Database:", result[0])
            print("Utente:", result[1])


if __name__ == "__main__":
    main()
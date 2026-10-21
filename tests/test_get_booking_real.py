import os
import sys
from pathlib import Path

from googleapiclient.discovery import build

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

sys.path.insert(0, str(PROTOTYPE_DIR))

from google_auth import authenticate
from google_calendar import get_booking


def load_test_calendar_id() -> str:
    env_file = PROJECT_ROOT / ".env"

    for line in env_file.read_text().splitlines():
        key, separator, value = line.strip().partition("=")

        if separator and key == "GOOGLE_TEST_CALENDAR_ID":
            return value.strip()

    raise RuntimeError("GOOGLE_TEST_CALENDAR_ID non trovato")


def main():
    if len(sys.argv) != 2:
        print("Utilizzo: python tests/test_get_booking_real.py EVENT_ID")
        return

    event_id = sys.argv[1]
    calendar_id = load_test_calendar_id()

    if not calendar_id or calendar_id == "primary":
        raise ValueError("Calendario di test non valido")

    credentials = authenticate()

    service = build(
        "calendar",
        "v3",
        credentials=credentials,
    )

    event = get_booking(
        service=service,
        calendar_id=calendar_id,
        event_id=event_id,
    )

    print("\nAPPUNTAMENTO RECUPERATO")
    print("ID:", event["id"])
    print("Titolo:", event.get("summary"))
    print("Inizio:", event["start"]["dateTime"])
    print("Fine:", event["end"]["dateTime"])

    print("\nTEST SUPERATO: evento recuperato da Google Calendar.")


if __name__ == "__main__":
    main()
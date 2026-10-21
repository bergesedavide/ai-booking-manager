import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

sys.path.insert(0, str(PROTOTYPE_DIR))

from google_auth import authenticate
from google_calendar import list_events_in_interval


def load_test_calendar_id() -> str:
    env_file = PROJECT_ROOT / ".env"

    for line in env_file.read_text().splitlines():
        key, separator, value = line.strip().partition("=")

        if separator and key == "GOOGLE_TEST_CALENDAR_ID":
            return value.strip().strip('"').strip("'")

    raise RuntimeError("GOOGLE_TEST_CALENDAR_ID non trovato")


def main():
    calendar_id = load_test_calendar_id()

    if not calendar_id or calendar_id == "primary":
        raise ValueError("Calendario di test non valido")

    timezone = ZoneInfo("Europe/Rome")

    # Intervallo che include l'appuntamento M03 già verificato.
    start = datetime(2026, 10, 22, 14, 0, tzinfo=timezone)
    end = datetime(2026, 10, 22, 18, 0, tzinfo=timezone)

    credentials = authenticate()
    service = build("calendar", "v3", credentials=credentials)

    print("\n=== TEST GOOGLE CALENDAR ===")
    print("Intervallo:", start.isoformat(), "-", end.isoformat())

    # 1. Recupero degli eventi.
    events = list_events_in_interval(
        service=service,
        calendar_id=calendar_id,
        start=start,
        end=end,
    )

    print("\n=== EVENTS.LIST ===")

    if not events:
        print("Nessun evento trovato.")

    for event in events:
        print("ID:", event.get("id"))
        print("Titolo:", event.get("summary"))
        print("Inizio:", event.get("start"))
        print("Fine:", event.get("end"))
        print("Stato:", event.get("status"))
        print("Trasparenza:", event.get("transparency", "opaque (default)"))
        print("---")

    # 2. Recupero degli intervalli occupati.
    response = service.freebusy().query(
        body={
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "timeZone": "Europe/Rome",
            "items": [{"id": calendar_id}],
        }
    ).execute()

    calendars = response.get("calendars", {})

    if calendar_id not in calendars:
        raise RuntimeError("Calendario assente dalla risposta FreeBusy")

    calendar_data = calendars[calendar_id]

    if calendar_data.get("errors"):
        raise RuntimeError(
            f"Errore FreeBusy: {calendar_data['errors']}"
        )

    busy_intervals = calendar_data.get("busy")

    if not isinstance(busy_intervals, list):
        raise RuntimeError("Risposta FreeBusy non valida")

    print("\n=== FREEBUSY ===")

    if not busy_intervals:
        print("Nessun intervallo occupato.")

    for interval in busy_intervals:
        print("Occupato da:", interval.get("start"))
        print("Occupato fino a:", interval.get("end"))
        print("---")

    print("\n=== RIEPILOGO ===")
    print("Eventi trovati:", len(events))
    print("Intervalli occupati:", len(busy_intervals))

    print("\nTEST COMPLETATO: sola lettura, nessuna modifica.")


if __name__ == "__main__":
    main()
from datetime import datetime
from pathlib import Path
import sys

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(
    0,
    str(PROJECT_ROOT / "google-calendar-prototype"),
)

from google_auth import authenticate
from google_calendar import get_booking, update_booking


EVENT_ID = "1jadf4sk0nbn8cshdvvlmbbtdk"
EXPECTED_TITLE = "TEST ETAG M04"

ORIGINAL_START = datetime(
    2026, 10, 28, 10, 0,
    tzinfo=ZoneInfo("Europe/Rome"),
)

ORIGINAL_END = datetime(
    2026, 10, 28, 11, 0,
    tzinfo=ZoneInfo("Europe/Rome"),
)

NEW_START = datetime(
    2026, 10, 28, 12, 0,
    tzinfo=ZoneInfo("Europe/Rome"),
)

NEW_END = datetime(
    2026, 10, 28, 13, 0,
    tzinfo=ZoneInfo("Europe/Rome"),
)


def load_calendar_id() -> str:
    config = {}

    for line in (PROJECT_ROOT / ".env").read_text().splitlines():
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        key, separator, value = line.partition("=")

        if separator:
            config[key.strip()] = value.strip()

    calendar_id = config.get("GOOGLE_TEST_CALENDAR_ID")

    if not calendar_id or calendar_id == "primary":
        raise ValueError("Calendario di test non valido")

    return calendar_id


def get_event(service, calendar_id: str) -> dict:
    return get_booking(
        service=service,
        calendar_id=calendar_id,
        event_id=EVENT_ID,
    )


def event_times(event: dict) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(event["start"]["dateTime"])
    end = datetime.fromisoformat(event["end"]["dateTime"])
    return start, end


def main() -> None:
    calendar_id = load_calendar_id()

    service = build(
        "calendar",
        "v3",
        credentials=authenticate(),
    )

    original = get_event(service, calendar_id)
    original_start, original_end = event_times(original)

    print("Evento:", original.get("summary"))
    print("Event ID:", original.get("id"))
    print("ETag:", original.get("etag"))
    print("Inizio:", original_start.isoformat())
    print("Fine:", original_end.isoformat())

    # Controlli di sicurezza prima di qualsiasi PATCH.
    if original.get("id") != EVENT_ID:
        raise ValueError("Event ID inatteso")

    if original.get("summary") != EXPECTED_TITLE:
        raise ValueError("Titolo evento inatteso")

    if (
        original_start != ORIGINAL_START
        or original_end != ORIGINAL_END
    ):
        raise ValueError(
            "L'evento non si trova nell'orario originale previsto"
        )

    if not original.get("etag"):
        raise ValueError("ETag mancante")

    # TEST 1: ETag errato.
    print("\n=== TEST 1: ETag errato ===")

    request = service.events().patch(
        calendarId=calendar_id,
        eventId=EVENT_ID,
        body={
            "start": {
                "dateTime": NEW_START.isoformat(),
                "timeZone": "Europe/Rome",
            },
            "end": {
                "dateTime": NEW_END.isoformat(),
                "timeZone": "Europe/Rome",
            },
        },
    )

    request.headers["If-Match"] = '"etag-volutamente-errato"'

    try:
        request.execute()
    except HttpError as error:
        if error.resp.status != 412:
            raise RuntimeError(
                f"Errore HTTP inatteso: {error.resp.status}"
            ) from error

        print("HTTP 412: richiesta rifiutata correttamente.")
    else:
        raise RuntimeError(
            "Google ha accettato una richiesta con ETag errato!"
        )

    # Verifica che il primo test non abbia modificato l'evento.
    unchanged = get_event(service, calendar_id)
    unchanged_start, unchanged_end = event_times(unchanged)

    if (
        unchanged_start != ORIGINAL_START
        or unchanged_end != ORIGINAL_END
        or unchanged.get("summary") != EXPECTED_TITLE
    ):
        raise RuntimeError(
            "L'evento è cambiato durante il primo test."
        )

    print("Evento invariato dopo il test con ETag errato.")

    # TEST 2: modifica reale tramite update_booking().
    print("\n=== TEST 2: ETag valido ===")
    print("ATTENZIONE: verrà eseguita una modifica reale.")

    updated = update_booking(
        service=service,
        calendar_id=calendar_id,
        event_id=EVENT_ID,
        new_start=NEW_START,
    )

    print("PATCH completata.")
    print("ETag aggiornato:", updated.get("etag"))

    # Recupera nuovamente l'evento da Google Calendar.
    verified = get_event(service, calendar_id)
    verified_start, verified_end = event_times(verified)

    print("\n=== VERIFICA FINALE ===")
    print("Titolo:", verified.get("summary"))
    print("Inizio:", verified_start.isoformat())
    print("Fine:", verified_end.isoformat())

    if verified.get("summary") != EXPECTED_TITLE:
        raise RuntimeError("Il titolo è stato modificato!")

    if verified_start != NEW_START:
        raise RuntimeError("Nuovo orario iniziale errato!")

    if verified_end != NEW_END:
        raise RuntimeError("Nuovo orario finale errato!")

    print("\n✅ ENTRAMBI I TEST SUPERATI")
    print("Titolo preservato.")
    print("Durata preservata.")
    print("Modifica reale verificata.")


if __name__ == "__main__":
    main()
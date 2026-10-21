import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

sys.path.insert(0, str(PROTOTYPE_DIR))

from google_calendar import check_reschedule_availability


TIMEZONE = ZoneInfo("Europe/Rome")


def make_event(event_id, start, end):
    return {
        "id": event_id,
        "status": "confirmed",
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }


def run_test(events, new_hour, new_minute, expected, description):
    service = MagicMock()

    service.events.return_value.list.return_value.execute.return_value = {
        "items": events
    }

    new_start = datetime(
        2026, 10, 26,
        new_hour, new_minute,
        tzinfo=TIMEZONE,
    )

    result = check_reschedule_availability(
        service=service,
        calendar_id="test-calendar-id",
        event_id="mario",
        new_start=new_start,
        duration_minutes=60,
    )

    assert result is expected, (
        f"{description}: atteso {expected}, ottenuto {result}"
    )

    print(f"TEST SUPERATO: {description}")


def main():
    events = [
        make_event(
            "mario",
            "2026-10-26T15:00:00+01:00",
            "2026-10-26T16:00:00+01:00",
        ),
        make_event(
            "luca",
            "2026-10-26T17:00:00+01:00",
            "2026-10-26T18:00:00+01:00",
        ),
    ]

    # L'appuntamento originale non deve bloccare se stesso.
    run_test(
        events, 15, 30, True,
        "esclusione dell'evento originale",
    )

    # La sovrapposizione con Luca deve essere rilevata.
    run_test(
        events, 17, 30, False,
        "sovrapposizione con un altro evento",
    )

    # Due appuntamenti consecutivi sono consentiti.
    run_test(
        events, 16, 0, True,
        "appuntamenti consecutivi",
    )

    # Nessun evento presente.
    run_test(
        [], 19, 0, True,
        "intervallo senza eventi",
    )

    # Un evento senza orario esplicito blocca l'intervallo.
    all_day_event = {
        "id": "giornata-intera",
        "status": "confirmed",
        "start": {"date": "2026-10-26"},
        "end": {"date": "2026-10-27"},
    }

    run_test(
        [all_day_event], 15, 0, False,
        "evento di un'intera giornata",
    )


if __name__ == "__main__":
    main()
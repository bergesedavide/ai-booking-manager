import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

sys.path.insert(0, str(PROTOTYPE_DIR))

from google_calendar import (
    AvailabilityStatus,
    check_reschedule_availability_safe,
)


CALENDAR_ID = "test-calendar-id"
EVENT_ID = "mario"

TIMEZONE = ZoneInfo("Europe/Rome")

NEW_START = datetime(
    2026, 10, 22, 15, 0,
    tzinfo=TIMEZONE,
)


def make_event(event_id, start, end):
    return {
        "id": event_id,
        "status": "confirmed",
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }


ORIGINAL_EVENT = make_event(
    "mario",
    "2026-10-22T15:00:00+02:00",
    "2026-10-22T16:00:00+02:00",
)


def make_service(events, busy, original=None):
    service = MagicMock()

    service.events.return_value.get.return_value.execute.return_value = (
        original if original is not None else ORIGINAL_EVENT
    )

    service.events.return_value.list.return_value.execute.return_value = {
        "items": events
    }

    service.freebusy.return_value.query.return_value.execute.return_value = {
        "calendars": {
            CALENDAR_ID: {
                "busy": busy
            }
        }
    }

    return service


def check_result(service, expected, description):
    result = check_reschedule_availability_safe(
        service=service,
        calendar_id=CALENDAR_ID,
        event_id=EVENT_ID,
        new_start=NEW_START,
        duration_minutes=60,
    )

    assert result == expected, (
        f"{description}: atteso {expected}, ottenuto {result}"
    )

    print(f"TEST SUPERATO: {description}")


def test_available():
    service = make_service(
        events=[],
        busy=[],
    )

    check_result(
        service,
        AvailabilityStatus.AVAILABLE,
        "intervallo libero",
    )


def test_original_event_only():
    service = make_service(
        events=[ORIGINAL_EVENT],
        busy=[
            {
                "start": "2026-10-22T15:00:00+02:00",
                "end": "2026-10-22T16:00:00+02:00",
            }
        ],
    )

    check_result(
        service,
        AvailabilityStatus.AVAILABLE,
        "evento originale escluso",
    )


def test_conflict():
    other_event = make_event(
        "luca",
        "2026-10-22T15:30:00+02:00",
        "2026-10-22T16:30:00+02:00",
    )

    service = make_service(
        events=[ORIGINAL_EVENT, other_event],
        busy=[
            {
                "start": "2026-10-22T15:00:00+02:00",
                "end": "2026-10-22T16:00:00+02:00",
            }
        ],
    )

    check_result(
        service,
        AvailabilityStatus.CONFLICT,
        "sovrapposizione con altro evento",
    )


def test_unknown():
    service = make_service(
        events=[],
        busy=[
            {
                "start": "2026-10-22T15:00:00+02:00",
                "end": "2026-10-22T16:00:00+02:00",
            }
        ],
    )

    check_result(
        service,
        AvailabilityStatus.UNKNOWN,
        "occupazione FreeBusy non spiegata",
    )


def test_google_error():
    service = make_service([], [])

    service.events.return_value.list.return_value.execute.side_effect = (
        RuntimeError("Errore Google")
    )

    check_result(
        service,
        AvailabilityStatus.UNKNOWN,
        "errore Google",
    )


def test_inconsistent_busy_intervals():
    short_event = make_event(
        "luca",
        "2026-10-22T15:00:00+02:00",
        "2026-10-22T15:30:00+02:00",
    )

    service = make_service(
        events=[short_event],
        busy=[
            {
                "start": "2026-10-22T15:00:00+02:00",
                "end": "2026-10-22T16:00:00+02:00",
            }
        ],
    )

    check_result(
        service,
        AvailabilityStatus.UNKNOWN,
        "intervalli Events e FreeBusy incoerenti",
    )


def test_original_duration_validation():
    service = make_service(
        events=[],
        busy=[],
    )

    # Caso 1: durata corretta (60 minuti)
    result = check_reschedule_availability_safe(
        service=service,
        calendar_id=CALENDAR_ID,
        event_id=EVENT_ID,
        new_start=NEW_START,
        duration_minutes=60,
    )

    assert result == AvailabilityStatus.AVAILABLE
    print("TEST SUPERATO: durata originale corretta")

    # Caso 2: durata troppo corta (30 minuti)
    result = check_reschedule_availability_safe(
        service=service,
        calendar_id=CALENDAR_ID,
        event_id=EVENT_ID,
        new_start=NEW_START,
        duration_minutes=30,
    )

    assert result == AvailabilityStatus.UNKNOWN
    print("TEST SUPERATO: durata troppo corta bloccata")

    # Caso 3: durata troppo lunga (90 minuti)
    result = check_reschedule_availability_safe(
        service=service,
        calendar_id=CALENDAR_ID,
        event_id=EVENT_ID,
        new_start=NEW_START,
        duration_minutes=90,
    )

    assert result == AvailabilityStatus.UNKNOWN
    print("TEST SUPERATO: durata troppo lunga bloccata")


def test_duplicate_original_event_id():
    duplicate_event = make_event(
        EVENT_ID,
        "2026-10-22T15:30:00+02:00",
        "2026-10-22T16:30:00+02:00",
    )

    service = make_service(
        events=[ORIGINAL_EVENT, duplicate_event],
        busy=[
            {
                "start": "2026-10-22T15:00:00+02:00",
                "end": "2026-10-22T16:00:00+02:00",
            }
        ],
    )

    check_result(
        service,
        AvailabilityStatus.UNKNOWN,
        "identificativo evento originale duplicato",
    )


def test_primary_calendar_blocked():
    service = make_service([], [])

    try:
        check_reschedule_availability_safe(
            service=service,
            calendar_id="primary",
            event_id=EVENT_ID,
            new_start=NEW_START,
            duration_minutes=60,
        )
    except ValueError:
        print("TEST SUPERATO: calendario primary bloccato")
    else:
        raise AssertionError(
            "Il calendario primary non è stato bloccato"
        )

    service.events.assert_not_called()
    service.freebusy.assert_not_called()


if __name__ == "__main__":
    test_available()
    test_original_event_only()
    test_conflict()
    test_unknown()
    test_google_error()
    test_inconsistent_busy_intervals()
    test_original_duration_validation()
    test_duplicate_original_event_id()
    test_primary_calendar_blocked()
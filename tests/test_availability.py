import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(
    0,
    str(PROJECT_ROOT / "google-calendar-prototype"),
)

from google_calendar import check_availability


CALENDAR_ID = "test-calendar@group.calendar.google.com"
START = datetime(
    2026, 10, 23, 15, 0,
    tzinfo=ZoneInfo("Europe/Rome"),
)


def mock_service(response: dict) -> MagicMock:
    service = MagicMock()
    service.freebusy.return_value.query.return_value.execute.return_value = response
    return service


def test_free() -> None:
    service = mock_service({
        "calendars": {
            CALENDAR_ID: {"busy": []}
        }
    })

    result = check_availability(service, CALENDAR_ID, START, 60)

    assert result is True
    print("TEST SUPERATO: intervallo libero.")


def test_busy() -> None:
    service = mock_service({
        "calendars": {
            CALENDAR_ID: {
                "busy": [{
                    "start": "2026-10-23T15:30:00+02:00",
                    "end": "2026-10-23T16:30:00+02:00",
                }]
            }
        }
    })

    result = check_availability(service, CALENDAR_ID, START, 60)

    assert result is False
    print("TEST SUPERATO: sovrapposizione rilevata.")


def test_google_error() -> None:
    service = mock_service({
        "calendars": {
            CALENDAR_ID: {
                "errors": [{"reason": "internalError"}],
                "busy": [],
            }
        }
    })

    try:
        check_availability(service, CALENDAR_ID, START, 60)
    except RuntimeError:
        print("TEST SUPERATO: errore Google gestito.")
    else:
        raise AssertionError("Errore Google non rilevato")


def test_missing_calendar() -> None:
    service = mock_service({"calendars": {}})

    try:
        check_availability(service, CALENDAR_ID, START, 60)
    except RuntimeError:
        print("TEST SUPERATO: calendario mancante rilevato.")
    else:
        raise AssertionError("Calendario mancante non rilevato")


def test_primary_blocked() -> None:
    service = MagicMock()

    try:
        check_availability(service, "primary", START, 60)
    except ValueError:
        service.freebusy.assert_not_called()
        print("TEST SUPERATO: calendario primary bloccato.")
    else:
        raise AssertionError("Calendario primary non bloccato")


if __name__ == "__main__":
    test_free()
    test_busy()
    test_google_error()
    test_missing_calendar()
    test_primary_blocked()
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

sys.path.insert(0, str(PROTOTYPE_DIR))

from google_calendar import get_freebusy_intervals


TIMEZONE = ZoneInfo("Europe/Rome")
CALENDAR_ID = "test-calendar-id"

START = datetime(2026, 10, 22, 14, 0, tzinfo=TIMEZONE)
END = datetime(2026, 10, 22, 18, 0, tzinfo=TIMEZONE)


def make_service(response):
    service = MagicMock()

    service.freebusy.return_value.query.return_value.execute.return_value = (
        response
    )

    return service


def test_valid_intervals():
    service = make_service({
        "calendars": {
            CALENDAR_ID: {
                "busy": [
                    {
                        "start": "2026-10-22T15:00:00+02:00",
                        "end": "2026-10-22T16:30:00+02:00",
                    }
                ]
            }
        }
    })

    intervals = get_freebusy_intervals(
        service, CALENDAR_ID, START, END
    )

    assert len(intervals) == 1

    busy_start, busy_end = intervals[0]

    assert busy_start.hour == 15
    assert busy_end.hour == 16
    assert busy_end.minute == 30

    service.freebusy().query.assert_called_once()

    print("TEST SUPERATO: intervallo occupato recuperato")


def test_empty_intervals():
    service = make_service({
        "calendars": {
            CALENDAR_ID: {
                "busy": []
            }
        }
    })

    intervals = get_freebusy_intervals(
        service, CALENDAR_ID, START, END
    )

    assert intervals == []

    print("TEST SUPERATO: nessun intervallo occupato")


def test_google_error():
    service = make_service({
        "calendars": {
            CALENDAR_ID: {
                "errors": [{"reason": "internalError"}],
                "busy": [],
            }
        }
    })

    try:
        get_freebusy_intervals(
            service, CALENDAR_ID, START, END
        )
    except RuntimeError:
        print("TEST SUPERATO: errore Google rilevato")
    else:
        raise AssertionError("Errore Google non rilevato")


def test_missing_busy():
    service = make_service({
        "calendars": {
            CALENDAR_ID: {}
        }
    })

    try:
        get_freebusy_intervals(
            service, CALENDAR_ID, START, END
        )
    except RuntimeError:
        print("TEST SUPERATO: risposta incompleta bloccata")
    else:
        raise AssertionError("Risposta incompleta accettata")


def test_primary_blocked():
    service = MagicMock()

    try:
        get_freebusy_intervals(
            service, "primary", START, END
        )
    except ValueError:
        print("TEST SUPERATO: calendario primary bloccato")
    else:
        raise AssertionError("Calendario primary accettato")

    service.freebusy().query.assert_not_called()


def main():
    test_valid_intervals()
    test_empty_intervals()
    test_google_error()
    test_missing_busy()
    test_primary_blocked()


if __name__ == "__main__":
    main()
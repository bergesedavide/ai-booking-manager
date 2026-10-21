import sys
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

sys.path.insert(0, str(PROTOTYPE_DIR))

from google_calendar import get_booking


def test_get_booking():
    service = MagicMock()

    event = {
        "id": "test-event-123",
        "summary": "Allenamento Mario",
        "status": "confirmed",
        "start": {
            "dateTime": "2026-10-25T15:00:00+01:00"
        },
        "end": {
            "dateTime": "2026-10-25T16:00:00+01:00"
        },
    }

    service.events.return_value.get.return_value.execute.return_value = event

    result = get_booking(
        service=service,
        calendar_id="test-calendar-id",
        event_id="test-event-123",
    )

    assert result == event

    service.events().get.assert_called_once_with(
        calendarId="test-calendar-id",
        eventId="test-event-123",
    )

    print("TEST SUPERATO: appuntamento recuperato correttamente.")


def test_primary_blocked():
    service = MagicMock()

    try:
        get_booking(
            service=service,
            calendar_id="primary",
            event_id="test-event-123",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Il calendario primary non è stato bloccato")

    service.events().get.assert_not_called()

    print("TEST SUPERATO: calendario primary bloccato.")


def test_recurring_event_blocked():
    service = MagicMock()

    service.events().get().execute.return_value = {
        "id": "recurring-event",
        "recurrence": ["RRULE:FREQ=WEEKLY"],
        "start": {
            "dateTime": "2026-10-26T15:00:00+01:00"
        },
        "end": {
            "dateTime": "2026-10-26T16:00:00+01:00"
        },
    }

    try:
        get_booking(
            service=service,
            calendar_id="test-calendar-id",
            event_id="recurring-event",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("L'evento ricorrente non è stato bloccato")

    print("TEST SUPERATO: evento ricorrente bloccato.")


if __name__ == "__main__":
    test_get_booking()
    test_primary_blocked()
    test_recurring_event_blocked()
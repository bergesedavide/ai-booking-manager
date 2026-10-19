import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

sys.path.insert(0, str(PROTOTYPE_DIR))

from google_calendar import create_booking


def test_create_booking() -> None:
    service = MagicMock()

    service.events.return_value.insert.return_value.execute.return_value = {
        "id": "test-event-123",
        "summary": "Allenamento Mario",
        "status": "confirmed",
    }

    start = datetime(
        2026, 10, 23, 15, 0,
        tzinfo=ZoneInfo("Europe/Rome"),
    )

    result = create_booking(
        service=service,
        calendar_id="test-calendar@group.calendar.google.com",
        title="Allenamento Mario",
        start=start,
        duration_minutes=60,
    )

    assert result["id"] == "test-event-123"

    call = service.events.return_value.insert.call_args

    assert call.kwargs["calendarId"] == (
        "test-calendar@group.calendar.google.com"
    )

    event = call.kwargs["body"]

    assert event["summary"] == "Allenamento Mario"
    assert event["start"]["dateTime"] == start.isoformat()

    expected_end = datetime(
        2026, 10, 23, 16, 0,
        tzinfo=ZoneInfo("Europe/Rome"),
    )

    assert event["end"]["dateTime"] == expected_end.isoformat()

    print("TEST SUPERATO: creazione appuntamento corretta.")


def test_primary_calendar_blocked() -> None:
    service = MagicMock()

    try:
        create_booking(
            service=service,
            calendar_id="primary",
            title="Test",
            start=datetime.now(ZoneInfo("Europe/Rome")),
            duration_minutes=60,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Il calendario primary non è stato bloccato")

    service.events.return_value.insert.assert_not_called()

    print("TEST SUPERATO: calendario primary bloccato.")


if __name__ == "__main__":
    test_create_booking()
    test_primary_calendar_blocked()
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

sys.path.insert(0, str(PROTOTYPE_DIR))

from google_calendar import update_booking


def test_update_booking():
    service = MagicMock()

    original_event = {
        "id": "test-event-123",
        "summary": "Allenamento Mario",
        "description": "Sessione personal training",
        "status": "confirmed",
        "start": {
            "dateTime": "2026-10-26T15:00:00+01:00"
        },
        "end": {
            "dateTime": "2026-10-26T16:00:00+01:00"
        },
    }

    service.events.return_value.get.return_value.execute.return_value = (
        original_event
    )

    service.events.return_value.patch.return_value.execute.return_value = {
        **original_event,
        "start": {
            "dateTime": "2026-10-27T17:00:00+01:00"
        },
        "end": {
            "dateTime": "2026-10-27T18:00:00+01:00"
        },
    }

    new_start = datetime(
        2026, 10, 27, 17, 0,
        tzinfo=ZoneInfo("Europe/Rome"),
    )

    result = update_booking(
        service=service,
        calendar_id="test-calendar-id",
        event_id="test-event-123",
        new_start=new_start,
    )

    service.events().patch.assert_called_once()

    kwargs = service.events().patch.call_args.kwargs

    assert kwargs["calendarId"] == "test-calendar-id"
    assert kwargs["eventId"] == "test-event-123"

    body = kwargs["body"]

    assert body["start"]["dateTime"] == "2026-10-27T17:00:00+01:00"
    assert body["end"]["dateTime"] == "2026-10-27T18:00:00+01:00"

    # Il PATCH non deve modificare titolo o descrizione.
    assert "summary" not in body
    assert "description" not in body

    assert result["id"] == "test-event-123"

    print("TEST SUPERATO: modifica appuntamento corretta.")


if __name__ == "__main__":
    test_update_booking()
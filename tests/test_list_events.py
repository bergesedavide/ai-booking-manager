import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

sys.path.insert(0, str(PROTOTYPE_DIR))

from google_calendar import list_events_in_interval


def test_list_events():
    service = MagicMock()

    first_page = {
        "items": [
            {"id": "event-1", "summary": "Allenamento Mario"}
        ],
        "nextPageToken": "page-2",
    }

    second_page = {
        "items": [
            {"id": "event-2", "summary": "Allenamento Luca"}
        ],
    }

    service.events.return_value.list.return_value.execute.side_effect = [
        first_page,
        second_page,
    ]

    timezone = ZoneInfo("Europe/Rome")

    start = datetime(2026, 10, 26, 15, 0, tzinfo=timezone)
    end = datetime(2026, 10, 26, 19, 0, tzinfo=timezone)

    events = list_events_in_interval(
        service=service,
        calendar_id="test-calendar-id",
        start=start,
        end=end,
    )

    assert len(events) == 2
    assert events[0]["id"] == "event-1"
    assert events[1]["id"] == "event-2"

    assert service.events().list.call_count == 2

    first_call = service.events().list.call_args_list[0].kwargs
    second_call = service.events().list.call_args_list[1].kwargs

    assert first_call["calendarId"] == "test-calendar-id"
    assert first_call["singleEvents"] is True
    assert first_call["showDeleted"] is False
    assert first_call["pageToken"] is None

    assert second_call["pageToken"] == "page-2"

    print("TEST SUPERATO: eventi recuperati con paginazione.")


def test_primary_blocked():
    service = MagicMock()
    timezone = ZoneInfo("Europe/Rome")

    start = datetime(2026, 10, 26, 15, 0, tzinfo=timezone)
    end = datetime(2026, 10, 26, 16, 0, tzinfo=timezone)

    try:
        list_events_in_interval(
            service=service,
            calendar_id="primary",
            start=start,
            end=end,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Il calendario primary non è stato bloccato")

    service.events().list.assert_not_called()

    print("TEST SUPERATO: calendario primary bloccato.")


if __name__ == "__main__":
    test_list_events()
    test_primary_blocked()
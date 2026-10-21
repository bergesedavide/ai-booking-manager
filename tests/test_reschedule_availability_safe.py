import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

sys.path.insert(0, str(PROTOTYPE_DIR))

from google_calendar import (
    AvailabilityStatus,
    check_reschedule_availability_safe,
)


def run_test(mock_result, expected_status, description):
    service = MagicMock()

    new_start = datetime(
        2026, 10, 26, 15, 0,
        tzinfo=ZoneInfo("Europe/Rome"),
    )

    with patch(
        "google_calendar.check_reschedule_availability",
        return_value=mock_result,
    ):
        result = check_reschedule_availability_safe(
            service=service,
            calendar_id="test-calendar-id",
            event_id="mario",
            new_start=new_start,
            duration_minutes=60,
        )

    assert result == expected_status, (
        f"{description}: atteso {expected_status}, ottenuto {result}"
    )

    print(f"TEST SUPERATO: {description}")


def test_google_error():
    service = MagicMock()

    new_start = datetime(
        2026, 10, 26, 15, 0,
        tzinfo=ZoneInfo("Europe/Rome"),
    )

    with patch(
        "google_calendar.check_reschedule_availability",
        side_effect=RuntimeError("Errore Google Calendar"),
    ):
        result = check_reschedule_availability_safe(
            service=service,
            calendar_id="test-calendar-id",
            event_id="mario",
            new_start=new_start,
            duration_minutes=60,
        )

    assert result == AvailabilityStatus.UNKNOWN

    print("TEST SUPERATO: errore Google gestito come UNKNOWN")


def test_invalid_calendar():
    service = MagicMock()

    new_start = datetime(
        2026, 10, 26, 15, 0,
        tzinfo=ZoneInfo("Europe/Rome"),
    )

    with patch(
        "google_calendar.check_reschedule_availability",
        side_effect=ValueError("Calendar ID di test non valido"),
    ):
        try:
            check_reschedule_availability_safe(
                service=service,
                calendar_id="primary",
                event_id="mario",
                new_start=new_start,
                duration_minutes=60,
            )
        except ValueError:
            print("TEST SUPERATO: errore di validazione mantenuto")
        else:
            raise AssertionError(
                "L'errore di validazione non è stato propagato"
            )


def main():
    run_test(
        False,
        AvailabilityStatus.CONFLICT,
        "sovrapposizione rilevata",
    )

    run_test(
        True,
        AvailabilityStatus.UNKNOWN,
        "assenza di conflitti non ancora verificata con FreeBusy",
    )

    test_google_error()
    test_invalid_calendar()


if __name__ == "__main__":
    main()
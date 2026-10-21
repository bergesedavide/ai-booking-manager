import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

sys.path.insert(0, str(PROTOTYPE_DIR))

from google_calendar import extract_event_intervals


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def test_normal_event():
    events = [
        {
            "id": "mario",
            "status": "confirmed",
            "start": {"dateTime": "2026-10-22T15:00:00+02:00"},
            "end": {"dateTime": "2026-10-22T16:00:00+02:00"},
        }
    ]

    result = extract_event_intervals(events)

    assert result == [
        (
            dt("2026-10-22T13:00:00+00:00"),
            dt("2026-10-22T14:00:00+00:00"),
        )
    ]

    print("TEST SUPERATO: evento ordinario")


def test_transparent_event():
    events = [
        {
            "id": "trasparente",
            "transparency": "transparent",
            "start": {"dateTime": "2026-10-22T15:00:00+02:00"},
            "end": {"dateTime": "2026-10-22T16:00:00+02:00"},
        }
    ]

    assert extract_event_intervals(events) == []

    print("TEST SUPERATO: evento trasparente ignorato")


def test_cancelled_event():
    events = [
        {
            "id": "cancellato",
            "status": "cancelled",
            "start": {"dateTime": "2026-10-22T15:00:00+02:00"},
            "end": {"dateTime": "2026-10-22T16:00:00+02:00"},
        }
    ]

    assert extract_event_intervals(events) == []

    print("TEST SUPERATO: evento cancellato ignorato")


def test_all_day_event():
    events = [
        {
            "id": "giornata-intera",
            "start": {"date": "2026-10-26"},
            "end": {"date": "2026-10-27"},
        }
    ]

    result = extract_event_intervals(events)

    assert result == [
        (
            dt("2026-10-25T23:00:00+00:00"),
            dt("2026-10-26T23:00:00+00:00"),
        )
    ]

    print("TEST SUPERATO: evento di un'intera giornata")


def test_invalid_event():
    events = [
        {
            "id": "incompleto",
            "start": {"dateTime": "2026-10-22T15:00:00+02:00"},
        }
    ]

    try:
        extract_event_intervals(events)
    except ValueError:
        print("TEST SUPERATO: evento incompleto bloccato")
    else:
        raise AssertionError("Evento incompleto accettato")


def test_invalid_duration():
    events = [
        {
            "id": "durata-errata",
            "start": {"dateTime": "2026-10-22T16:00:00+02:00"},
            "end": {"dateTime": "2026-10-22T15:00:00+02:00"},
        }
    ]

    try:
        extract_event_intervals(events)
    except ValueError:
        print("TEST SUPERATO: durata non valida bloccata")
    else:
        raise AssertionError("Durata non valida accettata")


def test_empty_list():
    assert extract_event_intervals([]) == []

    print("TEST SUPERATO: lista vuota")


def main():
    test_normal_event()
    test_transparent_event()
    test_cancelled_event()
    test_all_day_event()
    test_invalid_event()
    test_invalid_duration()
    test_empty_list()


if __name__ == "__main__":
    main()
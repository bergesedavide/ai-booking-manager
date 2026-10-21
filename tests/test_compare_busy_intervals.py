import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_DIR = PROJECT_ROOT / "google-calendar-prototype"

sys.path.insert(0, str(PROTOTYPE_DIR))

from google_calendar import compare_busy_intervals


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def test_overlapping_events():
    events = [
        (
            dt("2026-10-22T15:00:00+02:00"),
            dt("2026-10-22T16:00:00+02:00"),
        ),
        (
            dt("2026-10-22T15:30:00+02:00"),
            dt("2026-10-22T16:30:00+02:00"),
        ),
    ]

    freebusy = [
        (
            dt("2026-10-22T15:00:00+02:00"),
            dt("2026-10-22T16:30:00+02:00"),
        ),
    ]

    assert compare_busy_intervals(events, freebusy) is True

    print("TEST SUPERATO: eventi sovrapposti")


def test_different_timezones():
    events = [
        (
            dt("2026-10-22T15:00:00+02:00"),
            dt("2026-10-22T16:00:00+02:00"),
        ),
    ]

    freebusy = [
        (
            dt("2026-10-22T13:00:00+00:00"),
            dt("2026-10-22T14:00:00+00:00"),
        ),
    ]

    assert compare_busy_intervals(events, freebusy) is True

    print("TEST SUPERATO: fusi orari differenti")


def test_unexplained_busy_interval():
    events = [
        (
            dt("2026-10-22T15:00:00+02:00"),
            dt("2026-10-22T16:00:00+02:00"),
        ),
    ]

    freebusy = [
        (
            dt("2026-10-22T15:00:00+02:00"),
            dt("2026-10-22T16:30:00+02:00"),
        ),
    ]

    assert compare_busy_intervals(events, freebusy) is False

    print("TEST SUPERATO: occupazione non spiegata")


def test_empty_intervals():
    assert compare_busy_intervals([], []) is True

    print("TEST SUPERATO: intervalli vuoti")


def test_invalid_interval():
    events = [
        (
            dt("2026-10-22T16:00:00+02:00"),
            dt("2026-10-22T15:00:00+02:00"),
        ),
    ]

    try:
        compare_busy_intervals(events, [])
    except ValueError:
        print("TEST SUPERATO: intervallo non valido")
    else:
        raise AssertionError("Intervallo non valido accettato")


def main():
    test_overlapping_events()
    test_different_timezones()
    test_unexplained_busy_interval()
    test_empty_intervals()
    test_invalid_interval()


if __name__ == "__main__":
    main()